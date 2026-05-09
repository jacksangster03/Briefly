"""Best-effort persistence for normalized healthcare source events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import HealthcareSourceEventRecord
from app.db.session import get_session
from app.healthcare.schemas import HealthcareSourceEvent
from app.logger import get_logger

logger = get_logger("healthcare.source_store")


def upsert_healthcare_source_events(events: list[HealthcareSourceEvent]) -> int:
    if not events:
        return 0
    now = datetime.now(timezone.utc)
    count = 0
    try:
        with get_session() as db:
            for evt in events:
                row = (
                    db.query(HealthcareSourceEventRecord)
                    .filter(
                        HealthcareSourceEventRecord.source_key == evt.source_key,
                        HealthcareSourceEventRecord.stable_event_key == evt.stable_event_key,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = HealthcareSourceEventRecord(
                        source_key=evt.source_key,
                        stable_event_key=evt.stable_event_key,
                        created_at=now,
                    )
                    db.add(row)
                row.source_tier = evt.source_tier
                row.source_event_id = evt.source_event_id
                row.title = evt.title
                row.summary = evt.summary
                row.source_url = evt.source_url
                row.published_at = evt.published_at
                row.discovered_at = evt.discovered_at
                row.company_name = evt.company_name
                row.tickers_json = list(evt.tickers or [])
                row.drug_name = evt.drug_name
                row.condition = evt.condition
                row.regulator = evt.regulator
                row.healthcare_event_type = evt.healthcare_event_type
                row.trial_phase = evt.trial_phase
                row.trial_status = evt.trial_status
                row.severity = evt.severity
                row.confidence = float(evt.confidence)
                row.freshness_state = evt.freshness_state
                row.suppress_reason = evt.suppress_reason
                row.raw_data_json = dict(evt.raw_data or {})
                row.updated_at = now
                count += 1
    except Exception:
        logger.debug("healthcare source event persistence failed (non-fatal)", exc_info=True)
    return count

