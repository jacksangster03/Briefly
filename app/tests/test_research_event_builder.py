from __future__ import annotations

import pytest

from app.processing.research_event_builder import (
    build_research_event,
    composite_research_score,
    resolve_source_tier_and_quality,
)
from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import ResearchEvent


def test_resolve_source_tier_and_quality_uses_provider_tier_baseline():
    event = NormalisedEvent(source="fred", title="CPI release")
    tier, quality = resolve_source_tier_and_quality(event)
    assert tier == "high"
    assert quality == 0.80


def test_resolve_source_tier_and_quality_prefers_higher_factual_confidence():
    event = NormalisedEvent(source="newsapi", title="x", factual_confidence_score=0.92)
    tier, quality = resolve_source_tier_and_quality(event)
    assert tier == "medium"
    assert quality == 0.92  # factual_confidence_score wins over the medium baseline (0.55)


def test_build_research_event_sets_source_tier_and_causal_channel():
    event = NormalisedEvent(
        source="sec_edgar",
        title="Fed signals fewer rate cuts",
        event_type="fed_decision",
        factual_confidence_score=0.95,
        cluster_size=5,
    )
    research_event = build_research_event(event)
    assert research_event.source_tier == "highest"
    assert research_event.causal_channel == "rates"
    assert research_event.interpretation  # templated, non-empty
    assert research_event.corroboration_score == 1.0  # max confidence + max cluster


def test_build_research_event_applies_portfolio_tag_bonus():
    event = NormalisedEvent(
        title="Direct portfolio catalyst",
        portfolio_tag="DIRECT",
        personal_relevance_score=0.5,
    )
    research_event = build_research_event(event)
    assert research_event.portfolio_relevance_score == 0.5 + 0.40


def test_build_research_event_applies_watchlist_overlap_bonus():
    event = NormalisedEvent(title="NVDA news", tickers=["NVDA"])
    research_event = build_research_event(event, preferred_symbols={"NVDA"})
    assert research_event.watchlist_relevance_score == 1.0


def test_build_research_event_no_watchlist_overlap_gives_zero_bonus():
    event = NormalisedEvent(title="NVDA news", tickers=["NVDA"])
    research_event = build_research_event(event, preferred_symbols={"AMD"})
    assert research_event.watchlist_relevance_score == 0.0


def test_build_research_event_applies_price_confirmation_when_quotes_supplied():
    event = NormalisedEvent(
        title="Nvidia raises guidance",
        tickers=["NVDA"],
        sentiment=0.6,
    )
    quotes = {"NVDA": QuoteData(symbol="NVDA", current_price=100.0, change_percent=3.0)}
    research_event = build_research_event(event, quotes_by_symbol=quotes)
    assert research_event.price_confirmation_status == "confirmed"


def test_build_research_event_skips_price_confirmation_without_quotes():
    event = NormalisedEvent(title="Nvidia raises guidance", tickers=["NVDA"], sentiment=0.6)
    research_event = build_research_event(event)
    assert research_event.price_confirmation_status == "unavailable"
    assert research_event.price_confirmation_detail == ""


# -- composite_research_score tests ------------------------------------------


def test_composite_research_score_baseline_from_source_quality():
    # All non-source_quality_score inputs at zero except confidence (default 0.5).
    re = ResearchEvent(
        source_quality_score=0.80,
        causal_channel="other",
        confidence=0.0,  # isolate source_quality contribution
        novelty_score=0.0,
        corroboration_score=0.0,
        portfolio_relevance_score=0.0,
        watchlist_relevance_score=0.0,
    )
    score = composite_research_score(re)
    assert score == pytest.approx(0.80, abs=1e-9)


def test_composite_research_score_confirmed_price_adds_bonus():
    re = ResearchEvent(
        source_quality_score=0.60,
        causal_channel="other",
        price_confirmation_status="confirmed",
    )
    score = composite_research_score(re)
    assert score > 0.60 + 0.30 - 1e-9  # at minimum source_quality + confirmed bonus


def test_composite_research_score_earnings_channel_adds_double_bonus():
    base = ResearchEvent(source_quality_score=0.50, causal_channel="other")
    earnings = ResearchEvent(source_quality_score=0.50, causal_channel="earnings")
    assert composite_research_score(earnings) > composite_research_score(base) + 0.29


def test_composite_research_score_high_source_tier_adds_bonus():
    low = ResearchEvent(source_quality_score=0.50, source_tier="medium", causal_channel="other")
    high = ResearchEvent(source_quality_score=0.50, source_tier="highest", causal_channel="other")
    assert composite_research_score(high) > composite_research_score(low) + 0.24


def test_composite_research_score_portfolio_relevance_weighted_highest():
    base = ResearchEvent(source_quality_score=0.50, causal_channel="other")
    with_portfolio = ResearchEvent(
        source_quality_score=0.50, causal_channel="other", portfolio_relevance_score=1.0
    )
    assert composite_research_score(with_portfolio) - composite_research_score(base) == pytest.approx(0.60, abs=1e-9)


def test_composite_research_score_ordering_matches_analyst_priority():
    confirmed_earnings = ResearchEvent(
        source_quality_score=0.70,
        causal_channel="earnings",
        price_confirmation_status="confirmed",
        corroboration_score=0.80,
        portfolio_relevance_score=0.90,
    )
    low_signal = ResearchEvent(
        source_quality_score=0.30,
        causal_channel="other",
        price_confirmation_status="unavailable",
    )
    assert composite_research_score(confirmed_earnings) > composite_research_score(low_signal)
