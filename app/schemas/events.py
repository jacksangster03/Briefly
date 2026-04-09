"""Pydantic schemas for market events, quotes, and macro data."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QuoteData(BaseModel):
    """Real-time or delayed quote for a single instrument."""

    symbol: str
    display_name: str = ""
    current_price: float
    change: float = 0.0
    change_percent: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    previous_close: float = 0.0
    volume: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now())
    source: str = ""


class MacroDataPoint(BaseModel):
    """Single observation from a macroeconomic time series."""

    series_id: str
    name: str = ""
    value: float
    previous_value: float | None = None
    change: float | None = None
    change_percent: float | None = None
    date: str = ""          # YYYY-MM-DD
    source: str = "fred"


class EarningsEvent(BaseModel):
    """Upcoming or recent earnings report."""

    symbol: str
    company_name: str = ""
    report_date: str = ""   # YYYY-MM-DD
    fiscal_quarter: str = ""
    eps_estimate: float | None = None
    eps_actual: float | None = None
    revenue_estimate: float | None = None
    revenue_actual: float | None = None
    surprise_percent: float | None = None
    time: str = ""          # bmo | amc | during
    source: str = ""


class NormalisedEvent(BaseModel):
    """Canonical event schema used throughout the processing pipeline.

    Every piece of market intelligence, regardless of source, is normalised
    into this schema before scoring, dedup, and briefing assembly.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = ""                        # finnhub | newsapi | sec | fred
    source_type: str = ""                   # news | filing | earnings | macro | social
    published_at: datetime | None = None
    title: str = ""
    summary: str = ""
    url: str = ""
    tickers: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    event_type: str = ""                    # earnings | guidance | fda | macro | m_and_a | ...
    sentiment: float = 0.0                  # -1.0 to 1.0
    importance_score: float = 0.0           # 0 to 1
    novelty_score: float = 1.0             # 0 to 1 (1 = brand new)
    personal_relevance_score: float = 0.0   # 0 to 1
    factual_confidence_score: float = 0.5   # 0 to 1
    attention_score: float = 0.0            # 0 to 1
    final_score: float = 0.0               # 0 to 1, composite
    content_hash: str = ""
    cluster_id: str | None = None
    cluster_size: int = 1
    already_sent: bool = False
    update_status: str = "new"             # new | material_update | duplicate
    reason_code: str = ""
    score_explanation: str = ""
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tickers", mode="before")
    @classmethod
    def _normalise_tickers(cls, v):
        if isinstance(v, str):
            return [t.strip().upper() for t in v.split(",") if t.strip()]
        return [t.upper() for t in (v or [])]

    def compute_hash(self) -> str:
        """Generate a content hash from title + source for dedup."""
        raw = f"{self.source}|{self.title.lower().strip()}"
        self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self.content_hash


class SectorSnapshot(BaseModel):
    """Summary of a sector's current state for briefing assembly."""

    sector_key: str
    display_name: str
    etf_symbol: str = ""
    etf_quote: QuoteData | None = None
    top_events: list[NormalisedEvent] = Field(default_factory=list)
    notable_movers: list[QuoteData] = Field(default_factory=list)
