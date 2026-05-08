from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.briefing.news_classifier import annotate_news_events, is_stale_breaking_candidate
from app.schemas.events import NormalisedEvent


def _event(**kwargs) -> NormalisedEvent:
    base = dict(
        title="Fed signals policy path as yields move higher",
        summary="Rates repricing drives cross-asset moves.",
        event_type="macro_release",
        final_score=0.9,
        factual_confidence_score=0.88,
        novelty_score=0.9,
        cluster_size=5,
        update_status="new",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        raw_data={},
    )
    base.update(kwargs)
    return NormalisedEvent(**base)


def test_late_discovery_rejected_from_breaking():
    evt = _event(
        published_at=datetime.now(timezone.utc) - timedelta(hours=18),
        raw_data={"first_seen_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()},
    )
    annotate_news_events([evt], breaking_max_age_hours=6)
    blocked, reason = is_stale_breaking_candidate(evt)
    assert blocked is True
    assert reason in {"late_discovery_old_published_time", "freshness_stale"}


def test_fresh_story_not_rejected_from_breaking():
    evt = _event()
    annotate_news_events([evt], breaking_max_age_hours=6)
    blocked, reason = is_stale_breaking_candidate(evt)
    assert blocked is False
    assert reason == ""


def test_breaking_labels_exposed():
    new_evt = _event()
    update_evt = _event(update_status="material_update")
    stale_evt = _event(published_at=datetime.now(timezone.utc) - timedelta(hours=30))
    annotate_news_events([new_evt, update_evt, stale_evt], breaking_max_age_hours=6)
    assert new_evt.raw_data.get("breaking_label") in {"BREAKING", "CONTEXT"}
    assert update_evt.raw_data.get("breaking_label") == "UPDATE"
    assert stale_evt.raw_data.get("breaking_label") in {"CONTEXT", "LATE DISCOVERY"}
