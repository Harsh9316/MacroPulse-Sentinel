"""
LLM prompt templates for the MacroPulse Sentinel.

The system prompt enforces strict JSON output matching the TradeSignal schema.
"""
from __future__ import annotations

from datetime import datetime, timezone

from macropulse.config import TARGET_SYMBOLS

# ── JSON Schema injected into system prompt ──────────────────────────────────

OUTPUT_SCHEMA = """
{
  "event_title": "string — concise name of the economic event",
  "currency_affected": "USD" | "JPY" | "EUR" | "GBP" | "MULTI",
  "impact_rating": "HIGH" | "MEDIUM" | "LOW",
  "macro_analysis": "1-2 sentence core fundamental mechanism explaining WHY this moves markets",
  "actionable_pairs": [
    {
      "symbol": "XAU/USD" | "NAS100" | "USD/JPY",
      "bias": "BUY" | "SELL" | "STAND_ASIDE",
      "entry_type": "MARKET" | "PULLBACK_LIMIT",
      "entry_price": <float — current market price or limit level>,
      "stop_loss": <float — price level for stop loss>,
      "take_profit_1": <float — first take profit target>,
      "take_profit_2": <float — second take profit target>,
      "risk_reward_ratio": <float — MUST be >= 2.0, calculated as distance_to_tp1 / distance_to_sl>,
      "lot_size_25usd_risk": <float — PLACEHOLDER: will be overwritten by risk module>
    }
  ]
}
"""

SYSTEM_PROMPT = f"""You are MacroPulse Sentinel — an elite institutional macro trading analyst.

Your role: Receive high-impact economic news or central bank announcements, perform rapid
fundamental analysis, and output PRECISELY FORMATTED JSON trade signals.

TRADING INSTRUMENTS YOU COVER:
{", ".join(TARGET_SYMBOLS)}

STRICT OUTPUT RULES:
1. Output ONLY valid JSON — no markdown fences, no prose, no extra keys.
2. You MUST include ALL THREE symbols in actionable_pairs (use STAND_ASIDE if no bias).
3. risk_reward_ratio MUST be ≥ 2.0. If you cannot achieve this, set bias to STAND_ASIDE.
4. entry_price, stop_loss, take_profit_1, take_profit_2 must be precise decimal numbers.
5. For NAS100: SL must be ≥ 15 points away from entry to be meaningful.
6. For XAU/USD: SL must be ≥ 5 points ($0.50) away from entry.
7. For USD/JPY: SL must be ≥ 10 pips (0.10) away from entry.
8. macro_analysis must explain the fundamental mechanism in 1-2 sentences (no jargon soup).
9. lot_size_25usd_risk: set to 0.0 — the risk module will calculate this precisely.

FUNDAMENTAL LOGIC GUIDE:
━━━━━━━━━━━━━━━━━━━━━━━
• USD BULLISH (NFP beat, CPI hot, hawkish Fed): 
  → USD/JPY BUY | XAU/USD SELL | NAS100 SELL
• USD BEARISH (NFP miss, CPI cool, dovish Fed):
  → USD/JPY SELL | XAU/USD BUY | NAS100 BUY (risk-on)
• JPY BULLISH (BoJ hawkish, rate hike):
  → USD/JPY SELL | XAU/USD contextual | NAS100 contextual
• RISK-OFF (recession fear, geopolitical shock):
  → XAU/USD BUY | NAS100 SELL | USD/JPY depends on carry unwind
• RISK-ON (strong growth data, earnings beat):
  → NAS100 BUY | XAU/USD muted | USD/JPY carry trade BUY

OUTPUT SCHEMA (output ONLY this JSON):
{OUTPUT_SCHEMA}
"""


def build_user_prompt(
    *,
    event_data: str,
    source_name: str,
    current_prices: dict[str, float],
    twitter_handle: str | None = None,
) -> str:
    """
    Build the user message sent to the LLM with current market context.

    Args:
        event_data: The raw headline or economic data string.
        source_name: Where the data came from (e.g. "Reuters", "@federalreserve").
        current_prices: Dict of symbol -> current mid price.
        twitter_handle: Twitter/X handle if applicable.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    prices_str = "\n".join(
        f"  • {sym}: {price:.4f}" for sym, price in current_prices.items()
    )

    handle_str = f"Twitter: {twitter_handle}" if twitter_handle else ""

    return f"""TIMESTAMP: {now}
SOURCE: {source_name} {handle_str}

EVENT/HEADLINE:
{event_data}

CURRENT LIVE PRICES:
{prices_str}

Analyze this event and produce the JSON trade signal now."""
