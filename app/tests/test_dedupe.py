"""Tests for event deduplication engine."""

import pytest
from datetime import datetime, timezone, timedelta

from app.schemas.events import NormalisedEvent
from app.processing.dedupe import deduplicate_events, _cluster_by_ticker_time


def _make_event(
    title: str = "Test headline",
    source: str = "finnhub",
    tickers: list[str] | None = None,
    event_type: str = "market_news",
    published_at: datetime | None = None,
    final_score: float = 0.5,
) -> NormalisedEvent:
    evt = NormalisedEvent(
        source=source,
        source_type="news",
        title=title,
        tickers=tickers or [],
        event_type=event_type,
        published_at=published_at or datetime.now(timezone.utc),
        final_score=final_score,
    )
    evt.compute_hash()
    return evt


class TestExactDedup:
    def test_removes_exact_duplicates(self):
        e1 = _make_event("NVDA beats earnings estimates")
        e2 = _make_event("NVDA beats earnings estimates")
        result = deduplicate_events([e1, e2])
        assert len(result) == 1

    def test_keeps_different_headlines(self):
        e1 = _make_event("NVDA beats earnings estimates")
        e2 = _make_event("AMD reports strong Q4 revenue")
        result = deduplicate_events([e1, e2])
        assert len(result) == 2

    def test_empty_input(self):
        assert deduplicate_events([]) == []


class TestNormalisedDedup:
    def test_case_insensitive_match(self):
        e1 = _make_event("NVIDIA Beats Earnings", source="finnhub")
        e2 = _make_event("nvidia beats earnings", source="newsapi")
        result = deduplicate_events([e1, e2])
        assert len(result) == 1

    def test_punctuation_insensitive(self):
        e1 = _make_event("Apple's iPhone sales rise!")
        e2 = _make_event("Apples iPhone sales rise")
        result = deduplicate_events([e1, e2])
        assert len(result) == 1


class TestTickerTimeClustering:
    def test_clusters_same_ticker_same_window(self):
        now = datetime.now(timezone.utc)
        e1 = _make_event("AAPL up on earnings", tickers=["AAPL"],
                         event_type="earnings", published_at=now, final_score=0.8)
        e2 = _make_event("AAPL surges after results", tickers=["AAPL"],
                         event_type="earnings", published_at=now + timedelta(hours=1),
                         final_score=0.6)
        result = _cluster_by_ticker_time([e1, e2], window_hours=12)
        # Should keep only the higher-scored event
        assert len(result) == 1
        assert result[0].final_score == 0.8

    def test_different_tickers_not_clustered(self):
        now = datetime.now(timezone.utc)
        e1 = _make_event("AAPL up", tickers=["AAPL"], published_at=now)
        e2 = _make_event("MSFT up", tickers=["MSFT"], published_at=now)
        result = _cluster_by_ticker_time([e1, e2], window_hours=12)
        assert len(result) == 2

    def test_events_without_tickers_pass_through(self):
        e1 = _make_event("General market rally")
        e2 = _make_event("Fed signals rate hold")
        result = _cluster_by_ticker_time([e1, e2], window_hours=12)
        assert len(result) == 2


class TestFullPipeline:
    def test_dedup_reduces_count(self):
        events = [
            _make_event("Breaking: NVDA earnings beat", tickers=["NVDA"]),
            _make_event("Breaking: NVDA earnings beat", tickers=["NVDA"]),  # exact dup
            _make_event("breaking nvda earnings beat", tickers=["NVDA"]),   # normalised dup
            _make_event("AMD reports strong results", tickers=["AMD"]),     # different
        ]
        result = deduplicate_events(events)
        # 1 NVDA event + 1 AMD event
        assert len(result) == 2
