from __future__ import annotations

from datetime import datetime, timezone

from app.verticals.events import VerticalEvent, compute_payload_hash


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
