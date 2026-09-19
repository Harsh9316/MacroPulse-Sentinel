"""
MacroPulse Sentinel — Main Orchestrator

Wires all async workers together via a central asyncio.Queue event bus.
Implements the full pipeline:
  Ingestion → Dedup → LLM Eval → Risk Calc → Telegram Alert

Also manages:
  - Daily P&L circuit breaker
  - Graceful shutdown (SIGTERM / SIGINT)
  - Worker health monitoring
"""
from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime, timezone
from typing import Any

from macropulse.config import settings
from macropulse.dashboard.server import dashboard, start_dashboard_server
from macropulse.dedup import dedup_cache
from macropulse.feeds.calendar import EconomicCalendarPoller
from macropulse.feeds.price_feed import price_feed
from macropulse.feeds.rss import RSSHeadlineMonitor
from macropulse.feeds.twitter_monitor import TwitterMonitor
from macropulse.llm.client import LLMProviderError, LLMSentinelClient
from macropulse.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from macropulse.logger import get_logger
from macropulse.models import DailyPnL, EconomicEvent, NewsHeadline, TradeSignal
from macropulse.risk.calculator import RiskCalculator
from macropulse.telegram.bot import TelegramAlerter
from macropulse.telegram.formatter import (
    format_circuit_breaker_message,
    format_signal_dict,
    format_startup_message,
)

log = get_logger(__name__)


class Sentinel:
    """
    Central orchestrator for the MacroPulse Sentinel application.
    """

    def __init__(self) -> None:
        # Shared event bus
        self._queue: asyncio.Queue[EconomicEvent | NewsHeadline] = asyncio.Queue(maxsize=200)

        # Core components
        self._llm = LLMSentinelClient()
        self._risk = RiskCalculator()
        self._telegram = TelegramAlerter()

        # Workers
        self._calendar_poller = EconomicCalendarPoller(self._queue)
        self._rss_monitor = RSSHeadlineMonitor(self._queue)
        self._twitter_monitor = TwitterMonitor(self._queue)

        # Daily P&L state
        self._daily_pnl = DailyPnL(date=str(date.today()))

        # Shutdown flag
        self._shutdown_event = asyncio.Event()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        log.info("sentinel.starting")

        # Init shared services
        await dedup_cache.start()
        await price_feed.start()
        await self._telegram.start()

        # Send startup notification
        provider_label = self._llm._providers[0][0] if self._llm._providers else "unknown"
        startup_msg = format_startup_message(
            provider=f"{provider_label}/{self._llm._providers[0][1]}",
            mode=settings.price_feed_provider,
            dry_run=settings.dry_run,
        )
        await self._telegram.send_html(startup_msg)

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_shutdown)

        # Initialise dashboard system info
        provider_label = self._llm._providers[0][0] if self._llm._providers else "unknown"
        model_label    = self._llm._providers[0][1] if self._llm._providers else "—"
        await dashboard.update_system(
            status="running",
            llm_provider=f"{provider_label}/{model_label}",
        )

        log.info("sentinel.started", dry_run=settings.dry_run)

        # Run all workers concurrently
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(start_dashboard_server(), name="dashboard_server")
                tg.create_task(self._price_broadcast_loop(), name="price_broadcaster")
                tg.create_task(self._calendar_poller.start(), name="calendar_poller")
                tg.create_task(self._rss_monitor.start(), name="rss_monitor")
                tg.create_task(self._twitter_monitor.start(), name="twitter_monitor")
                tg.create_task(self._process_events(), name="event_processor")
                tg.create_task(dedup_cache.run_cleanup_loop(), name="dedup_cleanup")
                tg.create_task(self._midnight_reset_loop(), name="midnight_reset")
        except* asyncio.CancelledError:
            log.info("sentinel.shutdown_complete")

    async def stop(self) -> None:
        log.info("sentinel.stopping")
        await self._telegram.stop()
        await dedup_cache.stop()
        await price_feed.stop()

    def _handle_shutdown(self) -> None:
        log.info("sentinel.shutdown_signal_received")
        self._shutdown_event.set()
        # Cancel all tasks
        for task in asyncio.all_tasks():
            task.cancel()

    # ── Event Processing Loop ────────────────────────────────────────────

    async def _price_broadcast_loop(self) -> None:
        """Push live prices to the dashboard every 2 seconds."""
        while not self._shutdown_event.is_set():
            await asyncio.sleep(2)
            for sym in ("XAU/USD", "NAS100", "USD/JPY"):
                p = price_feed.get_price(sym)
                if p:
                    await dashboard.push_price(sym, p.bid, p.ask)

    async def _process_events(self) -> None:
        """Main consumer loop: dequeue events and process through LLM pipeline."""
        log.info("event_processor.started")
        await dashboard.update_feed_status("calendar", "active")
        await dashboard.update_feed_status("rss", "active")
        await dashboard.update_feed_status("twitter",
            "active" if settings.twitter_mode != "disabled" else "disabled")
        while not self._shutdown_event.is_set():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                self._check_daily_reset()
                continue
            except asyncio.CancelledError:
                break

            asyncio.create_task(self._handle_event(event))
            self._queue.task_done()

    async def _handle_event(self, event: EconomicEvent | NewsHeadline) -> None:
        """
        Full pipeline for a single event:
        1. Extract text and source metadata
        2. Get current prices
        3. Call LLM for sentiment analysis
        4. Enrich with risk calculations
        5. Push to Telegram
        """
        # ── Circuit breaker check ─────────────────────────────────────────
        if not self._daily_pnl.can_trade(settings.max_daily_loss):
            log.warning("event_processor.circuit_breaker_active")
            return

        # ── Extract event data ────────────────────────────────────────────
        if isinstance(event, EconomicEvent):
            event_text = (
                f"{event.event_name} | {event.currency}\n"
                f"Actual: {event.actual} | Forecast: {event.forecast} | Previous: {event.previous}"
            )
            source_name = f"Economic Calendar ({event.source})"
            twitter_handle = None
            event_id = event.event_id
        else:  # NewsHeadline
            event_text = f"{event.title}\n{event.summary or ''}"
            source_name = event.source_name
            twitter_handle = event.twitter_handle
            event_id = event.headline_id

        # ── Collect current prices ────────────────────────────────────────
        prices: dict[str, float] = {}
        for sym in ("XAU/USD", "NAS100", "USD/JPY"):
            mid = price_feed.get_mid_price(sym)
            if mid:
                prices[sym] = mid

        if not prices:
            log.warning("event_processor.no_prices_available")
            # Use placeholder prices in mock mode
            prices = {"XAU/USD": 2340.0, "NAS100": 19850.0, "USD/JPY": 151.5}

        # ── LLM Evaluation ───────────────────────────────────────────────
        user_prompt = build_user_prompt(
            event_data=event_text,
            source_name=source_name,
            current_prices=prices,
            twitter_handle=twitter_handle,
        )

        try:
            signal = await self._llm.evaluate(SYSTEM_PROMPT, user_prompt)
        except LLMProviderError as exc:
            log.error("event_processor.llm_failed", error=str(exc), event=event_text[:80])
            return
        except Exception as exc:
            log.error("event_processor.llm_unexpected", error=str(exc))
            return

        # Skip LOW-impact signals to reduce noise
        if signal.impact_rating.value == "LOW":
            log.info("event_processor.low_impact_skipped", event=signal.event_title)
            return

        # ── Risk enrichment ───────────────────────────────────────────────
        signal_dict = signal.model_dump()
        signal_dict = self._risk.enrich_signal(signal_dict, prices)

        # Add metadata
        signal_dict["triggered_by_id"] = event_id
        signal_dict["triggered_by_source"] = source_name

        # ── Format and send alert ────────────────────────────────────────
        message = format_signal_dict(signal_dict, triggered_by=source_name)
        await self._telegram.send_html(message)

        # ── Push to dashboard ────────────────────────────────────────────
        await dashboard.push_signal(signal_dict)
        await dashboard.push_event({
            "title": signal_dict.get("event_title", "—"),
            "source": source_name,
            "impact": signal_dict.get("impact_rating", "—"),
        })
        await dashboard.update_system(
            signals_today=self._daily_pnl.signals_fired + 1,
            daily_loss=self._daily_pnl.realized_loss,
            circuit_breaker=self._daily_pnl.circuit_breaker_triggered,
        )

        # Track signal count
        self._daily_pnl.signals_fired += 1
        log.info(
            "event_processor.signal_dispatched",
            event=signal.event_title,
            impact=signal.impact_rating,
            signals_today=self._daily_pnl.signals_fired,
        )

    # ── Daily Reset & Circuit Breaker ────────────────────────────────────

    def _check_daily_reset(self) -> None:
        """Reset daily P&L at midnight UTC."""
        today = str(date.today())
        if self._daily_pnl.date != today:
            log.info("sentinel.daily_reset", prev_date=self._daily_pnl.date)
            self._daily_pnl = DailyPnL(date=today)

    async def _midnight_reset_loop(self) -> None:
        """Periodic check for midnight reset and circuit breaker status."""
        while not self._shutdown_event.is_set():
            await asyncio.sleep(60)
            self._check_daily_reset()

            if (
                not self._daily_pnl.circuit_breaker_triggered
                and self._daily_pnl.realized_loss >= settings.max_daily_loss
            ):
                self._daily_pnl.circuit_breaker_triggered = True
                log.warning(
                    "sentinel.circuit_breaker_triggered",
                    daily_loss=self._daily_pnl.realized_loss,
                )
                await self._telegram.send_html(
                    format_circuit_breaker_message(
                        self._daily_pnl.realized_loss,
                        settings.max_daily_loss,
                    )
                )
