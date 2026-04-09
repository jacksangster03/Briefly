"""Persistence helpers for processed and sent events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import NormalisedEvent as StoredEvent
from app.db.session import get_session
from app.schemas.events import NormalisedEvent


def record_sent_events(events: list[NormalisedEvent]) -> None:
    """Persist sent events so future runs can suppress duplicates."""
    if not events:
        return

    with get_session() as session:
        for evt in events:
            row = (
                session.query(StoredEvent)
                .filter(StoredEvent.content_hash == evt.content_hash)
                .order_by(StoredEvent.id.desc())
                .first()
            )
            if row is None:
                row = StoredEvent(
                    event_id=evt.event_id,
                    source=evt.source,
                    source_type=evt.source_type,
                    published_at=evt.published_at,
                    title=evt.title,
                    summary=evt.summary,
                    url=evt.url,
                    tickers=evt.tickers,
                    sectors=evt.sectors,
                    regions=evt.regions,
                    event_type=evt.event_type,
                    sentiment=evt.sentiment,
                    content_hash=evt.content_hash,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(row)

            row.cluster_id = evt.cluster_id
            row.importance_score = evt.importance_score
            row.novelty_score = evt.novelty_score
            row.personal_relevance_score = evt.personal_relevance_score
            row.factual_confidence_score = evt.factual_confidence_score
            row.attention_score = evt.attention_score
            row.final_score = evt.final_score
            row.score_explanation = evt.score_explanation
            row.already_sent = True
