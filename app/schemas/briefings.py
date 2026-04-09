"""Pydantic schemas for briefing outputs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    NormalisedEvent,
    QuoteData,
    SectorSnapshot,
)


class MarketSetup(BaseModel):
    """Market overview section: indices, rates, commodities, crypto."""

    index_quotes: list[QuoteData] = Field(default_factory=list)
    macro_quotes: list[QuoteData] = Field(default_factory=list)
    treasury_10y: MacroDataPoint | None = None
    treasury_2y: MacroDataPoint | None = None
    vix: QuoteData | None = None
    summary_line: str = ""


class MorningBriefing(BaseModel):
    """Complete morning briefing, ready for formatting and delivery."""

    generated_at: datetime = Field(default_factory=datetime.now)
    market_setup: MarketSetup = Field(default_factory=MarketSetup)
    macro_context: list[MacroDataPoint] = Field(default_factory=list)
    top_themes: list[NormalisedEvent] = Field(default_factory=list)
    sector_scan: list[SectorSnapshot] = Field(default_factory=list)
    earnings_calendar: list[EarningsEvent] = Field(default_factory=list)
    watchlist_events: list[NormalisedEvent] = Field(default_factory=list)
    watchlist_quotes: list[QuoteData] = Field(default_factory=list)
    event_count: int = 0
    events_fetched: int = 0
    events_after_dedup: int = 0
    events_sent: int = 0


class IntradayUpdate(BaseModel):
    """Hourly intraday update with only new, material developments."""

    generated_at: datetime = Field(default_factory=datetime.now)
    hour_label: str = ""
    market_snapshot: list[QuoteData] = Field(default_factory=list)
    new_events: list[NormalisedEvent] = Field(default_factory=list)
    events_fetched: int = 0
    events_after_dedup: int = 0
    events_sent: int = 0


class BreakingAlert(BaseModel):
    """Single high-importance breaking alert."""

    generated_at: datetime = Field(default_factory=datetime.now)
    event: NormalisedEvent
    market_context: list[QuoteData] = Field(default_factory=list)
    reason: str = ""
