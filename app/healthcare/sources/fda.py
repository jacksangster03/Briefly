"""openFDA source adapter for healthcare vertical intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from app.healthcare.schemas import (
    HealthcareSourceEvent,
    HealthcareSourceHealth,
    stable_source_event_key,
)
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("healthcare.source.fda")


def fetch_fda_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Legacy compatibility API (unused by vertical plugin ingestion)."""
    _ = limit
    return []


def fetch_openfda_source_events(
    *,
    settings,
    budget_allowed: bool = True,
    limit: int = 25,
) -> tuple[list[HealthcareSourceEvent], HealthcareSourceHealth]:
    health = HealthcareSourceHealth(source_key="openfda", status="disabled")
    if not bool(getattr(settings, "enable_healthcare_official_sources", False)):
        return [], health
    if not bool(getattr(settings, "enable_healthcare_openfda_source", False)):
        return [], health
    if not budget_allowed:
        health.status = "rate_limited"
        health.last_error = "daily_budget_exhausted"
        return [], health

    base_url = str(getattr(settings, "fda_openfda_base_url", "")).strip()
    if not base_url:
        health.status = "error"
        health.last_error = "fda_openfda_base_url_missing"
        return [], health

    api_key = str(getattr(settings, "fda_openfda_api_key", "")).strip()
    try:
        payload = _fetch_enforcement_payload(
            base_url=base_url,
            api_key=api_key,
            limit=max(1, int(limit)),
            timeout_seconds=float(getattr(settings, "healthcare_source_timeout_seconds", 20)),
        )
        events, suppressed = _normalize_enforcement_payload(payload)
        health.fetched_count = len((payload or {}).get("results", []) or [])
        health.normalized_count = len(events)
        health.suppressed_count = suppressed
        health.status = "ok"
        health.last_success_at = datetime.now(timezone.utc)
        return events, health
    except Exception as exc:
        health.status = "error"
        health.last_error = str(exc)[:300]
        logger.debug("openfda healthcare source fetch failed", exc_info=True)
        return [], health


def _fetch_enforcement_payload(*, base_url: str, api_key: str, limit: int, timeout_seconds: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/drug/enforcement.json"
    params: dict[str, str] = {
        "search": "status:Ongoing+OR+status:Completed",
        "limit": str(limit),
    }
    if api_key:
        params["api_key"] = api_key
    resp = requests.get(url, params=params, timeout=max(1.0, timeout_seconds))
    if resp.status_code == 404:
        # Some openFDA mirrors may not expose this endpoint.
        return {"results": []}
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        return payload
    return {}


def _normalize_enforcement_payload(payload: dict[str, Any]) -> tuple[list[HealthcareSourceEvent], int]:
    out: list[HealthcareSourceEvent] = []
    suppressed = 0
    for raw in list((payload or {}).get("results", []) or []):
        classification = str(raw.get("classification") or "").strip()
        reason = str(raw.get("reason_for_recall") or "").strip()
        product_desc = str(raw.get("product_description") or "").strip()
        if not classification and not reason and not product_desc:
            suppressed += 1
            continue
        event_type, severity = _event_type_and_severity(classification=classification, reason=reason)
        recall_number = str(raw.get("recall_number") or raw.get("event_id") or "").strip()
        report_date = _parse_yyyymmdd(raw.get("report_date"))
        title = f"FDA enforcement update: {classification or 'Recall'}".strip()
        evt = HealthcareSourceEvent(
            source_key="openfda",
            source_tier="official",
            source_event_id=recall_number,
            stable_event_key=stable_source_event_key(
                source_key="openfda",
                source_event_id=recall_number,
                title=title,
                published_at=report_date,
            ),
            title=title,
            summary=reason or product_desc or "FDA enforcement update.",
            source_url="https://open.fda.gov/apis/drug/enforcement/",
            published_at=report_date,
            discovered_at=datetime.now(timezone.utc),
            company_name=str(raw.get("recalling_firm") or "").strip(),
            tickers=[],
            drug_name=product_desc[:200],
            condition="",
            regulator="FDA",
            healthcare_event_type=event_type,
            trial_phase="",
            trial_status="",
            severity=severity,
            confidence=0.68,
            freshness_state="new",
            suppress_reason="",
            raw_data={"classification": classification, "status": str(raw.get("status") or "")},
        )
        out.append(evt)
    return out, suppressed


def _event_type_and_severity(*, classification: str, reason: str) -> tuple[str, str]:
    text = f"{classification} {reason}".lower()
    if "class i" in text:
        return "drug_safety_warning", "critical"
    if "class ii" in text:
        return "drug_safety_warning", "high"
    if "mislabel" in text or "label" in text:
        return "label_update", "medium"
    return "drug_safety_warning", "medium"


def _parse_yyyymmdd(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) != 8:
        return None
    try:
        dt = datetime.strptime(text, "%Y%m%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
