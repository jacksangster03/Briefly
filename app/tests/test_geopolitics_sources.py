from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.verticals.sources.geopolitics import collect_geopolitics_events
from app.verticals.scoring import deterministic_vertical_score


def test_gdelt_adapter_normalises_events(monkeypatch):
    class _FakeProvider:
        def __init__(self, *args, **kwargs):
            pass

        def get_market_news(self, *, query: str, max_records: int = 50):
            evt = NormalisedEvent(
                source="gdelt",
                title="New sanctions on shipping routes",
                summary="Shipping corridor risk rises",
                url="https://example.com/geo",
                published_at=datetime.now(timezone.utc),
                tickers=["XLE"],
            )
            return [evt]

    monkeypatch.setattr("app.verticals.sources.geopolitics.GDELTProvider", _FakeProvider)
    settings = Settings(enable_gdelt=True)
    events, health = collect_geopolitics_events(settings=settings, watchlist=["XLE"])
    assert len(events) == 1
    assert events[0].event_type == "sanctions"
    assert health["gdelt"]["status"] == "ok"


def test_geopolitics_api_failure_returns_unavailable_diagnostics(monkeypatch):
    class _FailProvider:
        def __init__(self, *args, **kwargs):
            pass

        def get_market_news(self, *, query: str, max_records: int = 50):
            raise RuntimeError("gdelt down")

    monkeypatch.setattr("app.verticals.sources.geopolitics.GDELTProvider", _FailProvider)
    settings = Settings(enable_gdelt=True)
    events, health = collect_geopolitics_events(settings=settings)
    assert events == []
    assert health["gdelt"]["status"] == "error"


def test_headline_density_not_market_confirmation():
    from app.verticals.events import VerticalEvent

    event = VerticalEvent(
        vertical="geopolitics",
        source_name="gdelt",
        source_tier="primary",
        title="Shipping concern",
        diagnostics={"price_confirmation": 0.0, "freshness_score": 0.8},
        novelty_score=0.7,
        market_relevance=0.6,
        portfolio_relevance=0.2,
    )
    score = deterministic_vertical_score(event)
    assert score < 0.8


def test_official_tier_scores_higher_than_broad_media():
    from app.verticals.events import VerticalEvent

    common = dict(
        vertical="geopolitics",
        title="Same event",
        novelty_score=0.6,
        market_relevance=0.6,
        portfolio_relevance=0.4,
        diagnostics={"freshness_score": 0.8, "price_confirmation": 0.2},
    )
    official = VerticalEvent(source_name="official_feed", source_tier="official", **common)
    broad = VerticalEvent(source_name="random_media", source_tier="broad_media", **common)
    assert deterministic_vertical_score(official) > deterministic_vertical_score(broad)
