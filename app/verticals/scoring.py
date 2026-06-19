"""Deterministic scoring rules for vertical source events."""

from __future__ import annotations

from dataclasses import dataclass

from app.verticals.events import VerticalEvent

_SOURCE_TIER_WEIGHT = {
    "official": 1.0,
    "primary": 0.9,
    "trusted_media": 0.75,
    "broad_media": 0.55,
    "social_optional": 0.35,
}


@dataclass(frozen=True)
class VerticalScoreWeights:
    source_tier_weight: float = 0.25
    freshness_weight: float = 0.15
    novelty_weight: float = 0.20
    portfolio_relevance_weight: float = 0.20
    market_relevance_weight: float = 0.15
    price_confirmation_weight: float = 0.05


def deterministic_vertical_score(
    event: VerticalEvent,
    *,
    weights: VerticalScoreWeights = VerticalScoreWeights(),
) -> float:
    """Deterministic score in [0,1]; no LLM contribution."""
    tier = _SOURCE_TIER_WEIGHT.get(str(event.source_tier or "").lower(), 0.5)
    novelty = max(0.0, min(1.0, float(event.novelty_score or 0.0)))
    portfolio_rel = max(0.0, min(1.0, float(event.portfolio_relevance or 0.0)))
    market_rel = max(0.0, min(1.0, float(event.market_relevance or 0.0)))
    freshness = max(0.0, min(1.0, float((event.diagnostics or {}).get("freshness_score", 0.5))))
    price_confirmation = max(0.0, min(1.0, float((event.diagnostics or {}).get("price_confirmation", 0.0))))
    score = (
        tier * weights.source_tier_weight
        + freshness * weights.freshness_weight
        + novelty * weights.novelty_weight
        + portfolio_rel * weights.portfolio_relevance_weight
        + market_rel * weights.market_relevance_weight
        + price_confirmation * weights.price_confirmation_weight
    )
    return max(0.0, min(1.0, score))


def rank_vertical_events(events: list[VerticalEvent]) -> list[VerticalEvent]:
    """Stable deterministic ranking by computed score and recency proxy."""
    scored = []
    for event in events:
        e = event.model_copy(deep=True)
        diagnostics = dict(e.diagnostics or {})
        diagnostics["deterministic_score"] = deterministic_vertical_score(e)
        e.diagnostics = diagnostics
        scored.append(e)
    return sorted(
        scored,
        key=lambda item: (
            float((item.diagnostics or {}).get("deterministic_score", 0.0)),
            float(item.market_relevance or 0.0),
            float(item.portfolio_relevance or 0.0),
        ),
        reverse=True,
    )
