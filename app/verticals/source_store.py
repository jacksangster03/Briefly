"""Best-effort persistence for shared vertical source events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import VerticalSourceEventRecord
from app.db.session import get_session
from app.logger import get_logger
from app.verticals.events import VerticalEvent

logger = get_logger("verticals.source_store")


def upsert_vertical_source_events(events: list[VerticalEvent]) -> int:
    if not events:
        return 0
    count = 0
    now = datetime.now(timezone.utc)
    try:
        with get_session() as db:
            for event in events:
                evt = event.with_computed_hash()
                row = (
                    db.query(VerticalSourceEventRecord)
                    .filter(
                        VerticalSourceEventRecord.vertical == evt.vertical,
                        VerticalSourceEventRecord.source_name == evt.source_name,
                        VerticalSourceEventRecord.payload_hash == evt.payload_hash,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = VerticalSourceEventRecord(
                        vertical=evt.vertical,
                        source_name=evt.source_name,
                        payload_hash=evt.payload_hash,
                        created_at=now,
                    )
                    db.add(row)
                row.source_tier = evt.source_tier
                row.source_url = evt.source_url
                row.published_at = evt.published_at
                row.fetched_at = evt.fetched_at
                row.title = evt.title
                row.summary = evt.summary
                row.event_type = evt.event_type
                row.causal_channel = evt.causal_channel
                row.entities_json = list(evt.entities or [])
                row.tickers_json = list(evt.tickers or [])
                row.regions_json = list(evt.regions or [])
                row.countries_json = list(evt.countries or [])
                row.asset_classes_json = list(evt.asset_classes or [])
                row.source_count = int(evt.source_count or 1)
                row.novelty_score = float(evt.novelty_score or 0.0)
                row.relevance_score = float(evt.relevance_score or 0.0)
                row.portfolio_relevance = float(evt.portfolio_relevance or 0.0)
                row.market_relevance = float(evt.market_relevance or 0.0)
                row.confidence = float(evt.confidence or 0.0)
                row.diagnostics_json = dict(evt.diagnostics or {})
                row.updated_at = now
                count += 1
    except Exception:
        logger.debug("vertical source event persistence failed (non-fatal)", exc_info=True)
    return count
