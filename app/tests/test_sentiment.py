"""Tests for FinBERT sentiment integration."""

from unittest.mock import patch

from app.personalization.user_profile import UserProfile
from app.processing.relevance_scoring import score_event
from app.processing.sentiment import SentimentResult, compute_sentiment
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def _profile() -> UserProfile:
    return UserProfile(
        sector_weights={"technology": 1.0},
        coverage_weights={"us": 1.0},
        watchlist_primary=["AAPL"],
        watchlist_secondary=[],
    )


class TestSentimentModule:
    def test_compute_sentiment_disabled_returns_disabled_label(self):
        with patch("app.processing.sentiment.get_settings", return_value=Settings(enable_finbert=False)):
            result = compute_sentiment("Apple beats earnings estimates")
        assert result.label == "disabled"
        assert result.score == 0.0


class TestScoringSentimentAdjustment:
    def test_positive_sentiment_increases_score(self):
        event = NormalisedEvent(
            title="Apple raises guidance",
            summary="Strong demand and margin expansion",
            source="newsapi",
            event_type="headline",
            factual_confidence_score=0.7,
        )
        profile = _profile()

        with patch(
            "app.processing.relevance_scoring.compute_sentiment",
            return_value=SentimentResult(score=0.9, label="positive"),
        ):
            scored = score_event(event, profile)
        assert scored.sentiment > 0
        assert scored.raw_data["sentiment_label"] == "positive"
        assert scored.final_score > 0

    def test_negative_sentiment_decreases_score(self):
        event = NormalisedEvent(
            title="Apple cuts guidance",
            summary="Weak demand expected next quarter",
            source="newsapi",
            event_type="headline",
            factual_confidence_score=0.7,
        )
        profile = _profile()

        with patch(
            "app.processing.relevance_scoring.compute_sentiment",
            return_value=SentimentResult(score=-0.9, label="negative"),
        ):
            scored_negative = score_event(event, profile)

        with patch(
            "app.processing.relevance_scoring.compute_sentiment",
            return_value=SentimentResult(score=0.0, label="neutral"),
        ):
            scored_neutral = score_event(
                NormalisedEvent(
                    title=event.title,
                    summary=event.summary,
                    source=event.source,
                    event_type=event.event_type,
                    factual_confidence_score=event.factual_confidence_score,
                ),
                profile,
            )

        assert scored_negative.final_score < scored_neutral.final_score
