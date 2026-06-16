from __future__ import annotations

from app.processing.research_event_builder import (
    build_research_event,
    resolve_source_tier_and_quality,
)
from app.schemas.events import NormalisedEvent, QuoteData


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
