"""Schemas for healthcare / biotech intelligence."""

from __future__ import annotations

from datetime import datetime
import hashlib

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
    healthcare_classification_reason: str = ""
    healthcare_suppression_reason: str = ""


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
    healthcare_classification_reason: str = ""


class HealthcareBriefingSection(BaseModel):
    enabled: bool = False
    title: str = "HEALTHCARE / BIOTECH INTELLIGENCE"
    read: str = ""
    items: list[HealthcareBriefingItem] = Field(default_factory=list)
    confidence: str = "LOW"
    source_count: int = 0
    suppressed_count: int = 0
    unavailable_reason: str = ""


class HealthcareSourceEvent(BaseModel):
    source_key: str = ""
    source_tier: str = "official"
    source_event_id: str = ""
    stable_event_key: str = ""
    title: str = ""
    summary: str = ""
    source_url: str = ""
    published_at: datetime | None = None
    discovered_at: datetime | None = None
    company_name: str = ""
    tickers: list[str] = Field(default_factory=list)
    drug_name: str = ""
    condition: str = ""
    regulator: str = ""
    healthcare_event_type: str = "general_news"
    trial_phase: str = ""
    trial_status: str = ""
    severity: str = "low"
    confidence: float = 0.5
    freshness_state: str = "unknown"
    suppress_reason: str = ""
    raw_data: dict = Field(default_factory=dict)


class HealthcareSourceHealth(BaseModel):
    source_key: str
    status: str = "stub_inactive"  # ok | disabled | rate_limited | error | stub_inactive
    fetched_count: int = 0
    normalized_count: int = 0
    suppressed_count: int = 0
    last_success_at: datetime | None = None
    last_error: str = ""


def normalize_source_tier(value: str | None) -> str:
    tier = str(value or "").strip().lower()
    if tier in {"official", "company", "high_trust_news", "general_news"}:
        return tier
    return "official"


def stable_source_event_key(*, source_key: str, source_event_id: str, title: str, published_at: datetime | None) -> str:
    if source_event_id:
        raw = f"{source_key}|{source_event_id}"
    else:
        ts = published_at.isoformat() if published_at else ""
        raw = f"{source_key}|{title.strip().lower()}|{ts}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
