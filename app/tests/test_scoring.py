"""Tests for the relevance scoring engine."""

import pytest

from app.personalization.user_profile import UserProfile
from app.processing.relevance_scoring import score_event, score_events
from app.schemas.events import NormalisedEvent


def _make_profile() -> UserProfile:
    return UserProfile(
        sector_weights={
            "technology": 1.0,
            "semiconductors": 0.95,
            "healthcare": 0.8,
            "energy": 0.4,
        },
        coverage_weights={"us": 1.0, "europe": 0.7, "asia": 0.35},
        watchlist_primary=["NVDA", "AAPL", "LLY"],
        watchlist_secondary=["AMD", "MSFT"],
    )


class TestScoring:
    def test_watchlist_primary_boosts_score(self):
        profile = _make_profile()
        evt = NormalisedEvent(
            title="NVDA announces new GPU architecture",
            source="finnhub",
            tickers=["NVDA"],
            sectors=["technology", "semiconductors"],
            event_type="company_news",
            factual_confidence_score=0.75,
        )
        scored = score_event(evt, profile)
        assert scored.personal_relevance_score == 1.0
        assert scored.final_score > 0.5

    def test_low_relevance_scores_lower(self):
        profile = _make_profile()
        high_evt = NormalisedEvent(
            title="NVDA announces major GPU architecture",
            source="sec_edgar",
            tickers=["NVDA"],
            sectors=["semiconductors"],
            event_type="current_report",
            factual_confidence_score=0.95,
        )
        low_evt = NormalisedEvent(
            title="Random company reports flat earnings",
            source="newsapi",
            tickers=["XYZ"],
            sectors=["energy"],
            event_type="headline",
            factual_confidence_score=0.55,
        )
        scored_high = score_event(high_evt, profile)
        scored_low = score_event(low_evt, profile)
        assert scored_low.final_score < scored_high.final_score

    def test_sec_filing_high_confidence(self):
        profile = _make_profile()
        evt = NormalisedEvent(
            title="8-K: Apple Inc",
            source="sec_edgar",
            tickers=["AAPL"],
            sectors=["technology"],
            event_type="current_report",
            factual_confidence_score=0.95,
        )
        scored = score_event(evt, profile)
        assert scored.attention_score >= 0.9  # SEC = highest credibility

    def test_score_explanation_populated(self):
        profile = _make_profile()
        evt = NormalisedEvent(
            title="Test event",
            source="finnhub",
            event_type="market_news",
        )
        scored = score_event(evt, profile)
        assert scored.score_explanation
        assert "FINAL=" in scored.score_explanation

    def test_score_events_sorts_descending(self):
        profile = _make_profile()
        events = [
            NormalisedEvent(title="Low", source="newsapi", event_type="headline",
                            factual_confidence_score=0.3),
            NormalisedEvent(title="High", source="sec_edgar", tickers=["NVDA"],
                            sectors=["semiconductors"], event_type="current_report",
                            factual_confidence_score=0.95),
        ]
        scored = score_events(events, profile)
        assert scored[0].title == "High"
        assert scored[0].final_score >= scored[1].final_score
