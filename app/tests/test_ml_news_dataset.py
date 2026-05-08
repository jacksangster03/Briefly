from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.models import NewsClassifierLabel
from app.db.session import get_session
from app.main import _persist_scheduler_classifier_labels
from app.ml.news_dataset import (
    build_news_label_row,
    dedupe_news_label_payload_rows,
    dedupe_label_rows,
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


def test_news_dataset_export_dedupe_headlines(validation_isolated_db, tmp_path):
    e1 = _event()
    e1.event_id = "evt-dup-1"
    e1.title = "Same story headline"
    e2 = _event()
    e2.event_id = "evt-dup-2"
    e2.title = "Same story headline"
    r1 = build_news_label_row(e1, session_key="morning", local_date=date(2026, 5, 8))
    r2 = build_news_label_row(e2, session_key="us_pre_open", local_date=date(2026, 5, 8))
    with get_session() as db:
        upsert_news_label_rows(db, [r1, r2])
        out = export_news_labels(
            db,
            tmp_path / "news_labels_dedupe.csv",
            format="csv",
            dedupe_headlines=True,
        )
    text = out.read_text(encoding="utf-8")
    assert text.count("Same story headline") == 1
    assert "sessions_seen" in text


def test_dedupe_label_rows_prefers_most_recent(validation_isolated_db):
    r1 = build_news_label_row(_event(), session_key="morning", local_date=date(2026, 5, 8))
    r2_evt = _event()
    r2_evt.event_id = "evt-43"
    r2_evt.title = r1["headline"]
    r2 = build_news_label_row(r2_evt, session_key="us_intraday_risk", local_date=date(2026, 5, 8))
    with get_session() as db:
        upsert_news_label_rows(db, [r1, r2])
        rows = db.query(NewsClassifierLabel).all()
        deduped = dedupe_label_rows(rows)
    assert len(deduped) == 1
    row = deduped[0]
    assert getattr(row, "sessions_seen", 0) == 2


def test_dedupe_news_label_payload_rows_collapses_by_headline():
    base = build_news_label_row(_event(), session_key="morning", local_date=date(2026, 5, 8))
    dup = dict(base)
    dup["event_id"] = "evt-43"
    payload = dedupe_news_label_payload_rows([base, dup])
    assert len(payload) == 1


def test_scheduler_label_persistence_is_deduped(validation_isolated_db):
    e1 = _event()
    e1.event_id = "evt-dupe-1"
    e1.title = "Same title"
    e2 = _event()
    e2.event_id = "evt-dupe-2"
    e2.title = "Same title"

    class _Brief:
        global_news = [e1, e2]
        top_themes = []
        watchlist_events = []
        portfolio_focus = []
        events_pool = [e1, e2]

    persisted = _persist_scheduler_classifier_labels(
        briefing=_Brief(),
        session_key="morning",
        local_date=date(2026, 5, 8),
    )
    assert persisted == 1
    with get_session() as db:
        rows = db.query(NewsClassifierLabel).all()
        assert len(rows) == 1
