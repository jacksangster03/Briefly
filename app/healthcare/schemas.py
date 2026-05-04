"""Schemas for healthcare / biotech intelligence."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthcareEvent(BaseModel):
    title: str = ""
    summary: str = ""
    source: str = ""
    source_url: str = ""
    published_at: datetime | None = None
    event_type: str = "general_news"
    company_tickers: list[str] = Field(default_factory=list)
    company_names: list[str] = Field(default_factory=list)
    asset_names: list[str] = Field(default_factory=list)
    therapy_areas: list[str] = Field(default_factory=list)
    modality: list[str] = Field(default_factory=list)
    trial_phase: str = ""
    regulator: str = ""
    geography: str = ""
    severity: str = "low"
    source_quality: str = "generic_news"
    relevance_score: float = 0.0
    market_relevance: str = ""
    portfolio_lens: str = ""


class HealthcareBriefingItem(BaseModel):
    title: str = ""
    summary: str = ""
    event_type: str = "general_news"
    severity: str = "low"
    company_display: str = ""
    asset_display: str = ""
    theme_tags: list[str] = Field(default_factory=list)
    market_relevance: str = ""
    portfolio_lens: str = ""
    source_line: str = ""
    published_at: datetime | None = None


class HealthcareBriefingSection(BaseModel):
    enabled: bool = False
    title: str = "HEALTHCARE / BIOTECH INTELLIGENCE"
    read: str = ""
    items: list[HealthcareBriefingItem] = Field(default_factory=list)
    confidence: str = "LOW"
    source_count: int = 0
    suppressed_count: int = 0
    unavailable_reason: str = ""

