"""
Telegram alert formatter.

Renders rich HTML-formatted trade signal messages for the Telegram bot.
Each alert is visually distinct: BUY (green), SELL (red), STAND_ASIDE (grey).
"""
from __future__ import annotations

from datetime import datetime, timezone

from macropulse.models import ActionablePair, Bias, ImpactRating, TradeSignal

# Emoji palette
_BIAS_EMOJI = {Bias.BUY: "🟢", Bias.SELL: "🔴", Bias.STAND_ASIDE: "⚪"}
_IMPACT_EMOJI = {
    ImpactRating.HIGH: "🔥",
    ImpactRating.MEDIUM: "⚡",
    ImpactRating.LOW: "💤",
}
_SYMBOL_EMOJI = {
    "XAU/USD": "🥇",
    "NAS100": "📊",
    "USD/JPY": "🇯🇵",
}

_DIVIDER = "━" * 30


def _fmt_price(price: float, symbol: str) -> str:
    """Format price with appropriate decimal places per symbol."""
    decimals = {"XAU/USD": 2, "NAS100": 1, "USD/JPY": 3}
    d = decimals.get(symbol, 4)
    return f"{price:.{d}f}"


def _pair_block(pair: dict) -> str:
    """Render a single instrument block within the alert."""
    symbol = pair.get("symbol", "")
    bias_str = pair.get("bias", "STAND_ASIDE")
    try:
        bias = Bias(bias_str)
    except ValueError:
        bias = Bias.STAND_ASIDE

    emoji = _BIAS_EMOJI[bias]
    sym_emoji = _SYMBOL_EMOJI.get(symbol, "📈")

    if bias == Bias.STAND_ASIDE:
        return (
            f"\n{sym_emoji} <b>{symbol}</b>\n"
            f"⚪ <i>STAND ASIDE — No actionable setup</i>\n"
        )

    entry = pair.get("entry_price", 0)
    sl = pair.get("stop_loss", 0)
    tp1 = pair.get("take_profit_1", 0)
    tp2 = pair.get("take_profit_2", 0)
    rr = pair.get("risk_reward_ratio", 0)
    lot = pair.get("lot_size_25usd_risk", 0)
    entry_type = pair.get("entry_type", "MARKET")
    dollar_risk = pair.get("_dollar_risk", 25.0)
    dollar_tp1 = pair.get("_dollar_tp1", 0)
    dollar_tp2 = pair.get("_dollar_tp2", 0)

    bias_label = "📈 BUY" if bias == Bias.BUY else "📉 SELL"
    entry_label = "🎯 MARKET ORDER" if entry_type == "MARKET" else "🎯 LIMIT ORDER"

    return (
        f"\n{sym_emoji} <b>{symbol}</b>  {emoji} <b>{bias_label}</b>\n"
        f"<code>{_DIVIDER}</code>\n"
        f"{entry_label}\n"
        f"  ↳ Entry   : <b>{_fmt_price(entry, symbol)}</b>\n"
        f"  ↳ Stop    : <code>{_fmt_price(sl, symbol)}</code>  🛡 (${dollar_risk:.2f} risk)\n"
        f"  ↳ TP-1    : <code>{_fmt_price(tp1, symbol)}</code>  💰 (+${dollar_tp1:.2f})\n"
        f"  ↳ TP-2    : <code>{_fmt_price(tp2, symbol)}</code>  💰 (+${dollar_tp2:.2f})\n"
        f"  ↳ R:R     : <b>{rr:.1f}:1</b>\n"
        f"  ↳ Lot     : <b>{lot} lots</b>  (${25:.0f} risk)\n"
    )


def format_signal(signal: TradeSignal, triggered_by: str = "") -> str:
    """
    Render a complete Telegram HTML alert for a TradeSignal.

    Args:
        signal: Validated TradeSignal with enriched lot sizes.
        triggered_by: Source label (e.g. "Reuters", "@federalreserve").

    Returns:
        HTML-formatted string ready to send via Telegram.
    """
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    impact_emoji = _IMPACT_EMOJI.get(signal.impact_rating, "⚡")

    # Count actionable (non-STAND_ASIDE) pairs
    pairs_data = [p if isinstance(p, dict) else p.model_dump() for p in signal.actionable_pairs]
    actionable_count = sum(1 for p in pairs_data if p.get("bias") != "STAND_ASIDE")

    header = (
        f"🚨 <b>MACROPULSE SENTINEL ALERT</b> 🚨\n"
        f"<code>{_DIVIDER}</code>\n"
        f"{impact_emoji} <b>{signal.event_title.upper()}</b>\n"
        f"🌍 Currency: <code>{signal.currency_affected}</code>  "
        f"Impact: <b>{signal.impact_rating.value}</b>\n"
        f"🕐 <code>{now}</code>  |  Source: <i>{triggered_by or signal.triggered_by_source or 'N/A'}</i>\n"
        f"\n"
        f"📌 <b>MACRO ANALYSIS:</b>\n"
        f"<i>{signal.macro_analysis}</i>\n"
        f"\n"
        f"<code>{_DIVIDER}</code>\n"
        f"<b>📐 TRADE SETUPS ({actionable_count}/3 actionable)</b>\n"
    )

    pairs_section = ""
    for pair_obj in pairs_data:
        pairs_section += _pair_block(pair_obj)

    footer = (
        f"\n<code>{_DIVIDER}</code>\n"
        f"⚠️ <i>Risk: $25 max per trade | SL mandatory before entry</i>\n"
        f"🤖 <i>Model: {signal.llm_provider}/{signal.llm_model}</i>\n"
        f"#MacroPulse #{signal.currency_affected} #{signal.impact_rating.value}"
    )

    return header + pairs_section + footer


def format_signal_dict(signal_dict: dict, triggered_by: str = "") -> str:
    """
    Same as format_signal() but accepts a raw dict (post-enrichment).
    Useful when lot sizes have been mutated in-place before serialisation.
    """
    signal = TradeSignal.model_validate(signal_dict)
    # Carry over the enriched _dollar_* fields
    for i, pair_obj in enumerate(signal.actionable_pairs):
        raw_pair = signal_dict["actionable_pairs"][i]
        pair_dict = pair_obj.model_dump()
        pair_dict["_dollar_risk"] = raw_pair.get("_dollar_risk", 25.0)
        pair_dict["_dollar_tp1"] = raw_pair.get("_dollar_tp1", 0.0)
        pair_dict["_dollar_tp2"] = raw_pair.get("_dollar_tp2", 0.0)
        # Replace in signal
        signal.actionable_pairs[i] = ActionablePair.model_validate(pair_dict)

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    impact_emoji = _IMPACT_EMOJI.get(signal.impact_rating, "⚡")

    pairs_data = []
    for i, pair_obj in enumerate(signal.actionable_pairs):
        pd = pair_obj.model_dump()
        raw_pair = signal_dict["actionable_pairs"][i]
        pd["_dollar_risk"] = raw_pair.get("_dollar_risk", 25.0)
        pd["_dollar_tp1"] = raw_pair.get("_dollar_tp1", 0.0)
        pd["_dollar_tp2"] = raw_pair.get("_dollar_tp2", 0.0)
        pairs_data.append(pd)

    actionable_count = sum(1 for p in pairs_data if p.get("bias") != "STAND_ASIDE")

    header = (
        f"🚨 <b>MACROPULSE SENTINEL ALERT</b> 🚨\n"
        f"<code>{_DIVIDER}</code>\n"
        f"{impact_emoji} <b>{signal.event_title.upper()}</b>\n"
        f"🌍 Currency: <code>{signal.currency_affected}</code>  "
        f"Impact: <b>{signal.impact_rating.value}</b>\n"
        f"🕐 <code>{now}</code>  |  Source: <i>{triggered_by or signal.triggered_by_source or 'N/A'}</i>\n"
        f"\n"
        f"📌 <b>MACRO ANALYSIS:</b>\n"
        f"<i>{signal.macro_analysis}</i>\n"
        f"\n"
        f"<code>{_DIVIDER}</code>\n"
        f"<b>📐 TRADE SETUPS ({actionable_count}/3 actionable)</b>\n"
    )

    pairs_section = ""
    for pair_data in pairs_data:
        pairs_section += _pair_block(pair_data)

    footer = (
        f"\n<code>{_DIVIDER}</code>\n"
        f"⚠️ <i>Risk: $25 max per trade | SL mandatory before entry</i>\n"
        f"🤖 <i>Model: {signal.llm_provider}/{signal.llm_model}</i>\n"
        f"#MacroPulse #{signal.currency_affected} #{signal.impact_rating.value}"
    )

    return header + pairs_section + footer


def format_startup_message(provider: str, mode: str, dry_run: bool) -> str:
    """System startup notification."""
    return (
        f"✅ <b>MacroPulse Sentinel Online</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"🤖 LLM: <code>{provider}</code>\n"
        f"📡 Feed: <code>{mode}</code>\n"
        f"💵 Risk: <code>$25/trade | $750/day max</code>\n"
        f"🎯 Pairs: <code>XAU/USD | NAS100 | USD/JPY</code>\n"
        f"{'🔕 <b>DRY RUN — No real alerts will fire</b>' if dry_run else '🔔 <b>LIVE MODE</b>'}\n"
        f"#MacroPulse #Online"
    )


def format_circuit_breaker_message(daily_loss: float, max_loss: float) -> str:
    """Daily loss circuit breaker notification."""
    return (
        f"🛑 <b>CIRCUIT BREAKER TRIGGERED</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"Daily loss: <b>${daily_loss:.2f}</b> / ${max_loss:.2f}\n"
        f"⛔ <b>No further trade signals will be sent today.</b>\n"
        f"Resets at midnight UTC.\n"
        f"#MacroPulse #CircuitBreaker"
    )
