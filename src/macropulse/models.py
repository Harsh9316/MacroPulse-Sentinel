"""
Pydantic V2 data models for MacroPulse Sentinel.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class ImpactRating(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Bias(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    STAND_ASIDE = "STAND_ASIDE"


class EntryType(str, Enum):
    MARKET = "MARKET"
    PULLBACK_LIMIT = "PULLBACK_LIMIT"


class SourceType(str, Enum):
    CALENDAR = "CALENDAR"
    RSS = "RSS"
    TWITTER = "TWITTER"


# ── Raw Ingestion Models ─────────────────────────────────────────────────────

class EconomicEvent(BaseModel):
    """Raw economic calendar event from Forex Factory or similar."""
    event_id: str                          # unique ID or generated hash
    event_name: str
    country: str
    currency: str
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None
    impact: Literal["HIGH", "MEDIUM", "LOW", "HOLIDAY"]
    scheduled_at: datetime
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "forex_factory"
    raw_text: str = ""                     # full text for dedup hashing


class NewsHeadline(BaseModel):
    """Breaking headline from RSS feed or Twitter/X."""
    headline_id: str                       # SHA-256 of text
    title: str
    summary: str | None = None
    url: str | None = None
    source_name: str                       # e.g. "Reuters", "@federalreserve"
    source_type: SourceType
    twitter_handle: str | None = None      # e.g. "federalreserve"
    published_at: datetime | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    raw_text: str = ""


# ── LLM Output Models ────────────────────────────────────────────────────────

class ActionablePair(BaseModel):
    """Trade signal for a single instrument."""
    symbol: Literal["XAU/USD", "NAS100", "USD/JPY"]
    bias: Bias
    entry_type: EntryType
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    lot_size_25usd_risk: float             # calculated by risk module

    @field_validator("risk_reward_ratio")
    @classmethod
    def _rr_min(cls, v: float) -> float:
        if v < 2.0:
            raise ValueError(f"R:R {v} is below minimum threshold of 2.0")
        return round(v, 2)

    @field_validator("lot_size_25usd_risk")
    @classmethod
    def _lot_non_negative(cls, v: float) -> float:
        # Allow 0.0 as LLM placeholder — risk module fills real value
        if v < 0:
            raise ValueError("Lot size cannot be negative")
        return round(v, 4)


class TradeSignal(BaseModel):
    """
    Structured LLM output — exactly the schema specified in the project brief.
    """
    event_title: str
    currency_affected: Literal["USD", "JPY", "EUR", "GBP", "MULTI"]
    impact_rating: ImpactRating
    macro_analysis: str                    # 1-2 sentence fundamental mechanism
    actionable_pairs: list[ActionablePair]

    # Metadata added by the orchestrator (not from LLM)
    triggered_by_id: str | None = None    # headline_id or event_id
    triggered_by_source: str | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    llm_provider: str | None = None
    llm_model: str | None = None


# ── Risk Calculation ─────────────────────────────────────────────────────────

class PipValue(BaseModel):
    """Pip/point value configuration per symbol."""
    symbol: str
    pip_size: float           # e.g. 0.01 for XAU/USD, 1.0 for NAS100, 0.01 for USD/JPY
    pip_value_per_lot: float  # USD value of 1 pip for 1 standard lot
    lot_precision: int = 2    # decimal places for lot size display


class RiskCalculation(BaseModel):
    """Output of the risk calculator for a single trade."""
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    sl_pips: float
    tp1_pips: float
    tp2_pips: float
    risk_reward_ratio: float
    lot_size: float
    dollar_risk: float        # should be ≤ MAX_RISK_PER_TRADE
    dollar_tp1: float
    dollar_tp2: float


# ── Alert State ──────────────────────────────────────────────────────────────

class DailyPnL(BaseModel):
    """Tracks intraday risk exposure."""
    date: str                             # YYYY-MM-DD
    realized_loss: float = 0.0
    signals_fired: int = 0
    circuit_breaker_triggered: bool = False

    def can_trade(self, max_daily_loss: float) -> bool:
        return not self.circuit_breaker_triggered and self.realized_loss < max_daily_loss
