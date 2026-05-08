from __future__ import annotations

from app.ml.news_classifier_shadow import (
    classify_news_event_shadow,
    compare_deterministic_vs_ml,
    ml_news_classifier_enabled,
)
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def _event() -> NormalisedEvent:
    evt = NormalisedEvent(
        event_id="evt-1",
        title="Headline",
        summary="Summary",
        event_type="news_search",
        final_score=0.8,
        factual_confidence_score=0.75,
        update_status="new",
        tickers=["AAPL"],
        raw_data={
            "news_story_type": "commentary_valuation",
            "news_suppress_reason": "valuation_commentary_without_catalyst",
            "news_breaking_eligible": False,
            "news_classifier_confidence": 0.7,
        },
    )
    return evt


def test_ml_disabled_means_no_inference():
    s = Settings(enable_ml_news_classifier=False)
    assert ml_news_classifier_enabled(s) is False
    assert classify_news_event_shadow(_event(), s) is None


def test_missing_model_path_is_graceful_skip():
    s = Settings(enable_ml_news_classifier=True, ml_news_classifier_shadow_mode=True, ml_news_classifier_model_path="")
    out = classify_news_event_shadow(_event(), s)
    assert out is not None
    assert out.unavailable_reason == "missing_model_path"


def test_invalid_model_path_is_graceful_skip():
    s = Settings(enable_ml_news_classifier=True, ml_news_classifier_shadow_mode=True, ml_news_classifier_model_path="/no/such/model.pkl")
    out = classify_news_event_shadow(_event(), s)
    assert out is not None
    assert out.unavailable_reason == "model_path_not_found"


def test_shadow_compare_does_not_mutate_deterministic_fields():
    evt = _event()
    before = evt.model_dump()
    s = Settings(enable_ml_news_classifier=False)
    cmp = compare_deterministic_vs_ml(evt, classify_news_event_shadow(evt, s))
    assert cmp["agreement"] is None
    assert evt.model_dump() == before
