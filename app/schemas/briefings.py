"""Pydantic schemas for briefing outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.delivery import ChartAsset
from app.healthcare.schemas import HealthcareBriefingSection
from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    MarketBreadth,
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
    market_breadth: list[MarketBreadth] = Field(default_factory=list)


class MorningBriefing(BaseModel):
    """Complete morning briefing, ready for formatting and delivery."""

    generated_at: datetime = Field(default_factory=datetime.now)
    session_mode: SessionMode = "weekday"
    session_key: str = "morning"
    session_title: str = "Morning Briefing"
    market_setup: MarketSetup = Field(default_factory=MarketSetup)
    market_setup_analysis: str = ""
    dominant_tape_driver: str = ""
    market_setup_analysis_confidence: str = "low"
    market_setup_signal_tags: list[str] = Field(default_factory=list)
    macro_context: list[MacroDataPoint] = Field(default_factory=list)
    commodity_strip: list[MacroDataPoint] = Field(default_factory=list)
    regional_lens: list[dict[str, str]] = Field(default_factory=list)
    regional_skew_summary: str = ""
    portfolio_impact_bullets: list[str] = Field(default_factory=list)
    portfolio_action_posture: str = ""
    geo_risk_level: str = ""
    geo_risk_raw_level: str = ""
    geo_risk_summary: str = ""
    regime_snapshot: dict[str, str] = Field(default_factory=dict)
    regime_shift: dict[str, str] = Field(default_factory=dict)
    regime_context: str = ""
    positioning_alignment: str = ""
    global_news: list[NormalisedEvent] = Field(default_factory=list)
    top_themes: list[NormalisedEvent] = Field(default_factory=list)
    portfolio_focus: list[NormalisedEvent] = Field(default_factory=list)
    applied_news_stack: list[dict[str, object]] = Field(default_factory=list)
    sector_scan: list[SectorSnapshot] = Field(default_factory=list)
    earnings_calendar: list[EarningsEvent] = Field(default_factory=list)
    earnings_relevance: dict[str, str] = Field(default_factory=dict)
    watchlist_events: list[NormalisedEvent] = Field(default_factory=list)
    watchlist_quotes: list[QuoteData] = Field(default_factory=list)
    portfolio_quotes: list[QuoteData] = Field(default_factory=list)
    session_quality_score: float = 0.0
    session_quality_bucket: str = ""
    session_quality_color_hex: str = ""
    session_quality_label: str = ""
    section_confidence: dict[str, str] = Field(default_factory=dict)
    data_freshness: dict[str, str] = Field(default_factory=dict)
    market_data_outage: bool = False
    news_data_outage: bool = False
    news_pipeline_status: str = ""
    news_raw_fetched: int = 0
    news_after_fingerprint_dedup: int = 0
    session_diagnosis: dict[str, object] = Field(default_factory=dict)
    trigger_board: dict[str, list[str]] = Field(default_factory=dict)
    macro_policy_watch: str = ""
    vertical_shadow_lines: list[str] = Field(default_factory=list)
    valuation_lens_lines: list[str] = Field(default_factory=list)
    quote_freshness: dict[str, dict] = Field(default_factory=dict)
    data_basis_lines: list[str] = Field(default_factory=list)
    market_clock_context: dict[str, object] = Field(default_factory=dict)
    what_changed_header: str = "WHAT CHANGED"
    what_changed_lines: list[str] = Field(default_factory=list)
    healthcare_intelligence: HealthcareBriefingSection | None = None
    contract_warnings: list[str] = Field(default_factory=list)
    canonical_prices: dict[str, dict] = Field(default_factory=dict)
    morning_chart_bundle: dict = Field(default_factory=dict)
    morning_chart_selection: list[dict[str, str]] = Field(default_factory=list)
    chart_assets: list[ChartAsset] = Field(default_factory=list)
    event_count: int = 0
    events_fetched: int = 0
    events_after_dedup: int = 0
    events_sent: int = 0
    fx_pulse_section: str = ""
    fx_materiality_score: int = 0


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


BreakingTier = Literal["breaking", "high_priority", "regular", "ignore"]


class BreakingClassification(BaseModel):
    """Deterministic classification metadata for breaking candidates."""

    tier: BreakingTier = "breaking"
    category: str = "macro"
    impact_score: int = 0
    confidence_score: int = 0
    novelty_score: int = 0
    immediacy_score: int = 0
    breadth_score: int = 0
    why_markets_care: str = ""
    watch_assets: list[str] = Field(default_factory=list)
    watch_symbols: list[str] = Field(default_factory=list)
    confirm_signals: list[str] = Field(default_factory=list)
    invalidate_signals: list[str] = Field(default_factory=list)
    storyline_key: str = ""


class BreakingAlert(BaseModel):
    """Single high-importance breaking alert."""

    generated_at: datetime = Field(default_factory=datetime.now)
    event: NormalisedEvent
    market_context: list[QuoteData] = Field(default_factory=list)
    reason: str = ""
    classification: BreakingClassification = Field(default_factory=BreakingClassification)
    tracking_ids: list[str] = Field(default_factory=list)
