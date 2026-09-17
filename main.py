"""
MacroPulse Sentinel — Entry Point

Usage:
  python main.py                          # Full run (reads .env)
  DRY_RUN=true python main.py             # No Telegram sends
  PRICE_FEED_PROVIDER=mock python main.py # Mock prices (no network)

Zero-cost minimum setup:
  1. Install Ollama → ollama pull llama3.2 → ollama serve
  2. Get free Gemini key at aistudio.google.com (optional cloud fallback)
  3. Get Telegram bot from @BotFather (free)
  4. Everything else works with no keys at all.
"""
from __future__ import annotations

import asyncio
import sys

from macropulse.config import settings
from macropulse.logger import configure_logging, get_logger
from macropulse.orchestrator import Sentinel


def _print_startup_banner() -> None:
    """Print a clear human-readable summary of which free/paid sources are active."""
    lines = [
        "",
        "┌─────────────────────────────────────────────────────┐",
        "│           MacroPulse Sentinel  v1.0.0               │",
        "├─────────────────────────────────────────────────────┤",
    ]

    # LLM stack
    llm_sources = []
    if settings.ollama_enabled:
        llm_sources.append(f"Ollama/{settings.ollama_model} [FREE LOCAL]")
    if settings.gemini_api_key:
        llm_sources.append(f"Gemini/{settings.gemini_model} [FREE CLOUD]")
    if settings.groq_api_key:
        llm_sources.append(f"Groq/{settings.groq_model}")
    if settings.openai_api_key:
        llm_sources.append(f"OpenAI/{settings.openai_model}")
    if settings.anthropic_api_key:
        llm_sources.append(f"Anthropic/{settings.anthropic_model}")
    llm_str = " → ".join(llm_sources) if llm_sources else "⚠ NONE CONFIGURED"
    lines.append(f"│  LLM    : {llm_str[:45]:<45} │")

    # Price feed
    feed_labels = {
        "yfinance":   "yfinance/Yahoo Finance [FREE, no key]",
        "twelvedata": "Twelve Data WebSocket [requires key]",
        "mock":       "Mock/random walk [testing only]",
    }
    lines.append(f"│  Prices : {feed_labels.get(settings.price_feed_provider,'?')[:45]:<45} │")

    # Twitter
    tw_labels = {
        "rss":      "Nitter RSS  [FREE, no key] — 23 accounts",
        "api":      "Twitter API v2 stream [requires key]",
        "disabled": "Disabled",
    }
    lines.append(f"│  Twitter: {tw_labels.get(settings.twitter_mode,'?')[:45]:<45} │")

    # Telegram
    tg_str = "Connected [free from @BotFather]" if settings.has_telegram else "⚠ Not configured"
    lines.append(f"│  Telegram: {tg_str[:44]:<44} │")

    # Mode
    mode_str = "🔕 DRY RUN  (no alerts sent)" if settings.dry_run else "🔔 LIVE MODE  (alerts will fire)"
    lines.append(f"│  Mode   : {mode_str[:45]:<45} │")
    lines.append("├─────────────────────────────────────────────────────┤")
    lines.append("│  Risk   : $25/trade max  |  $750/day circuit break  │")
    lines.append("│  Pairs  : XAU/USD  ·  NAS100  ·  USD/JPY            │")
    lines.append("└─────────────────────────────────────────────────────┘")
    print("\n".join(lines))


async def main() -> None:
    configure_logging()
    log = get_logger("main")
    _print_startup_banner()

    log.info(
        "macropulse.init",
        version="1.0.0",
        dry_run=settings.dry_run,
        price_feed=settings.price_feed_provider,
        twitter_mode=settings.twitter_mode,
        ollama_enabled=settings.ollama_enabled,
        gemini_configured=bool(settings.gemini_api_key),
    )

    if not settings.has_any_llm:
        print(
            "\n❌  No LLM provider found. FREE options:\n"
            "    1. Ollama (local): https://ollama.com\n"
            "       → ollama pull llama3.2\n"
            "       → ollama serve\n"
            "    2. Gemini (cloud): https://aistudio.google.com\n"
            "       → Add GEMINI_API_KEY=... to .env\n"
        )
        sys.exit(1)

    if not settings.has_telegram and not settings.dry_run:
        log.warning(
            "macropulse.no_telegram",
            hint="Get free bot from @BotFather → set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID",
        )

    sentinel = Sentinel()
    try:
        await sentinel.start()
    except KeyboardInterrupt:
        pass
    finally:
        await sentinel.stop()
        log.info("macropulse.exited")


if __name__ == "__main__":
    asyncio.run(main())
