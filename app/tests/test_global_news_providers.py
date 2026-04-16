"""Adapter tests for Phase 4.5 global-news providers."""

from __future__ import annotations

from app.data_sources.providers.alphavantage_news import AlphaVantageNewsProvider
from app.data_sources.providers.fmp_news import FMPNewsProvider
from app.data_sources.providers.gdelt import GDELTProvider
from app.data_sources.providers.mediastack import MediastackProvider


def test_gdelt_provider_normalizes_articles():
    provider = GDELTProvider(base_url="https://api.gdeltproject.org/api/v2/doc/doc")
    provider._get = lambda *args, **kwargs: {  # type: ignore[assignment]
        "articles": [
            {
                "title": "Oil shipping risks rise after blockade chatter",
                "url": "https://news.example/oil-shipping",
                "seendate": "20260416T120501Z",
                "domain": "news.example",
                "sourcecountry": "US",
            }
        ]
    }
    events = provider.get_market_news(query="oil", max_records=20)
    assert len(events) == 1
    assert events[0].source == "gdelt"
    assert events[0].event_type == "global_news"
    assert events[0].url == "https://news.example/oil-shipping"
    assert events[0].raw_data["source_name"] == "news.example"


def test_alpha_vantage_provider_normalizes_feed():
    provider = AlphaVantageNewsProvider(
        api_key="k",
        base_url="https://www.alphavantage.co/query",
    )
    provider._get = lambda *args, **kwargs: {  # type: ignore[assignment]
        "feed": [
            {
                "title": "Fed officials split on near-term rate cuts",
                "url": "https://alpha.example/fed",
                "summary": "Macro policy split remains in focus.",
                "time_published": "20260416T080000",
                "source": "ExampleWire",
                "overall_sentiment_score": "0.14",
                "ticker_sentiment": [{"ticker": "SPY"}, {"ticker": "TLT"}],
            }
        ]
    }
    events = provider.get_market_news(topics="economy_macro", limit=10)
    assert len(events) == 1
    assert events[0].source == "alpha_vantage"
    assert events[0].tickers == ["SPY", "TLT"]
    assert events[0].sentiment == 0.14
    assert events[0].raw_data["source_name"] == "ExampleWire"


def test_fmp_provider_normalizes_rows():
    provider = FMPNewsProvider(
        api_key="k",
        base_url="https://financialmodelingprep.com/stable/news/general-latest",
    )
    provider._get = lambda *args, **kwargs: [  # type: ignore[assignment]
        {
            "title": "Treasury yields rise as inflation worries persist",
            "url": "https://fmp.example/yields",
            "text": "Bond markets reprice growth and policy path.",
            "publishedDate": "2026-04-16 09:20:00",
            "site": "FMP Wire",
            "symbol": "TLT",
        }
    ]
    events = provider.get_market_news(limit=10)
    assert len(events) == 1
    assert events[0].source == "fmp"
    assert events[0].tickers == ["TLT"]
    assert events[0].raw_data["source_name"] == "FMP Wire"


def test_mediastack_provider_normalizes_rows():
    provider = MediastackProvider(
        api_key="k",
        base_url="https://api.mediastack.com/v1/news",
    )
    provider._get = lambda *args, **kwargs: {  # type: ignore[assignment]
        "data": [
            {
                "title": "Sanctions pressure cross-border energy trade",
                "url": "https://media.example/sanctions",
                "description": "Global trade routes adapt to restrictions.",
                "published_at": "2026-04-16T11:00:00+00:00",
                "source": "Media Stack Source",
                "country": "us",
            }
        ]
    }
    events = provider.get_market_news(limit=10)
    assert len(events) == 1
    assert events[0].source == "mediastack"
    assert events[0].event_type == "headline"
    assert events[0].raw_data["source_name"] == "Media Stack Source"
