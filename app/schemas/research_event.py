"""ResearchEvent: the analyst-grade unit between a deduplicated NormalisedEvent
and a rendered briefing line.

Per docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17, ResearchEvent is the
typed contract that the equity-analyst-first redesign builds on top of. It is
a pure, deterministic data object: every field here is either copied from an
upstream deterministic source (NormalisedEvent, source_credibility,
relevance_scoring, news_classifier) or computed by a pure function. An LLM
may later draft prose into `interpretation`, `second_order_readthrough`, or
`what_to_watch_next`, but must never set `source_tier`, `confidence`,
`suppress_reason`, `corroboration_score`, or any field that decides inclusion
or trust. Those remain pure-function outputs, exactly as SendDecision and
NewsClassification are today.

This module defines the schema only. It is not yet wired into the pipeline,
generators, or formatters (see Part 17.7 for the phased rollout).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.events import NormalisedEvent

# Small, fixed taxonomy. Replaces the ad-hoc bucket keys used today in
# app/briefing/morning_generator.py::_build_applied_news_stack().
CausalChannel = Literal[
    "rates",
    "earnings",
    "regulatory",
    "geopolitical",
    "supply_chain",
    "sentiment",
    "other",
]

# Source tier vocabulary. Note: the repo currently has two unreconciled trust
# vocabularies (configs/sources.yaml / processing/source_credibility.py's
# highest|high|medium|low|experimental, and app/verticals/scoring.py's
# official|primary|trusted_media|broad_media|social_optional). This field is
# left as a plain str rather than a Literal until that unification happens
# (flagged in docs/BRIEFLY.md Section 12 and BRIEFLY_RESEARCH_AGENT_STRATEGY.md
# Part 17.2); callers should use whichever vocabulary their upstream score
# came from and treat `source_quality_score` as the normalised 0-1 value.

PriceConfirmationStatus = Literal["confirmed", "contradicted", "pending", "unavailable"]
ResearchConfidenceLabel = Literal["high", "medium", "low", "none"]


class EvidenceItem(BaseModel):
    """A single piece of evidence backing a ResearchEvent.

    Distinct from a duplicate-story citation: each EvidenceItem represents an
    independent source corroborating (or contradicting) the same underlying
    event, feeding `corroboration_score`.
    """

    url: str = ""
    source_name: str = ""
    source_tier: str = ""
    published_at: datetime | None = None
    snippet: str = ""


class ResearchEvent(BaseModel):
    """Analyst-grade research unit. See module docstring for authority rules."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_event_id: str = ""  # links back to NormalisedEvent.event_id
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- Identity / content ---
    title: str = ""
    summary: str = ""
    published_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    # --- Source quality (already computable from existing code; see Part 17.3) ---
    source_tier: str = ""
    source_quality_score: float = 0.0  # 0-1, normalised regardless of vocabulary

    # --- Linkage ---
    tickers: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)

    # --- Interpretation taxonomy ---
    causal_channel: CausalChannel = "other"

    # --- Scoring inputs (0-1 unless noted) ---
    novelty_score: float = 0.0
    corroboration_score: float = 0.0
    portfolio_relevance_score: float = 0.0
    watchlist_relevance_score: float = 0.0
    earnings_relevance: float = 0.0
    valuation_relevance: float = 0.0
    macro_relevance: float = 0.0
    geo_relevance: float = 0.0
    confidence: float = 0.5
    confidence_label: ResearchConfidenceLabel = "none"
    confidence_reason: str = ""
    confidence_factors: dict[str, Any] = Field(default_factory=dict)
    freshness_state: str = "unknown"
    material_update: bool = False

    # --- Price confirmation (net new; does not exist anywhere upstream today) ---
    price_confirmation_status: PriceConfirmationStatus = "unavailable"
    price_confirmation_detail: str = ""

    # --- Composite output ---
    final_score: float = 0.0
    suppress_reason: str = ""

    # --- Interpretation (deterministic/template-generated; LLM may draft prose
    # from these fields but never sets them as a trust/inclusion decision) ---
    interpretation: str = ""
    second_order_readthrough: str = ""
    why_now: str = ""
    portfolio_impact: str = ""
    what_changed: str = ""
    what_to_watch_next: str = ""

    # --- Evidence ---
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    primary_sources: list[str] = Field(default_factory=list)
    secondary_sources: list[str] = Field(default_factory=list)

    # --- Optional supporting chart, surfaced via the existing
    # morning_charts.py `optional_event` hook (Part 17.2/17.7 step 7) ---
    chart_key: str | None = None

    @field_validator("tickers", "sectors", "asset_classes", mode="before")
    @classmethod
    def _normalise_string_list(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return list(v or [])

    @field_validator("tickers", mode="after")
    @classmethod
    def _uppercase_tickers(cls, v: list[str]) -> list[str]:
        return [t.upper() for t in v]

    def compute_hash(self) -> str:
        """Deterministic content hash, mirroring NormalisedEvent.compute_hash."""
        raw = f"{self.source_event_id}|{self.title.lower().strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @classmethod
    def from_normalised_event(cls, event: NormalisedEvent) -> ResearchEvent:
        """Seed a ResearchEvent from fields that already have a deterministic
        source today. Does not populate causal_channel, price_confirmation,
        interpretation, or evidence_items: those require logic that does not
        exist yet (see Part 17.7 phases 2-5) and are left at their defaults.
        """
        return cls(
            source_event_id=event.event_id,
            title=event.title,
            summary=event.summary,
            published_at=event.published_at,
            first_seen_at=_raw_datetime(event, "news_first_seen_time") or _raw_datetime(event, "first_seen_at"),
            last_seen_at=_raw_datetime(event, "news_last_seen_time") or _raw_datetime(event, "last_seen_at"),
            tickers=list(event.tickers),
            sectors=list(event.sectors),
            novelty_score=event.novelty_score,
            portfolio_relevance_score=event.personal_relevance_score,
            confidence=event.factual_confidence_score,
            freshness_state=str(event.raw_data.get("news_freshness_state") or event.update_status or "unknown"),
            material_update=bool(event.raw_data.get("news_material_update") or event.update_status == "material_update"),
            final_score=event.final_score,
            suppress_reason=event.reason_code if event.reason_code in {
                "already_sent_tracking_id", "already_sent_exact", "continuation_suppressed",
            } else "",
            primary_sources=[event.url] if event.url else [],
        )


def _raw_datetime(event: NormalisedEvent, key: str) -> datetime | None:
    value = (event.raw_data or {}).get(key)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
