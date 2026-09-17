"""
Position sizing & risk calculator.

Calculates exact lot sizes to achieve a fixed $25 USD risk per trade
based on SL distance and per-symbol pip values.

Symbol Specifications:
━━━━━━━━━━━━━━━━━━━━━
XAU/USD (Gold):
  Contract = 100 troy oz per standard lot
  Pip size = 0.01 (1 cent per oz)
  Pip value per lot = 100 oz × $0.01 = $1.00 per pip per lot
  → lot = $25 / (SL_pips × $1.00)
  Example: SL = 30 pips ($0.30) → lot = 25/30 = 0.833 lots

NAS100 (US100/NDX CFD):
  1 standard lot = $1 per index point on most CFD brokers
  Point size = 1.0
  Point value per lot = $1.00
  → lot = $25 / (SL_points × $1.00)
  Example: SL = 50 points → lot = 25/50 = 0.5 lots

USD/JPY:
  Standard Forex: 1 lot = 100,000 units
  Pip = 0.01 JPY
  Pip value per lot (USD) = 100,000 × 0.01 / USDJPY_rate
  At rate 150.00: pip value = 100,000 × 0.01 / 150 = $6.667 per pip per lot
  → lot = $25 / (SL_pips × pip_value_per_lot)
  Example: SL = 20 pips, rate 150 → lot = 25/(20×6.667) = 0.187 lots
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from macropulse.config import settings
from macropulse.logger import get_logger
from macropulse.models import ActionablePair, Bias, RiskCalculation

log = get_logger(__name__)


@dataclass(frozen=True)
class SymbolSpec:
    """Immutable specification for a trading instrument."""
    symbol: str
    pip_size: float           # minimum price movement
    pip_value_per_lot: float  # USD value of 1 pip for 1 standard lot
    # For JPY pairs, pip_value_per_lot is approximate; use dynamic_pip_value()
    requires_dynamic_pip: bool = False
    lot_min: float = 0.01
    lot_step: float = 0.01
    lot_precision: int = 2
    price_precision: int = 2


SYMBOL_SPECS: dict[str, SymbolSpec] = {
    "XAU/USD": SymbolSpec(
        symbol="XAU/USD",
        pip_size=0.01,
        pip_value_per_lot=1.00,      # $1 per pip per lot (100oz × $0.01)
        requires_dynamic_pip=False,
        lot_min=0.01,
        lot_step=0.01,
        lot_precision=2,
        price_precision=2,
    ),
    "NAS100": SymbolSpec(
        symbol="NAS100",
        pip_size=1.0,                # 1 index point
        pip_value_per_lot=1.00,      # $1 per point per lot
        requires_dynamic_pip=False,
        lot_min=0.01,
        lot_step=0.01,
        lot_precision=2,
        price_precision=1,
    ),
    "USD/JPY": SymbolSpec(
        symbol="USD/JPY",
        pip_size=0.01,               # 1 pip = 0.01 JPY
        pip_value_per_lot=6.667,     # at ~150; recalculated dynamically
        requires_dynamic_pip=True,
        lot_min=0.001,
        lot_step=0.001,
        lot_precision=3,
        price_precision=3,
    ),
}


def _dynamic_pip_value(symbol: str, current_price: float) -> float:
    """
    Calculate the live pip value per lot for JPY-denominated pairs.
    pip_value_USD = (pip_size × lot_size_units) / current_price
    For USD/JPY: (0.01 × 100,000) / current_price
    """
    if symbol == "USD/JPY" and current_price > 0:
        return (0.01 * 100_000) / current_price
    return SYMBOL_SPECS[symbol].pip_value_per_lot


def _round_to_step(value: float, step: float, precision: int) -> float:
    """Round lot size down to nearest valid step (always floor for safety)."""
    return round(math.floor(value / step) * step, precision)


class RiskCalculator:
    """
    Computes position sizing and validates R:R for all trade signals.
    Also tracks daily P&L for circuit-breaker enforcement.
    """

    def __init__(self) -> None:
        self._max_risk = settings.max_risk_per_trade
        self._max_daily = settings.max_daily_loss

    def calculate(
        self,
        pair: ActionablePair,
        current_price: float | None = None,
    ) -> RiskCalculation | None:
        """
        Calculate full risk parameters for an ActionablePair.

        Args:
            pair: The trade signal from the LLM.
            current_price: Live mid price (used for dynamic pip value calc).

        Returns:
            RiskCalculation on success, None if the trade fails validation.
        """
        spec = SYMBOL_SPECS.get(pair.symbol)
        if not spec:
            log.warning("risk.unknown_symbol", symbol=pair.symbol)
            return None

        if pair.bias == Bias.STAND_ASIDE:
            log.debug("risk.stand_aside", symbol=pair.symbol)
            return None

        entry = pair.entry_price
        sl = pair.stop_loss
        tp1 = pair.take_profit_1
        tp2 = pair.take_profit_2

        # ── Directional validation ────────────────────────────────────────
        if pair.bias == Bias.BUY:
            if sl >= entry:
                log.warning("risk.invalid_sl", symbol=pair.symbol, bias="BUY",
                            entry=entry, sl=sl, reason="SL must be below entry for BUY")
                return None
            if tp1 <= entry or tp2 <= entry:
                log.warning("risk.invalid_tp", symbol=pair.symbol, bias="BUY")
                return None
        else:  # SELL
            if sl <= entry:
                log.warning("risk.invalid_sl", symbol=pair.symbol, bias="SELL",
                            entry=entry, sl=sl, reason="SL must be above entry for SELL")
                return None
            if tp1 >= entry or tp2 >= entry:
                log.warning("risk.invalid_tp", symbol=pair.symbol, bias="SELL")
                return None

        # ── Distance calculations ────────────────────────────────────────
        sl_distance = abs(entry - sl)
        tp1_distance = abs(tp1 - entry)
        tp2_distance = abs(tp2 - entry)

        sl_pips = sl_distance / spec.pip_size
        tp1_pips = tp1_distance / spec.pip_size
        tp2_pips = tp2_distance / spec.pip_size

        # ── Minimum SL distance check ─────────────────────────────────────
        min_sl_pips = {"XAU/USD": 50, "NAS100": 15, "USD/JPY": 10}
        if sl_pips < min_sl_pips.get(pair.symbol, 5):
            log.warning("risk.sl_too_tight", symbol=pair.symbol, sl_pips=sl_pips)
            return None

        # ── R:R validation ────────────────────────────────────────────────
        rr = tp1_distance / sl_distance if sl_distance > 0 else 0
        if rr < 2.0:
            log.warning("risk.rr_below_minimum", symbol=pair.symbol, rr=round(rr, 2))
            return None

        # ── Pip value (dynamic for JPY pairs) ────────────────────────────
        pip_val = (
            _dynamic_pip_value(pair.symbol, current_price or entry)
            if spec.requires_dynamic_pip
            else spec.pip_value_per_lot
        )

        # ── Lot size calculation ──────────────────────────────────────────
        # lot = max_risk / (sl_pips × pip_value_per_lot)
        raw_lot = self._max_risk / (sl_pips * pip_val)
        lot = _round_to_step(raw_lot, spec.lot_step, spec.lot_precision)
        lot = max(lot, spec.lot_min)  # enforce minimum

        # ── Dollar P&L at TP/SL ──────────────────────────────────────────
        dollar_risk = round(sl_pips * pip_val * lot, 2)
        dollar_tp1 = round(tp1_pips * pip_val * lot, 2)
        dollar_tp2 = round(tp2_pips * pip_val * lot, 2)

        calc = RiskCalculation(
            symbol=pair.symbol,
            entry_price=round(entry, spec.price_precision),
            stop_loss=round(sl, spec.price_precision),
            take_profit_1=round(tp1, spec.price_precision),
            take_profit_2=round(tp2, spec.price_precision),
            sl_pips=round(sl_pips, 1),
            tp1_pips=round(tp1_pips, 1),
            tp2_pips=round(tp2_pips, 1),
            risk_reward_ratio=round(rr, 2),
            lot_size=lot,
            dollar_risk=dollar_risk,
            dollar_tp1=dollar_tp1,
            dollar_tp2=dollar_tp2,
        )

        log.info(
            "risk.calculated",
            symbol=pair.symbol,
            bias=pair.bias.value,
            lot=lot,
            sl_pips=round(sl_pips, 1),
            dollar_risk=dollar_risk,
            rr=round(rr, 2),
        )
        return calc

    def enrich_signal(
        self,
        signal_dict: dict,
        prices: dict[str, float],
    ) -> dict:
        """
        Mutate the signal's actionable_pairs in-place with calculated lot sizes.
        Returns the enriched signal dict (for JSON serialisation to Telegram).
        """
        for pair in signal_dict.get("actionable_pairs", []):
            sym = pair.get("symbol", "")
            calc = self.calculate(
                ActionablePair.model_validate(pair),
                current_price=prices.get(sym),
            )
            if calc:
                pair["lot_size_25usd_risk"] = calc.lot_size
                pair["risk_reward_ratio"] = calc.risk_reward_ratio
                pair["_dollar_risk"] = calc.dollar_risk
                pair["_dollar_tp1"] = calc.dollar_tp1
                pair["_dollar_tp2"] = calc.dollar_tp2
            else:
                # Risk validation failed — force STAND_ASIDE
                pair["bias"] = "STAND_ASIDE"
                pair["lot_size_25usd_risk"] = 0.0
        return signal_dict
