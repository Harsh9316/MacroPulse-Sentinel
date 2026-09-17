"""
Tests for the risk calculator.

Verifies lot size math, R:R validation, and per-symbol pip value calculations.
"""
import pytest
from macropulse.models import ActionablePair, Bias, EntryType
from macropulse.risk.calculator import RiskCalculator, _dynamic_pip_value


@pytest.fixture
def calc():
    return RiskCalculator()


# ── XAU/USD Tests ────────────────────────────────────────────────────────────

def test_xauusd_buy_lot_size(calc):
    """$25 risk on 50-pip SL = 0.50 lots for XAU/USD."""
    pair = ActionablePair(
        symbol="XAU/USD",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=2340.00,
        stop_loss=2339.50,   # 50 pips below = $0.50 distance
        take_profit_1=2341.50,  # 150 pips = 3:1 R:R
        take_profit_2=2342.50,
        risk_reward_ratio=3.0,
        lot_size_25usd_risk=0.0,
    )
    result = calc.calculate(pair)
    assert result is not None
    # 50 pips × $1/pip/lot → lot = 25/50 = 0.50
    assert result.lot_size == pytest.approx(0.50, abs=0.01)
    assert result.dollar_risk <= 25.50  # allow small rounding


def test_xauusd_sell_lot_size(calc):
    """SELL: SL above entry, TP below entry."""
    pair = ActionablePair(
        symbol="XAU/USD",
        bias=Bias.SELL,
        entry_type=EntryType.MARKET,
        entry_price=2340.00,
        stop_loss=2340.50,   # 50 pips above
        take_profit_1=2338.50,  # 150 pips below = 3:1 R:R
        take_profit_2=2337.00,
        risk_reward_ratio=3.0,
        lot_size_25usd_risk=0.0,
    )
    result = calc.calculate(pair)
    assert result is not None
    assert result.lot_size == pytest.approx(0.50, abs=0.01)


# ── NAS100 Tests ─────────────────────────────────────────────────────────────

def test_nas100_buy_lot_size(calc):
    """$25 risk on 50-point SL = 0.5 lots for NAS100."""
    pair = ActionablePair(
        symbol="NAS100",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=19850.0,
        stop_loss=19800.0,   # 50 points
        take_profit_1=19950.0,  # 100 pts = 2:1
        take_profit_2=20050.0,
        risk_reward_ratio=2.0,
        lot_size_25usd_risk=0.0,
    )
    result = calc.calculate(pair)
    assert result is not None
    assert result.lot_size == pytest.approx(0.50, abs=0.01)
    assert result.dollar_risk <= 25.10


def test_nas100_rr_enforced(calc):
    """Signals with R:R < 2.0 must be rejected."""
    pair = ActionablePair(
        symbol="NAS100",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=19850.0,
        stop_loss=19800.0,   # 50 points SL
        take_profit_1=19880.0,  # 30 pts TP → R:R = 0.6 (rejected)
        take_profit_2=19900.0,
        risk_reward_ratio=2.5,  # LLM claims 2.5 but actual is < 2
        lot_size_25usd_risk=0.0,
    )
    result = calc.calculate(pair)
    assert result is None  # should be rejected


# ── USD/JPY Tests ─────────────────────────────────────────────────────────────

def test_usdjpy_dynamic_pip_value():
    """At 150.00, pip value for 1 lot USD/JPY ≈ $6.667."""
    pip_val = _dynamic_pip_value("USD/JPY", 150.00)
    assert pip_val == pytest.approx(6.667, rel=0.01)


def test_usdjpy_buy_lot_size(calc):
    """$25 risk, 20-pip SL at USDJPY=150 → lot ≈ 0.187"""
    pair = ActionablePair(
        symbol="USD/JPY",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=150.00,
        stop_loss=149.80,    # 20 pips
        take_profit_1=150.60,  # 60 pips = 3:1
        take_profit_2=151.00,
        risk_reward_ratio=3.0,
        lot_size_25usd_risk=0.0,
    )
    result = calc.calculate(pair, current_price=150.00)
    assert result is not None
    # 20 pips × 6.667/pip/lot → lot = 25/133.33 ≈ 0.187
    assert result.lot_size == pytest.approx(0.187, abs=0.01)
    assert result.dollar_risk <= 25.50


# ── Edge Cases ───────────────────────────────────────────────────────────────

def test_stand_aside_returns_none(calc):
    pair = ActionablePair(
        symbol="XAU/USD",
        bias=Bias.STAND_ASIDE,
        entry_type=EntryType.MARKET,
        entry_price=2340.0,
        stop_loss=2339.0,
        take_profit_1=2342.0,
        take_profit_2=2344.0,
        risk_reward_ratio=2.0,
        lot_size_25usd_risk=0.0,
    )
    assert calc.calculate(pair) is None


def test_invalid_buy_sl_above_entry(calc):
    """BUY with SL above entry must be rejected."""
    pair = ActionablePair(
        symbol="XAU/USD",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=2340.0,
        stop_loss=2341.0,   # WRONG: SL above entry for BUY
        take_profit_1=2345.0,
        take_profit_2=2350.0,
        risk_reward_ratio=4.0,
        lot_size_25usd_risk=0.0,
    )
    assert calc.calculate(pair) is None


def test_sl_too_tight_rejected(calc):
    """SL < minimum pips must be rejected (prevents absurd lot sizes)."""
    pair = ActionablePair(
        symbol="NAS100",
        bias=Bias.BUY,
        entry_type=EntryType.MARKET,
        entry_price=19850.0,
        stop_loss=19847.0,   # only 3 points — below 15pt minimum
        take_profit_1=19860.0,
        take_profit_2=19870.0,
        risk_reward_ratio=3.0,
        lot_size_25usd_risk=0.0,
    )
    assert calc.calculate(pair) is None
