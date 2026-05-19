"""Compatibility mapping between healthcare source events and shared vertical events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.healthcare.schemas import HealthcareSourceEvent
from app.verticals.events import VerticalEvent


def healthcare_source_to_vertical_event(event: HealthcareSourceEvent) -> VerticalEvent:
    return VerticalEvent(
        vertical="healthcare",
        source_name=event.source_key,
        source_tier="official" if str(event.source_tier or "").lower() == "official" else "primary",
        source_url=event.source_url,
        published_at=event.published_at,
        fetched_at=event.discovered_at or datetime.now(timezone.utc),
        title=event.title,
        summary=event.summary,
        entities=[event.company_name] if event.company_name else [],
        tickers=list(event.tickers or []),
        regions=["us", "eu"] if event.regulator in {"FDA", "EMA"} else [],
        countries=[],
        asset_classes=["equities", "healthcare"],
        event_type=event.healthcare_event_type or "healthcare_event",
        causal_channel="regulatory_catalyst",
        source_count=1,
        novelty_score=0.6,
        relevance_score=0.7,
        portfolio_relevance=0.5,
        market_relevance=0.6,
        confidence=float(event.confidence or 0.5),
        diagnostics={
            "regulator": event.regulator,
            "severity": event.severity,
            "freshness_state": event.freshness_state,
            "suppress_reason": event.suppress_reason,
        },
    ).with_computed_hash()
