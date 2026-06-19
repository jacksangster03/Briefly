from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import VerticalSourceEventRecord
from app.db.session import get_session
from app.verticals.events import VerticalEvent, compute_payload_hash
from app.verticals.source_store import upsert_vertical_source_events


def test_vertical_event_minimal_validation():
    event = VerticalEvent(vertical="geopolitics", title="Sanctions update")
    assert event.vertical == "geopolitics"
    assert event.source_count == 1
    assert event.payload_hash == ""


def test_vertical_event_optional_fields_safe():
    event = VerticalEvent(vertical="ai_tech")
    assert event.entities == []
    assert event.tickers == []
    assert event.diagnostics == {}


def test_payload_hash_is_deterministic():
    published = datetime(2026, 5, 19, 7, 30, tzinfo=timezone.utc)
    a = compute_payload_hash(
        source_name="gdelt",
        source_url="https://example.com/a",
        published_at=published,
        title="Headline",
    )
    b = compute_payload_hash(
        source_name="gdelt",
        source_url="https://example.com/a",
        published_at=published,
        title="Headline",
    )
    assert a == b


def test_duplicate_event_upsert_is_idempotent(validation_isolated_db):
    evt = VerticalEvent(
        vertical="geopolitics",
        source_name="gdelt",
        source_url="https://example.com/a",
        title="Shipping disruption",
        published_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
    ).with_computed_hash()
    upsert_vertical_source_events([evt])
    upsert_vertical_source_events([evt])
    with get_session() as db:
        rows = (
            db.query(VerticalSourceEventRecord)
            .filter(
                VerticalSourceEventRecord.vertical == "geopolitics",
                VerticalSourceEventRecord.source_name == "gdelt",
                VerticalSourceEventRecord.payload_hash == evt.payload_hash,
            )
            .all()
        )
    assert len(rows) == 1


def test_source_store_db_failure_is_non_blocking(monkeypatch):
    evt = VerticalEvent(vertical="ai_tech", source_name="sec_edgar", title="AI filing").with_computed_hash()

    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("app.verticals.source_store.get_session", _boom)
    count = upsert_vertical_source_events([evt])
    assert count == 0
