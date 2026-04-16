"""Regression tests for breaking-alert repeat suppression."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.db.models import NormalisedEvent as StoredEvent
from app.db.models import SentMessage
from app.db.session import get_session
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


def test_classify_uses_sent_message_tracking_ids(tmp_path):
    """If a sent message already carried the tracking ID, suppress re-alerts."""
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "repeat_breaking_tracking.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        event = _make_event(
            source="finnhub",
            event_type="market_news",
            title="Oil tops $100, dollar gains, stocks fall as US moves to blockade Iran",
            cluster_id="macro_iran_2",
        )
        with get_session() as session:
            session.add(
                SentMessage(
                    message_type="breaking",
                    channel="telegram",
                    event_ids=[event.cluster_id, event.content_hash],
                    content_preview="BREAKING",
                    content_hash="msg-hash",
                    sent_at=datetime.now(timezone.utc),
                    success=True,
                )
            )

        incoming = _make_event(
            source="newsapi",
            event_type="headline",
            title="Oil tops $100, dollar gains, stocks fall as US moves to blockade Iran",
            cluster_id=event.cluster_id,
        )
        classified = classify_against_sent_history([incoming], lookback_hours=24)[0]
        assert classified.already_sent is True
        assert classified.update_status == "duplicate"
        assert classified.reason_code == "already_sent_tracking_id"
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()


def test_record_sent_events_refreshes_existing_row_recency(tmp_path):
    """Re-marking an existing hash as sent should refresh lookback recency."""
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "repeat_breaking_refresh.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        stale = _make_event(
            source="finnhub",
            event_type="market_news",
            title="Britain's Tesco to shine light on inflation risks from Iran war",
            cluster_id="macro_iran_old",
        )
        with get_session() as session:
            session.add(
                StoredEvent(
                    event_id=stale.event_id,
                    source=stale.source,
                    source_type=stale.source_type,
                    published_at=stale.published_at,
                    title=stale.title,
                    summary=stale.summary,
                    url=stale.url,
                    tickers=stale.tickers,
                    sectors=stale.sectors,
                    regions=stale.regions,
                    event_type=stale.event_type,
                    sentiment=stale.sentiment,
                    content_hash=stale.content_hash,
                    cluster_id=stale.cluster_id,
                    already_sent=True,
                    created_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
            )

        # Mark same content as newly sent now; this should refresh created_at.
        record_sent_events([stale])

        incoming = _make_event(
            source="newsapi",
            event_type="headline",
            title="Britain's Tesco to shine light on inflation risks from Iran war",
            cluster_id="another_cluster",
        )
        classified = classify_against_sent_history([incoming], lookback_hours=24)[0]
        assert classified.already_sent is True
        assert classified.update_status == "duplicate"
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()
