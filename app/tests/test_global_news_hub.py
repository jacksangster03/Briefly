"""Tests for Phase 4.5 global news hub aggregation behavior."""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.global_news_hub import GlobalNewsHubService
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def _isolated_settings(**overrides) -> Settings:
    base = dict(
        enable_gdelt=False,
        enable_alpha_vantage_news=False,
        enable_fmp_news=False,
        enable_mediastack_news=False,
        enable_marketaux_news=False,
    )
    base.update(overrides)
    return Settings(**base)


class _StubFinnhub:
    def __init__(self, events: list[NormalisedEvent]):
        self.events = events
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def get_market_news(self):
        self.calls += 1
        return list(self.events)


class _StubNewsAPI:
    def __init__(self, events: list[NormalisedEvent]):
        self.events = events
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def get_top_headlines(self):
        self.calls += 1
        return list(self.events)


class _StubProvider:
    def __init__(self, events: list[NormalisedEvent], configured: bool = True):
        self.events = events
        self.calls = 0
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def get_market_news(self, **kwargs):
        self.calls += 1
        return list(self.events)


def _event(
    *,
    source: str,
    title: str,
    url: str,
    summary: str = "",
    factual_confidence_score: float = 0.6,
) -> NormalisedEvent:
    evt = NormalisedEvent(
        source=source,
        source_type="news",
        published_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
        title=title,
        summary=summary,
        url=url,
        event_type="market_news",
        factual_confidence_score=factual_confidence_score,
    )
    evt.compute_hash()
    return evt


def test_global_news_hub_merges_sources_and_dedupes_story_fingerprint():
    shared_title = "Oil tops $100 as blockade risks rise in Hormuz"
    finnhub_event = _event(
        source="finnhub",
        title=shared_title,
        url="https://example.com/story?id=123&utm_source=foo",
        summary="Initial report",
        factual_confidence_score=0.55,
    )
    newsapi_event = _event(
        source="newsapi",
        title=shared_title,
        url="https://example.com/story?id=456&utm_source=bar",
        summary="Corroborating report with more detail",
        factual_confidence_score=0.75,
    )
    unique_event = _event(
        source="newsapi",
        title="ECB official signals caution on near-term cuts",
        url="https://eu.example.net/ecb-cuts?ref=tracker",
        summary="Separate story",
        factual_confidence_score=0.7,
    )

    hub = GlobalNewsHubService(
        _isolated_settings(),
        finnhub=_StubFinnhub([finnhub_event]),
        newsapi=_StubNewsAPI([newsapi_event, unique_event]),
    )
    merged = hub.fetch_market_news()

    assert len(merged) == 2
    assert hub.last_run_stats["duplicates_suppressed"] == 1
    titles = {evt.title for evt in merged}
    assert shared_title in titles
    assert "ECB official signals caution on near-term cuts" in titles

    shared_selected = next(evt for evt in merged if evt.title == shared_title)
    assert shared_selected.source == "newsapi"
    assert shared_selected.raw_data["canonical_url"] == "https://example.com/story"
    assert shared_selected.raw_data["domain"] == "example.com"
    assert shared_selected.raw_data["story_fingerprint"]


def test_global_news_hub_respects_daily_budget_guards():
    GlobalNewsHubService._daily_usage.clear()
    settings = _isolated_settings(
        enable_gdelt=True,
        gdelt_daily_call_budget=1,
    )
    gdelt = _StubProvider(
        [
            _event(
                source="gdelt",
                title="Tariff escalations pressure shipping routes",
                url="https://gdelt.example/tariffs",
            )
        ]
    )
    hub = GlobalNewsHubService(
        settings,
        finnhub=None,
        newsapi=None,
        gdelt=gdelt,  # type: ignore[arg-type]
    )

    first = hub.fetch_market_news()
    second = hub.fetch_market_news()

    assert len(first) == 1
    assert second == []
    assert gdelt.calls == 1


def test_global_news_hub_canonicalizes_provider_metadata():
    event = _event(
        source="finnhub",
        title="Fed officials debate pace of rate cuts",
        url="https://www.sample.org/path/article?utm_campaign=foo#section",
        summary="Policy debate continues",
    )
    event.raw_data = {"id": "abc123"}
    hub = GlobalNewsHubService(
        _isolated_settings(),
        finnhub=_StubFinnhub([event]),
        newsapi=_StubNewsAPI([]),
    )
    merged = hub.fetch_market_news()
    assert len(merged) == 1
    enriched = merged[0]
    assert enriched.url == "https://www.sample.org/path/article"
    assert enriched.raw_data["canonical_url"] == "https://www.sample.org/path/article"
    assert enriched.raw_data["domain"] == "sample.org"
    assert enriched.raw_data["provider_event_id"] == "abc123"


def test_global_news_hub_includes_marketaux_when_enabled():
    GlobalNewsHubService._daily_usage.clear()
    settings = _isolated_settings(
        enable_marketaux_news=True,
        marketaux_api_key="k",
        marketaux_news_daily_call_budget=2,
        marketaux_news_limit=5,
    )
    marketaux = _StubProvider(
        [
            _event(
                source="marketaux",
                title="Dollar strength pressures EM importers",
                url="https://marketaux.example/dollar-em",
            )
        ]
    )
    hub = GlobalNewsHubService(
        settings,
        finnhub=None,
        newsapi=None,
        marketaux=marketaux,  # type: ignore[arg-type]
    )

    merged = hub.fetch_market_news()
    assert len(merged) == 1
    assert marketaux.calls == 1
    assert hub.last_run_stats["provider_contributions"]["marketaux"] == 1
