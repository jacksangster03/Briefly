from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.models import NewsClassifierLabel
from app.db.session import get_session
from app.ml.news_dataset import (
    build_news_label_row,
    export_news_labels,
    upsert_news_label_rows,
)
from app.schemas.events import NormalisedEvent


def _event() -> NormalisedEvent:
    return NormalisedEvent(
        event_id="evt-42",
        title="Fed holds rates steady",
        summary="No change at this meeting.",
        source="newsapi",
        url="https://example.com/story",
        event_type="macro_release",
        final_score=0.86,
        update_status="new",
        tickers=["SPY"],
        sectors=["macro"],
        published_at=datetime.now(timezone.utc),
        raw_data={
            "news_story_type": "macro_policy",
            "news_suppress_reason": "",
            "news_breaking_eligible": True,
            "news_freshness_state": "new",
        },
    )


def test_build_and_upsert_news_label_row(validation_isolated_db):
    row = build_news_label_row(
        _event(),
        session_key="morning",
        local_date=date(2026, 5, 8),
        included_in_briefing=True,
        sent_as_breaking=False,
    )
    with get_session() as db:
        n = upsert_news_label_rows(db, [row])
        assert n == 1
        saved = db.query(NewsClassifierLabel).filter(NewsClassifierLabel.event_id == "evt-42").first()
        assert saved is not None
        assert saved.deterministic_story_type == "macro_policy"
        assert saved.included_in_briefing is True


def test_news_dataset_export_csv(validation_isolated_db, tmp_path):
    row = build_news_label_row(
        _event(),
        session_key="morning",
        local_date=date(2026, 5, 8),
        included_in_briefing=True,
        sent_as_breaking=False,
    )
    with get_session() as db:
        upsert_news_label_rows(db, [row])
        out = export_news_labels(db, tmp_path / "news_labels.csv", format="csv")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "deterministic_story_type" in text
    assert "manual_story_type" in text
