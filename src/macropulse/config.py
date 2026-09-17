"""
Central configuration via Pydantic Settings (reads from .env / environment).
"""
from __future__ import annotations


from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM — FREE LOCAL (Ollama) ───────────────────────────────────────
    # Ollama: run models 100% locally, no API key required.
    # Install: https://ollama.com  →  ollama pull llama3.2
    ollama_base_url: str = "http://localhost:11434"   # default Ollama address
    ollama_model: str = "llama3.2"                    # or mistral, phi3, etc.
    ollama_enabled: bool = True                        # tries Ollama first always

    # ── LLM — FREE CLOUD (Google Gemini) ────────────────────────────────
    # Free tier: 1500 req/day, no credit card needed.
    # Get key: https://aistudio.google.com/app/apikey
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = "gemini-1.5-flash"            # fastest free model

    # ── LLM — PAID (optional fallbacks, used only if above fail) ────────
    groq_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)
    groq_model: str = "llama-3-3-70b-versatile"
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-3-5-haiku-20241022"

    # ── Telegram (bot token from @BotFather — always free) ──────────────
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)

    # ── Twitter/X — FREE via Nitter RSS (no key needed) ─────────────────
    twitter_bearer_token: str | None = Field(default=None)
    twitter_mode: Literal["api", "rss", "disabled"] = "rss"   # rss = no key

    # ── Price Feed — FREE via yfinance (Yahoo Finance, no key needed) ────
    twelvedata_api_key: str | None = Field(default=None)
    price_feed_provider: Literal["twelvedata", "yfinance", "mock"] = "yfinance"

    # ── Risk Parameters ─────────────────────────────────────────────────
    account_size: float = 25_000.0
    max_risk_per_trade: float = 25.0
    max_daily_loss: float = 750.0

    # ── Polling Intervals ───────────────────────────────────────────────
    calendar_poll_fast_seconds: int = 5
    calendar_poll_idle_seconds: int = 60
    rss_poll_interval_seconds: int = 15

    # ── Operational ─────────────────────────────────────────────────────
    dry_run: bool = False
    log_level: str = "INFO"
    dedup_db_path: Path = Path("./data/dedup.db")

    @field_validator("dedup_db_path", mode="before")
    @classmethod
    def _make_path(cls, v: str | Path) -> Path:
        p = Path(v)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def has_any_llm(self) -> bool:
        """True if at least one LLM provider is available (Ollama counts as always-available)."""
        return self.ollama_enabled or bool(
            self.gemini_api_key
            or self.groq_api_key
            or self.openai_api_key
            or self.anthropic_api_key
        )

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


# Singleton — import this everywhere
settings = Settings()


# ── Monitored Twitter/X Handles (with full context) ─────────────────────────
TWITTER_WATCHLIST: dict[str, dict] = {
    # ── Central Banks ──────────────────────────────────────────────────
    "federalreserve": {
        "label": "Federal Reserve",
        "currency": "USD",
        "tier": "CRITICAL",
        "id": "27260086",
    },
    "ecb": {
        "label": "European Central Bank",
        "currency": "EUR",
        "tier": "CRITICAL",
        "id": "172958346",
    },
    "bankofengland": {
        "label": "Bank of England",
        "currency": "GBP",
        "tier": "CRITICAL",
        "id": "61717058",
    },
    "bank_of_japan_e": {
        "label": "Bank of Japan (English)",
        "currency": "JPY",
        "tier": "CRITICAL",
        "id": "2896556648",
    },
    "snb_bns": {
        "label": "Swiss National Bank",
        "currency": "CHF",
        "tier": "HIGH",
        "id": "379525065",
    },
    "rbainfo": {
        "label": "Reserve Bank of Australia",
        "currency": "AUD",
        "tier": "HIGH",
        "id": "277136722",
    },
    "bankofcanada": {
        "label": "Bank of Canada",
        "currency": "CAD",
        "tier": "HIGH",
        "id": "18774017",
    },
    # ── Key Officials ──────────────────────────────────────────────────
    "lagarde": {
        "label": "Christine Lagarde (ECB President)",
        "currency": "EUR",
        "tier": "CRITICAL",
        "id": "2940480836",
    },
    "janethyellen": {
        "label": "Janet Yellen (US Treasury Secretary)",
        "currency": "USD",
        "tier": "CRITICAL",
        "id": "18765432",  # approximate — verify before prod
    },
    "bosticicfed": {
        "label": "Raphael Bostic (Atlanta Fed President)",
        "currency": "USD",
        "tier": "HIGH",
        "id": "3362197928",
    },
    "nick_timiraos": {
        "label": "Nick Timiraos (WSJ — Fed Whisperer)",
        "currency": "USD",
        "tier": "HIGH",
        "id": "1116180705",
    },
    "gregipwsj": {
        "label": "Greg Ip (WSJ Chief Economics Commentator)",
        "currency": "USD",
        "tier": "MEDIUM",
        "id": "108271792",
    },
    # ── Financial News Wires ───────────────────────────────────────────
    "reuters": {
        "label": "Reuters",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "1652541",
    },
    "reutersbiz": {
        "label": "Reuters Business",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "19013366",
    },
    "bloomberg": {
        "label": "Bloomberg",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "336983867",
    },
    "markets": {
        "label": "Bloomberg Markets",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "302602916",
    },
    "financialjuice": {
        "label": "FinancialJuice (ultra-low-latency wire)",
        "currency": "MULTI",
        "tier": "CRITICAL",
        "id": "359878210",
    },
    "forexlive": {
        "label": "ForexLive",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "44226080",
    },
    "wsjmarkets": {
        "label": "WSJ Markets",
        "currency": "MULTI",
        "tier": "HIGH",
        "id": "17629359",
    },
    "cnbc": {
        "label": "CNBC",
        "currency": "MULTI",
        "tier": "MEDIUM",
        "id": "22029919",
    },
    "zerohedge": {
        "label": "ZeroHedge (breaking macro)",
        "currency": "MULTI",
        "tier": "MEDIUM",
        "id": "266009612",
    },
    "fxstreet": {
        "label": "FXStreet",
        "currency": "MULTI",
        "tier": "MEDIUM",
        "id": "18289765",
    },
    "mktcall": {
        "label": "MarketCall / Market News",
        "currency": "MULTI",
        "tier": "MEDIUM",
        "id": "123456",  # placeholder — update with actual ID
    },
}

# Nitter RSS base URL (fallback when Twitter API not available)
NITTER_BASE_URL = "https://nitter.privacydev.net"

# High-impact economic event keywords (case-insensitive)
HIGH_IMPACT_EVENTS = frozenset({
    "fomc", "federal funds rate", "fed rate",
    "nonfarm payrolls", "nfp", "non-farm payroll",
    "cpi", "consumer price index", "core cpi", "core inflation",
    "ppi", "producer price index",
    "boj rate", "bank of japan rate", "japan interest rate",
    "boe rate", "bank of england rate",
    "ecb rate", "ecb interest rate", "european central bank rate",
    "retail sales",
    "gdp", "gross domestic product",
    "unemployment rate", "jobless claims", "initial claims",
    "ism manufacturing", "ism services",
    "pce", "personal consumption expenditure",
    "core pce",
    "trade balance",
    "jackson hole",
    "hawkish", "dovish",
})

# Target trading instruments
TARGET_SYMBOLS = ["XAU/USD", "NAS100", "USD/JPY"]
