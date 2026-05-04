"""Tests for Phase 4.4 global market/geopolitics selectors."""

from __future__ import annotations

from app.briefing.global_news_selector import (
    build_market_relevance_note,
    select_global_market_events,
)
from app.briefing.intraday_generator import IntradayGenerator
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent
from app.schemas.events import QuoteData
from app.settings import Settings
from app.universe.sector_universe import InstrumentDef, SectorUniverse


def test_global_selector_accepts_market_linked_high_trust_events():
    events = [
        NormalisedEvent(
            title="Oil tops $100, dollar gains as US weighs blockade around Iran",
            summary="Shipping and insurance costs rise across the Strait of Hormuz.",
            event_type="geopolitical",
            factual_confidence_score=0.83,
            final_score=0.80,
            cluster_size=5,
            regions=["Middle East", "US"],
        ),
        NormalisedEvent(
            title="Top 5 stocks to buy if oil stays above $100",
            summary="Analysts share their favorite picks for this week.",
            event_type="headline",
            factual_confidence_score=0.60,
            final_score=0.84,
            cluster_size=2,
            regions=["US"],
        ),
    ]

    selected = select_global_market_events(
        events,
        session_mode="weekday",
        max_items=6,
        region_weights={"middle_east": 1.0, "us": 1.0},
    )

    titles = [evt.title for evt in selected]
    assert "Oil tops $100, dollar gains as US weighs blockade around Iran" in titles
    assert "Top 5 stocks to buy if oil stays above $100" not in titles


def test_global_selector_rejects_non_market_world_news():
    events = [
        NormalisedEvent(
            title="Celebrity wedding in Paris draws global audience",
            summary="A-list guests gather for a weekend event.",
            event_type="headline",
            factual_confidence_score=0.9,
            final_score=0.9,
            cluster_size=4,
            regions=["Europe"],
        )
    ]
    selected = select_global_market_events(
        events,
        session_mode="sunday",
        max_items=6,
    )
    assert selected == []


def test_global_selector_rejects_generic_etf_listicle():
    events = [
        NormalisedEvent(
            title="This High-Yield Emerging Funds ETF Offers International Diversification",
            summary="A look at an ETF for long-term investors.",
            event_type="headline",
            factual_confidence_score=0.88,
            final_score=0.85,
            cluster_size=3,
            regions=["Global Macro"],
        ),
        NormalisedEvent(
            title="US sanctions tighten tanker insurance in Gulf shipping lanes",
            summary="Oil and freight risk premium rise after policy action.",
            event_type="geopolitical",
            factual_confidence_score=0.86,
            final_score=0.81,
            cluster_size=4,
            regions=["Middle East", "US"],
        ),
    ]
    selected = select_global_market_events(
        events,
        session_mode="weekday",
        max_items=6,
    )
    titles = [evt.title for evt in selected]
    assert "This High-Yield Emerging Funds ETF Offers International Diversification" not in titles
    assert "US sanctions tighten tanker insurance in Gulf shipping lanes" in titles


def test_market_relevance_note_is_concise_and_present():
    event = NormalisedEvent(
        title="ECB and Fed rhetoric keeps rates path in focus",
        summary="Yields move as markets reprice the policy trajectory.",
        event_type="macro_release",
    )
    note = build_market_relevance_note(event)
    assert note.startswith("Why market-relevant:")
    assert "rates" in note.lower() or "inflation" in note.lower()


class _DummyIntradayNews:
    def __init__(self, events):
        self._events = events

    def fetch_market_news(self):
        return self._events


class _DummyIntradayMarket:
    def get_quotes(self, _symbols):
        return [QuoteData(symbol="SPY", current_price=500.0)]


def test_intraday_generator_includes_global_block_and_dedupes(monkeypatch):
    global_event = NormalisedEvent(
        title="Oil tops $100 as blockade risk rises around Iran",
        summary="Shipping costs jump as tanker traffic reprices.",
        event_type="geopolitical",
        factual_confidence_score=0.82,
        final_score=0.8,
        cluster_size=5,
        cluster_id="cluster-global-1",
        regions=["Middle East"],
    )
    regular_event = NormalisedEvent(
        title="Banks prepare for earnings week",
        summary="Trading desks stay active into results.",
        event_type="market_news",
        factual_confidence_score=0.72,
        final_score=0.72,
        cluster_size=2,
        cluster_id="cluster-regular-1",
    )

    monkeypatch.setattr(
        "app.briefing.intraday_generator.process_event_stream",
        lambda *_args, **_kwargs: [global_event, regular_event],
    )
    monkeypatch.setattr(
        "app.briefing.intraday_generator.select_intraday_events",
        lambda *_args, **_kwargs: [global_event, regular_event],
    )

    generator = IntradayGenerator(
        settings=Settings(),
        profile=UserProfile(delivery={"intraday_global_risk_enabled": True}),
        universe=SectorUniverse(
            sectors=[],
            indices=[InstrumentDef(symbol="SPY", display="S&P 500")],
            macro_instruments=[],
        ),
        market_data=_DummyIntradayMarket(),
        news_data=_DummyIntradayNews([global_event, regular_event]),
    )
    update = generator.generate(min_score=0.0, max_events=7)
    assert [evt.title for evt in update.global_risk_items] == [global_event.title]
    assert [evt.title for evt in update.new_events] == [regular_event.title]


def test_intraday_generator_hides_global_block_when_disabled(monkeypatch):
    event = NormalisedEvent(
        title="Oil tops $100 as blockade risk rises around Iran",
        summary="Shipping costs jump as tanker traffic reprices.",
        event_type="geopolitical",
        factual_confidence_score=0.82,
        final_score=0.8,
        cluster_size=5,
    )
    monkeypatch.setattr(
        "app.briefing.intraday_generator.process_event_stream",
        lambda *_args, **_kwargs: [event],
    )
    monkeypatch.setattr(
        "app.briefing.intraday_generator.select_intraday_events",
        lambda *_args, **_kwargs: [event],
    )

    generator = IntradayGenerator(
        settings=Settings(),
        profile=UserProfile(delivery={"intraday_global_risk_enabled": False}),
        universe=SectorUniverse(
            sectors=[],
            indices=[InstrumentDef(symbol="SPY", display="S&P 500")],
            macro_instruments=[],
        ),
        market_data=_DummyIntradayMarket(),
        news_data=_DummyIntradayNews([event]),
    )
    update = generator.generate(min_score=0.0, max_events=7)
    assert update.global_risk_items == []
    assert [evt.title for evt in update.new_events] == [event.title]
