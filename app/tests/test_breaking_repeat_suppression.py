"""Regression tests for breaking-alert repeat suppression."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.processing.dedupe import classify_against_sent_history
from app.processing.event_store import record_sent_events
from app.schemas.events import NormalisedEvent


def _make_event(
    *,
    source: str,
    event_type: str,
    title: str,
    cluster_id: str | None = None,
) -> NormalisedEvent:
    event = NormalisedEvent(
        source=source,
        source_type="news",
        title=title,
        summary="Iran war developments reshape oil and inflation expectations.",
        event_type=event_type,
        published_at=datetime.now(timezone.utc),
        cluster_id=cluster_id,
    )
    event.compute_hash()
    return event


def test_classify_same_headline_across_news_types_is_duplicate(tmp_path):
    """Same headline from a different provider/type should not re-alert."""
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "repeat_breaking.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        sent = _make_event(
            source="finnhub",
            event_type="market_news",
            title="Britain's Tesco to shine light on inflation risks from Iran war",
            cluster_id="macro_iran_1",
        )
        record_sent_events([sent])

        incoming = _make_event(
            source="newsapi",
            event_type="headline",
            title="Britain's Tesco to shine light on inflation risks from Iran war",
            cluster_id="different_cluster",
        )
        classified = classify_against_sent_history([incoming], lookback_hours=24)[0]
        assert classified.already_sent is True
        assert classified.update_status == "duplicate"
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()
