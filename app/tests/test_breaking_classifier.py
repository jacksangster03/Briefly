"""Tests for deterministic breaking-classification logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.briefing.breaking_classifier import (
    build_breaking_storyline_key,
    classify_breaking_event,
)
from app.schemas.events import NormalisedEvent


def _event(
    *,
    title: str,
    summary: str = "",
    event_type: str = "headline",
    final_score: float = 0.85,
    factual_confidence_score: float = 0.82,
    novelty_score: float = 0.9,
    cluster_size: int = 4,
    tickers: list[str] | None = None,
    sectors: list[str] | None = None,
    published_minutes_ago: int = 20,
) -> NormalisedEvent:
    return NormalisedEvent(
        title=title,
        summary=summary,
        event_type=event_type,
        final_score=final_score,
        factual_confidence_score=factual_confidence_score,
        novelty_score=novelty_score,
        cluster_size=cluster_size,
        tickers=tickers or [],
        sectors=sectors or [],
        published_at=datetime.now(timezone.utc) - timedelta(minutes=published_minutes_ago),
    )


def test_geopolitical_catalyst_classifies_as_breaking():
    evt = _event(
        title="Kremlin says US rejected proposal on Iranian uranium stocks",
        summary="Negotiations over Iran nuclear stockpiles stall as diplomacy tensions rise.",
        event_type="geopolitical",
    )
    result = classify_breaking_event(evt)
    assert result.tier == "breaking"
    assert result.category == "geopolitics"
    assert result.impact_score >= 4
    assert any("WTI" in asset for asset in result.watch_assets)


def test_clickbait_company_headline_is_ignored():
    evt = _event(
        title="2.5 Billion Reasons Apple Might Be the Best AI Stock to Buy Today",
        summary="Analysts say valuation is attractive for long-term buyers.",
        event_type="headline",
        tickers=["AAPL"],
        sectors=["technology"],
    )
    result = classify_breaking_event(evt)
    assert result.tier == "ignore"


def test_storyline_key_stable_for_near_duplicate_geopolitical_headlines():
    a = _event(
        title="Pakistan says no dates set for second round of US-Iran talks",
        summary="Diplomatic process remains uncertain amid nuclear dispute.",
        event_type="geopolitical",
    )
    b = _event(
        title="No date set for next US Iran talks, Pakistan official says",
        summary="Officials say second negotiation round remains unscheduled.",
        event_type="geopolitical",
    )
    key_a = build_breaking_storyline_key(a, category="geopolitics")
    key_b = build_breaking_storyline_key(b, category="geopolitics")
    assert key_a == key_b


def test_single_name_material_earnings_is_not_auto_breaking():
    evt = _event(
        title="Starbucks launches beta app in ChatGPT to fuel new drink discovery",
        summary="Company said launch aims to improve customer engagement in US stores.",
        event_type="company_news",
        tickers=["SBUX"],
        sectors=["consumer_discretionary"],
        cluster_size=2,
        factual_confidence_score=0.75,
    )
    result = classify_breaking_event(evt)
    assert result.tier in {"high_priority", "regular", "ignore"}
