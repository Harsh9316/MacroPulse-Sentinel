"""
Live price feed manager.

Provider priority (free-first):
  1. yfinance  — Yahoo Finance polling, completely FREE, no API key
  2. twelvedata — WebSocket, requires paid/free-tier key
  3. mock       — random walk, for dry-run / CI testing

yfinance symbol mapping (Yahoo Finance tickers):
  XAU/USD  →  GC=F   (Gold Futures — ~99% correlated with spot)
  NAS100   →  NQ=F   (E-mini Nasdaq 100 Futures)
  USD/JPY  →  JPY=X  (USD/JPY forex rate)
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import NamedTuple

import aiohttp

from macropulse.config import settings
from macropulse.logger import get_logger

log = get_logger(__name__)

# ── yfinance symbol mapping ───────────────────────────────────────────────────
# Maps our internal symbol names → Yahoo Finance tickers
_YFINANCE_TICKERS: dict[str, str] = {
    "XAU/USD": "GC=F",     # Gold Futures (COMEX) — best free proxy for spot XAU/USD
    "NAS100":  "NQ=F",     # E-mini Nasdaq-100 Futures
    "USD/JPY": "JPY=X",    # USD/JPY forex spot rate
}

# Twelve Data WebSocket endpoint (used only if PRICE_FEED_PROVIDER=twelvedata)
_TD_WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"
_TWELVEDATA_MAP = {"XAU/USD": "XAU/USD", "NAS100": "NDX", "USD/JPY": "USD/JPY"}

# Mock baseline prices
_MOCK_BASE_PRICES: dict[str, float] = {
    "XAU/USD": 2340.00,
    "NAS100":  19_850.00,
    "USD/JPY": 151.50,
}

# yfinance poll interval (seconds) — don't go below 3s to avoid Yahoo throttling
_YFINANCE_POLL_INTERVAL = 5


class Price(NamedTuple):
    symbol: str
    bid: float
    ask: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class PriceFeedManager:
    """
    Maintains a live price cache for all target symbols.
    Exposes get_price(symbol) for synchronous reads by the risk calculator.
    """

    def __init__(self) -> None:
        self._prices: dict[str, Price] = {}
        self._provider = settings.price_feed_provider
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        if self._provider == "yfinance":
            log.info("price_feed.started", mode="yfinance_yahoo_finance", note="FREE_NO_KEY")
            asyncio.create_task(self._run_yfinance_loop())
        elif self._provider == "twelvedata":
            log.info("price_feed.started", mode="twelvedata_websocket")
            asyncio.create_task(self._run_ws_loop())
        else:
            log.info("price_feed.started", mode="mock")
            asyncio.create_task(self._run_mock_loop())

    async def stop(self) -> None:
        self._running = False

    # ── Public API ───────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> Price | None:
        return self._prices.get(symbol)

    def get_mid_price(self, symbol: str) -> float | None:
        p = self.get_price(symbol)
        return p.mid if p else None

    # ── yfinance (FREE — Yahoo Finance polling) ───────────────────────────

    async def _run_yfinance_loop(self) -> None:
        """
        Poll Yahoo Finance every 5 seconds using yfinance.
        Completely free, no API key required.
        
        Note: GC=F (Gold futures) has a ~10-min delay in yfinance free tier.
        For live scalping use twelvedata. For swing/position trading yfinance is fine.
        """
        try:
            import yfinance as yf
        except ImportError:
            log.error(
                "price_feed.yfinance_not_installed",
                hint="pip install yfinance",
            )
            # Fall back to mock
            await self._run_mock_loop()
            return

        tickers_str = " ".join(_YFINANCE_TICKERS.values())
        log.info("price_feed.yfinance_subscribing", tickers=tickers_str)

        while self._running:
            try:
                # Fetch all 3 symbols in one batch call
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(
                    None, self._fetch_yfinance_batch
                )
                for internal_sym, price_val in data.items():
                    if price_val and price_val > 0:
                        # Yahoo Finance doesn't provide bid/ask for all instruments
                        # Use a tight synthetic spread (0.01% of price)
                        spread = price_val * 0.0001
                        self._prices[internal_sym] = Price(
                            symbol=internal_sym,
                            bid=round(price_val - spread / 2, 4),
                            ask=round(price_val + spread / 2, 4),
                            timestamp=datetime.now(timezone.utc),
                        )
                        log.debug("price_feed.yfinance_update",
                                  symbol=internal_sym, price=round(price_val, 4))
            except Exception as exc:
                log.warning("price_feed.yfinance_error", error=str(exc))

            await asyncio.sleep(_YFINANCE_POLL_INTERVAL)

    def _fetch_yfinance_batch(self) -> dict[str, float]:
        """Synchronous yfinance batch fetch (run in executor)."""
        import yfinance as yf

        result: dict[str, float] = {}
        tickers = yf.Tickers(" ".join(_YFINANCE_TICKERS.values()))

        for internal_sym, yahoo_ticker in _YFINANCE_TICKERS.items():
            try:
                info = tickers.tickers[yahoo_ticker].fast_info
                # fast_info.last_price is the most recent trade price
                price = getattr(info, "last_price", None)
                if price is None:
                    # fallback: use regularMarketPrice from info dict
                    full_info = tickers.tickers[yahoo_ticker].info
                    price = full_info.get("regularMarketPrice") or full_info.get("ask")
                if price:
                    result[internal_sym] = float(price)
            except Exception as exc:
                log.debug("price_feed.yfinance_ticker_error",
                          ticker=yahoo_ticker, error=str(exc))
        return result

    # ── Twelve Data WebSocket (optional, requires key) ───────────────────

    async def _run_ws_loop(self) -> None:
        backoff = 1
        while self._running:
            try:
                await self._ws_connect()
                backoff = 1
            except Exception as exc:
                log.warning("price_feed.ws_error", error=str(exc), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _ws_connect(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                _TD_WS_URL,
                params={"apikey": settings.twelvedata_api_key},
                heartbeat=30,
            ) as ws:
                await ws.send_json({
                    "action": "subscribe",
                    "params": {"symbols": ",".join(_TWELVEDATA_MAP.values())},
                })
                log.info("price_feed.twelvedata_subscribed")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await self._handle_ws_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def _handle_ws_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if data.get("event") == "price":
            td_symbol = data.get("symbol", "")
            internal = next(
                (k for k, v in _TWELVEDATA_MAP.items() if v == td_symbol), td_symbol
            )
            try:
                price_val = float(data.get("price", 0))
                bid = float(data.get("bid", price_val))
                ask = float(data.get("ask", price_val))
                self._prices[internal] = Price(
                    symbol=internal, bid=bid, ask=ask,
                    timestamp=datetime.now(timezone.utc),
                )
            except (TypeError, ValueError):
                pass

    # ── Mock Price Generator (testing) ────────────────────────────────────

    async def _run_mock_loop(self) -> None:
        bases = dict(_MOCK_BASE_PRICES)
        while self._running:
            for symbol, base in bases.items():
                drift = base * random.gauss(0, 0.0005)
                new_price = max(base + drift, base * 0.95)
                bases[symbol] = new_price
                spread = base * 0.0001
                self._prices[symbol] = Price(
                    symbol=symbol,
                    bid=round(new_price - spread / 2, 4),
                    ask=round(new_price + spread / 2, 4),
                    timestamp=datetime.now(timezone.utc),
                )
            await asyncio.sleep(1)


# Module-level singleton
price_feed = PriceFeedManager()
