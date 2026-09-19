"""
MacroPulse Sentinel — Real-Time Dashboard Server

Serves the web dashboard at http://localhost:8765
Exposes:
  GET  /                    → index.html
  GET  /api/state           → full current state snapshot (JSON)
  GET  /api/signals         → last 50 signals (JSON)
  GET  /api/events          → last 100 raw events (JSON)
  WS   /ws                  → real-time push of prices, signals, events

The DashboardBroadcaster singleton is shared with the Sentinel orchestrator.
Call dashboard.push_price(), dashboard.push_signal(), dashboard.push_event()
from within the orchestrator to stream updates to all connected browsers.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from collections import deque
from datetime import datetime, timezone
from typing import Any

from aiohttp import WSMsgType, web

from macropulse.config import settings
from macropulse.logger import get_logger

log = get_logger(__name__)

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_MAX_SIGNALS = 50
_MAX_EVENTS = 100
_DEFAULT_PORT = 8765


class DashboardBroadcaster:
    """
    In-memory state store + WebSocket broadcast hub.
    Thread-safe via asyncio — all methods must be called from the event loop.
    """

    def __init__(self) -> None:
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._signals: deque[dict] = deque(maxlen=_MAX_SIGNALS)
        self._events: deque[dict] = deque(maxlen=_MAX_EVENTS)
        self._prices: dict[str, dict] = {}
        self._system: dict[str, Any] = {
            "status": "starting",
            "llm_provider": "—",
            "price_feed": settings.price_feed_provider,
            "twitter_mode": settings.twitter_mode,
            "dry_run": settings.dry_run,
            "daily_loss": 0.0,
            "max_daily_loss": settings.max_daily_loss,
            "signals_today": 0,
            "circuit_breaker": False,
            "feeds": {
                "calendar": "idle",
                "rss": "idle",
                "twitter": "idle",
            },
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Public push API (called by Sentinel orchestrator) ─────────────────

    async def push_price(self, symbol: str, bid: float, ask: float) -> None:
        mid = (bid + ask) / 2
        prev = self._prices.get(symbol, {}).get("mid", mid)
        change = mid - prev
        self._prices[symbol] = {
            "symbol": symbol,
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "mid": round(mid, 4),
            "change": round(change, 4),
            "pct": round((change / prev * 100) if prev else 0, 3),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await self._broadcast({"type": "price", "data": self._prices[symbol]})

    async def push_signal(self, signal_dict: dict) -> None:
        payload = {**signal_dict, "received_at": datetime.now(timezone.utc).isoformat()}
        self._signals.appendleft(payload)
        self._system["signals_today"] += 1
        await self._broadcast({"type": "signal", "data": payload})

    async def push_event(self, event_dict: dict) -> None:
        payload = {**event_dict, "received_at": datetime.now(timezone.utc).isoformat()}
        self._events.appendleft(payload)
        await self._broadcast({"type": "event", "data": payload})

    async def update_system(self, **kwargs: Any) -> None:
        self._system.update(kwargs)
        await self._broadcast({"type": "system", "data": self._system})

    async def update_feed_status(self, feed: str, status: str) -> None:
        self._system["feeds"][feed] = status
        await self._broadcast({"type": "system", "data": self._system})

    # ── WebSocket management ─────────────────────────────────────────────

    async def _broadcast(self, message: dict) -> None:
        if not self._ws_clients:
            return
        raw = json.dumps(message, default=str)
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(raw)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    def _state_snapshot(self) -> dict:
        return {
            "signals": list(self._signals),
            "events": list(self._events),
            "prices": self._prices,
            "system": self._system,
        }

    # ── aiohttp route handlers ───────────────────────────────────────────

    async def handle_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "index.html")

    async def handle_state(self, request: web.Request) -> web.Response:
        return web.json_response(self._state_snapshot(), dumps=lambda x: json.dumps(x, default=str))

    async def handle_signals(self, request: web.Request) -> web.Response:
        return web.json_response(list(self._signals), dumps=lambda x: json.dumps(x, default=str))

    async def handle_events(self, request: web.Request) -> web.Response:
        return web.json_response(list(self._events), dumps=lambda x: json.dumps(x, default=str))

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        log.info("dashboard.ws_connected", total=len(self._ws_clients))

        # Send full state snapshot on connect
        await ws.send_str(json.dumps({"type": "snapshot", "data": self._state_snapshot()}, default=str))

        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
        self._ws_clients.discard(ws)
        log.info("dashboard.ws_disconnected", total=len(self._ws_clients))
        return ws


# Module-level singleton — imported by orchestrator
dashboard = DashboardBroadcaster()


async def start_dashboard_server(port: int = _DEFAULT_PORT) -> None:
    """Start the aiohttp web server in the background."""
    app = web.Application()
    app.router.add_get("/", dashboard.handle_index)
    app.router.add_get("/api/state", dashboard.handle_state)
    app.router.add_get("/api/signals", dashboard.handle_signals)
    app.router.add_get("/api/events", dashboard.handle_events)
    app.router.add_get("/ws", dashboard.handle_websocket)
    app.router.add_static("/static", _STATIC_DIR)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("dashboard.server_started", url=f"http://localhost:{port}")
    print(f"\n  🖥️  Dashboard: \033[1;36mhttp://localhost:{port}\033[0m\n")
