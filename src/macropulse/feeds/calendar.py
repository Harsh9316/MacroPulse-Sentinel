"""
Economic Calendar Poller — Forex Factory JSON endpoint.

Adaptive polling:
  - Every CALENDAR_POLL_FAST_SECONDS (5s) when within 10 min of a red event
  - Every CALENDAR_POLL_IDLE_SECONDS (60s) otherwise

High-impact events trigger immediate dispatch to the event queue.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from macropulse.config import HIGH_IMPACT_EVENTS, settings
from macropulse.dedup import dedup_cache
from macropulse.logger import get_logger
from macropulse.models import EconomicEvent

log = get_logger(__name__)

# Forex Factory public JSON calendar endpoint
# Returns current week's events — no auth required
_FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
# Alternative / backup endpoint
_FF_CALENDAR_ALT = "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"

_HIGH_IMPACT_CURRENCIES = {"USD", "JPY", "EUR", "GBP"}


def _is_high_impact(event_name: str, currency: str, impact: str) -> bool:
    """Return True if this event warrants immediate LLM evaluation."""
    if impact not in ("HIGH",):
        return False
    if currency not in _HIGH_IMPACT_CURRENCIES:
        return False
    name_lower = event_name.lower()
    return any(kw in name_lower for kw in HIGH_IMPACT_EVENTS)


def _parse_ff_datetime(date_str: str, time_str: str) -> datetime | None:
    """Parse Forex Factory date/time strings into a UTC datetime."""
    try:
        # FF format: date="2025-01-17", time="8:30am"
        combined = f"{date_str} {time_str}".strip()
        if not time_str or time_str.lower() in ("all day", "tentative", ""):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.strptime(combined, "%Y-%m-%d %I:%M%p")
        # FF calendar is US/Eastern — convert to UTC (approximation: UTC-5)
        # For production accuracy, use pytz or zoneinfo
        dt = dt.replace(tzinfo=timezone.utc) + timedelta(hours=5)
        return dt
    except Exception:
        return None


def _event_dedup_key(event: dict[str, Any]) -> str:
    """Generate a stable hash key for a Forex Factory event dict."""
    key = f"{event.get('date','')}{event.get('title','')}{event.get('actual','')}"
    return hashlib.sha256(key.encode()).hexdigest()


class EconomicCalendarPoller:
    """
    Polls the Forex Factory JSON calendar and publishes high-impact
    EconomicEvent objects to the shared asyncio queue.
    """

    def __init__(self, queue: asyncio.Queue[EconomicEvent | Any]) -> None:
        self._queue = queue
        self._session: aiohttp.ClientSession | None = None
        self._last_known_events: dict[str, dict] = {}  # hash -> event dict

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "MacroPulse-Sentinel/1.0"},
        )
        log.info("calendar_poller.started")
        await self._run_loop()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()

    # ── Main Loop ────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while True:
            interval = await self._poll_once()
            await asyncio.sleep(interval)

    async def _poll_once(self) -> int:
        """
        Fetch calendar, dispatch new high-impact events.
        Returns the recommended sleep interval (seconds).
        """
        try:
            events = await self._fetch_calendar()
        except Exception as exc:
            log.warning("calendar_poller.fetch_error", error=str(exc))
            return settings.calendar_poll_idle_seconds

        now = datetime.now(timezone.utc)
        fast_mode = False

        for raw in events:
            dt = _parse_ff_datetime(raw.get("date", ""), raw.get("time", ""))
            impact = raw.get("impact", "").upper()
            currency = raw.get("country", "").upper()
            name = raw.get("title", "")

            # Check if we're within 10 minutes of a high-impact event
            if dt and impact == "HIGH" and currency in _HIGH_IMPACT_CURRENCIES:
                delta = abs((dt - now).total_seconds())
                if delta <= 600:  # 10 min window
                    fast_mode = True

            if not _is_high_impact(name, currency, impact):
                continue

            # Only dispatch if actual value just appeared (data release)
            actual = raw.get("actual")
            if not actual:
                continue

            h = _event_dedup_key(raw)
            is_new = await dedup_cache.check_and_mark(
                f"calendar:{h}:{actual}"
            )
            if not is_new:
                continue

            event = EconomicEvent(
                event_id=h,
                event_name=name,
                country=raw.get("country", ""),
                currency=currency,
                actual=actual,
                forecast=raw.get("forecast"),
                previous=raw.get("previous"),
                impact=impact,
                scheduled_at=dt or now,
                source="forex_factory",
                raw_text=f"{name} actual={actual} forecast={raw.get('forecast')} prev={raw.get('previous')}",
            )
            log.info(
                "calendar_poller.event_dispatched",
                name=name,
                currency=currency,
                actual=actual,
                forecast=raw.get("forecast"),
            )
            await self._queue.put(event)

        return (
            settings.calendar_poll_fast_seconds
            if fast_mode
            else settings.calendar_poll_idle_seconds
        )

    async def _fetch_calendar(self) -> list[dict[str, Any]]:
        """Fetch JSON from Forex Factory with fallback URL."""
        for url in (_FF_CALENDAR_URL, _FF_CALENDAR_ALT):
            try:
                async with self._session.get(url) as resp:  # type: ignore[union-attr]
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientError:
                continue
        raise RuntimeError("All calendar URLs failed")
