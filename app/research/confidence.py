"""Deterministic source-grounded confidence for ResearchEvent objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas.research_event import ResearchEvent

ConfidenceLabel = Literal["high", "medium", "low", "none"]


@dataclass(frozen=True)
class ResearchConfidence:
    label: ConfidenceLabel
    reason: str
    factors: dict[str, Any] = field(default_factory=dict)


_HIGH_SOURCE_TIERS = {"highest", "official", "high", "primary"}
_MEDIUM_SOURCE_TIERS = {"medium", "trusted_media"}
_STALE_STATES = {"stale", "old_context", "repeated"}
_FRESH_STATES = {"new", "updated", "material_update"}


def calculate_research_confidence(event: ResearchEvent) -> ResearchConfidence:
    """Return evidence confidence, not statistical certainty.

    Confidence is calculated after retrieval/classification from deterministic
    source quality, freshness, corroboration, relevance and price-confirmation
    signals. It never authorizes inclusion by itself.
    """
    source_tier = str(event.source_tier or "").lower()
    freshness = str(event.freshness_state or "unknown").lower()
    price_status = str(event.price_confirmation_status or "unavailable").lower()
    has_evidence = bool(event.primary_sources or event.secondary_sources or event.evidence_items)
    relevance = max(float(event.portfolio_relevance_score or 0.0), float(event.watchlist_relevance_score or 0.0))
    quality = float(event.source_quality_score or 0.0)
    corroboration = float(event.corroboration_score or 0.0)
    confidence = float(event.confidence or 0.0)
    material_update = bool(event.material_update)
    strong_source = source_tier in _HIGH_SOURCE_TIERS or quality >= 0.80
    decent_source = strong_source or source_tier in _MEDIUM_SOURCE_TIERS or quality >= 0.60
    fresh = freshness in _FRESH_STATES or material_update
    stale = freshness in _STALE_STATES

    factors = {
        "source_tier": event.source_tier,
        "source_quality_score": round(quality, 4),
        "freshness_state": event.freshness_state,
        "corroboration_score": round(corroboration, 4),
        "novelty_score": round(float(event.novelty_score or 0.0), 4),
        "price_confirmation_status": event.price_confirmation_status,
        "causal_channel": event.causal_channel,
        "portfolio_relevance_score": round(float(event.portfolio_relevance_score or 0.0), 4),
        "watchlist_relevance_score": round(float(event.watchlist_relevance_score or 0.0), 4),
        "material_update": material_update,
        "has_evidence": has_evidence,
    }

    if not has_evidence or event.suppress_reason:
        return ResearchConfidence(
            "none",
            "No eligible evidence basis cleared the analyst-output gate.",
            factors,
        )
    if stale and not material_update:
        return ResearchConfidence(
            "low",
            "Evidence is stale or repeated without a material update.",
            factors,
        )
    if price_status == "contradicted":
        return ResearchConfidence(
            "low",
            "Source evidence exists, but current price action contradicts the implied direction.",
            factors,
        )
    # Standard high-confidence: strong source + fresh + portfolio-relevant.
    _standard_high = (
        strong_source
        and fresh
        and (corroboration >= 0.65 or event.primary_sources)
        and confidence >= 0.75
        and relevance >= 0.55
        and price_status != "contradicted"
    )
    # Macro/global relevance edge: official/high-quality sources on rates,
    # geopolitical, or regulatory events are market-moving regardless of
    # direct portfolio relevance, so allow high confidence even when
    # portfolio_relevance < 0.55 if macro or geo signal is strong.
    _macro_channel = str(event.causal_channel or "").lower() in {"rates", "geopolitical", "regulatory"}
    _macro_high = (
        _macro_channel
        and strong_source
        and fresh
        and confidence >= 0.70
        and (float(event.macro_relevance or 0.0) >= 0.60 or float(event.geo_relevance or 0.0) >= 0.55)
        and price_status != "contradicted"
    )
    if _standard_high or _macro_high:
        reason = (
            "Fresh official/macro-channel evidence with strong market relevance and no contradiction."
            if (_macro_high and not _standard_high)
            else "Fresh high-quality evidence with sufficient portfolio/watchlist relevance and no contradiction."
        )
        return ResearchConfidence("high", reason, factors)
    if decent_source and (fresh or material_update) and relevance >= 0.35:
        detail = "price confirmation pending" if price_status == "pending" else "partial corroboration or interpretation required"
        return ResearchConfidence(
            "medium",
            f"Relevant fresh evidence exists, but {detail}.",
            factors,
        )
    return ResearchConfidence(
        "low",
        "Evidence is weak, only partially relevant, insufficiently corroborated, or unclear in market impact.",
        factors,
    )

