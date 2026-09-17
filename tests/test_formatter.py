"""
Tests for the Telegram alert formatter.
"""
import pytest
from macropulse.models import ActionablePair, Bias, EntryType, ImpactRating, TradeSignal
from macropulse.telegram.formatter import format_signal, format_startup_message


def _make_signal(**kwargs) -> TradeSignal:
    defaults = dict(
        event_title="US NFP — Massive Beat",
        currency_affected="USD",
        impact_rating=ImpactRating.HIGH,
        macro_analysis=(
            "Strong payrolls signal a resilient labour market, "
            "reinforcing Fed hawkishness and boosting USD broadly."
        ),
        actionable_pairs=[
            ActionablePair(
                symbol="XAU/USD",
                bias=Bias.SELL,
                entry_type=EntryType.MARKET,
                entry_price=2340.00,
                stop_loss=2340.80,
                take_profit_1=2337.80,
                take_profit_2=2335.00,
                risk_reward_ratio=2.75,
                lot_size_25usd_risk=0.31,
            ),
            ActionablePair(
                symbol="NAS100",
                bias=Bias.SELL,
                entry_type=EntryType.MARKET,
                entry_price=19850.0,
                stop_loss=19900.0,
                take_profit_1=19750.0,
                take_profit_2=19650.0,
                risk_reward_ratio=2.0,
                lot_size_25usd_risk=0.5,
            ),
            ActionablePair(
                symbol="USD/JPY",
                bias=Bias.BUY,
                entry_type=EntryType.MARKET,
                entry_price=150.00,
                stop_loss=149.80,
                take_profit_1=150.60,
                take_profit_2=151.00,
                risk_reward_ratio=3.0,
                lot_size_25usd_risk=0.187,
            ),
        ],
        llm_provider="groq",
        llm_model="llama-3-3-70b-versatile",
    )
    defaults.update(kwargs)
    return TradeSignal(**defaults)


def test_format_signal_contains_event_title():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "US NFP" in msg.upper() or "NFP" in msg.upper()


def test_format_signal_contains_all_symbols():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "XAU/USD" in msg
    assert "NAS100" in msg
    assert "USD/JPY" in msg


def test_format_signal_contains_buy_sell_emojis():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "🔴" in msg or "SELL" in msg
    assert "🟢" in msg or "BUY" in msg


def test_format_signal_contains_trade_params():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "2340.00" in msg or "2340" in msg  # XAU entry
    assert "19850" in msg or "19850.0" in msg  # NAS100 entry
    assert "150.000" in msg or "150.00" in msg  # USD/JPY entry


def test_format_signal_contains_risk_info():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "$25" in msg or "25" in msg
    assert "R:R" in msg or "R/R" in msg or "2." in msg


def test_format_signal_html_tags():
    signal = _make_signal()
    msg = format_signal(signal)
    assert "<b>" in msg
    assert "<code>" in msg
    assert "<i>" in msg


def test_format_signal_stand_aside():
    signal = _make_signal(
        actionable_pairs=[
            ActionablePair(
                symbol="XAU/USD",
                bias=Bias.STAND_ASIDE,
                entry_type=EntryType.MARKET,
                entry_price=2340.0,
                stop_loss=2339.0,
                take_profit_1=2342.0,
                take_profit_2=2344.0,
                risk_reward_ratio=2.0,
                lot_size_25usd_risk=0.0,
            ),
        ] + _make_signal().actionable_pairs[1:]
    )
    msg = format_signal(signal)
    assert "STAND ASIDE" in msg


def test_startup_message():
    msg = format_startup_message("groq/llama-3-3-70b-versatile", "mock", dry_run=True)
    assert "MacroPulse" in msg
    assert "DRY RUN" in msg.upper()
    assert "XAU/USD" in msg


def test_startup_message_live():
    msg = format_startup_message("groq/llama-3-3-70b-versatile", "twelvedata", dry_run=False)
    assert "LIVE" in msg.upper()
