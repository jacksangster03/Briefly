"""Pydantic schemas for briefing outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.delivery import ChartAsset
from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    NormalisedEvent,
    QuoteData,
    SectorSnapshot,
)

SessionMode = Literal["weekday", "saturday", "sunday"]


def session_mode_for(dt: datetime) -> SessionMode:
    """Derive the session mode from a local datetime's weekday.

    Saturday/Sunday get their own labels so the formatter can switch into
    weekend framing: there's no live cash session, index quotes are stale,
    and the reference close is Friday's close rather than "yesterday".
    """
    weekday = dt.weekday()  # Mon=0..Sun=6
    if weekday == 5:
        return "saturday"
    if weekday == 6:
        return "sunday"
    return "weekday"


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
    session_mode: SessionMode = "weekday"
    market_setup: MarketSetup = Field(default_factory=MarketSetup)
    macro_context: list[MacroDataPoint] = Field(default_factory=list)
    global_news: list[NormalisedEvent] = Field(default_factory=list)
    top_themes: list[NormalisedEvent] = Field(default_factory=list)
    portfolio_focus: list[NormalisedEvent] = Field(default_factory=list)
    sector_scan: list[SectorSnapshot] = Field(default_factory=list)
    earnings_calendar: list[EarningsEvent] = Field(default_factory=list)
    watchlist_events: list[NormalisedEvent] = Field(default_factory=list)
    watchlist_quotes: list[QuoteData] = Field(default_factory=list)
    portfolio_quotes: list[QuoteData] = Field(default_factory=list)
    chart_assets: list[ChartAsset] = Field(default_factory=list)
    event_count: int = 0
    events_fetched: int = 0
    events_after_dedup: int = 0
    events_sent: int = 0


class IntradayUpdate(BaseModel):
    """Hourly intraday update with only new, material developments."""

    generated_at: datetime = Field(default_factory=datetime.now)
    session_mode: SessionMode = "weekday"
    hour_label: str = ""
    market_snapshot: list[QuoteData] = Field(default_factory=list)
    global_risk_items: list[NormalisedEvent] = Field(default_factory=list)
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
