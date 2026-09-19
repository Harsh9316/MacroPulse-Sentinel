"""
Unit tests for the MacroPulse Sentinel Dashboard server and broadcaster.
"""
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from macropulse.dashboard.server import DashboardBroadcaster, _STATIC_DIR


@pytest.mark.asyncio
async def test_broadcaster_push_price():
    broadcaster = DashboardBroadcaster()
    await broadcaster.push_price("XAU/USD", 2650.10, 2650.50)
    state = broadcaster._state_snapshot()
    assert "XAU/USD" in state["prices"]
    p = state["prices"]["XAU/USD"]
    assert p["bid"] == 2650.10
    assert p["ask"] == 2650.50
    assert p["mid"] == 2650.30


@pytest.mark.asyncio
async def test_broadcaster_push_signal():
    broadcaster = DashboardBroadcaster()
    sample_signal = {
        "event_title": "US Non-Farm Payrolls",
        "impact_rating": "HIGH",
        "pairs": {
            "XAU/USD": {"action": "BUY", "confidence": 0.85},
            "NAS100": {"action": "STAND_ASIDE", "confidence": 0.50},
            "USD/JPY": {"action": "SELL", "confidence": 0.90},
        },
    }
    await broadcaster.push_signal(sample_signal)
    state = broadcaster._state_snapshot()
    assert len(state["signals"]) == 1
    assert state["signals"][0]["event_title"] == "US Non-Farm Payrolls"
    assert state["system"]["signals_today"] == 1


@pytest.mark.asyncio
async def test_broadcaster_push_event():
    broadcaster = DashboardBroadcaster()
    await broadcaster.push_event({"title": "FOMC Rate Decision", "source": "calendar", "impact": "HIGH"})
    state = broadcaster._state_snapshot()
    assert len(state["events"]) == 1
    assert state["events"][0]["title"] == "FOMC Rate Decision"


@pytest.mark.asyncio
async def test_broadcaster_update_system_and_feeds():
    broadcaster = DashboardBroadcaster()
    await broadcaster.update_system(llm_provider="ollama/llama3", status="running")
    await broadcaster.update_feed_status("calendar", "active")
    state = broadcaster._state_snapshot()
    assert state["system"]["llm_provider"] == "ollama/llama3"
    assert state["system"]["status"] == "running"
    assert state["system"]["feeds"]["calendar"] == "active"


@pytest.mark.asyncio
async def test_dashboard_routes():
    from aiohttp.test_utils import TestClient, TestServer

    broadcaster = DashboardBroadcaster()
    await broadcaster.push_price("USD/JPY", 152.10, 152.15)
    await broadcaster.push_signal({"event_title": "CPI Release"})
    await broadcaster.push_event({"title": "CPI Release", "source": "calendar"})

    app = web.Application()
    app.router.add_get("/", broadcaster.handle_index)
    app.router.add_get("/api/state", broadcaster.handle_state)
    app.router.add_get("/api/signals", broadcaster.handle_signals)
    app.router.add_get("/api/events", broadcaster.handle_events)
    app.router.add_get("/ws", broadcaster.handle_websocket)
    app.router.add_static("/static", _STATIC_DIR)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        # Test index.html
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "MacroPulse Sentinel" in text

        # Test /api/state
        resp = await client.get("/api/state")
        assert resp.status == 200
        data = await resp.json()
        assert "prices" in data
        assert "USD/JPY" in data["prices"]

        # Test /api/signals
        resp = await client.get("/api/signals")
        assert resp.status == 200
        signals = await resp.json()
        assert len(signals) == 1

        # Test /api/events
        resp = await client.get("/api/events")
        assert resp.status == 200
        events = await resp.json()
        assert len(events) == 1
    finally:
        await client.close()

