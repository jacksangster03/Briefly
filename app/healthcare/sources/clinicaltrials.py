"""ClinicalTrials.gov adapter for healthcare vertical intelligence."""

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

logger = get_logger("healthcare.source.clinicaltrials")


def fetch_clinicaltrials_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Legacy compatibility API (unused by vertical plugin ingestion)."""
    _ = limit
    return []


def fetch_clinicaltrials_source_events(
    *,
    settings,
    profile,
    budget_allowed: bool = True,
    limit: int = 30,
) -> tuple[list[HealthcareSourceEvent], HealthcareSourceHealth]:
    health = HealthcareSourceHealth(source_key="clinicaltrials", status="disabled")
    if not bool(getattr(settings, "enable_healthcare_official_sources", False)):
        return [], health
    if not bool(getattr(settings, "enable_healthcare_clinicaltrials_source", False)):
        return [], health
    if not budget_allowed:
        health.status = "rate_limited"
        health.last_error = "daily_budget_exhausted"
        return [], health

    base_url = str(getattr(settings, "clinicaltrials_base_url", "")).strip()
    if not base_url:
        health.status = "error"
        health.last_error = "clinicaltrials_base_url_missing"
        return [], health

    names = _company_name_terms(profile)
    query_expr = " OR ".join(names[:6]) if names else "biotech OR pharma"
    events: list[HealthcareSourceEvent] = []
    try:
        payload = _fetch_payload(
            base_url=base_url,
            query_expr=query_expr,
            page_size=max(1, int(limit)),
            timeout_seconds=float(getattr(settings, "healthcare_source_timeout_seconds", 20)),
        )
        events, suppressed = _normalize_payload(payload)
        health.fetched_count = int(len((payload or {}).get("studies", []) or []))
        health.normalized_count = len(events)
        health.suppressed_count = suppressed
        health.status = "ok"
        health.last_success_at = datetime.now(timezone.utc)
    except Exception as exc:
        health.status = "error"
        health.last_error = str(exc)[:300]
        logger.debug("clinicaltrials healthcare source fetch failed", exc_info=True)
    return events, health


def _company_name_terms(profile) -> list[str]:
    out: list[str] = []
    # configured healthcare tickers/company names are the strongest anchors
    prefs = getattr(profile, "healthcare_preferences", {}) or {}
    for item in list(prefs.get("tickers", []) or []):
        raw = str(item or "").strip()
        if raw:
            out.append(raw)
    for ticker in list(getattr(profile, "all_watchlist_tickers", []) or [])[:6]:
        raw = str(ticker or "").strip()
        if raw:
            out.append(raw)
    deduped: list[str] = []
    seen: set[str] = set()
    for name in out:
        key = name.upper()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _fetch_payload(*, base_url: str, query_expr: str, page_size: int, timeout_seconds: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/studies"
    fields = ",".join(
        [
            "NCTId",
            "BriefTitle",
            "BriefSummary",
            "Condition",
            "InterventionName",
            "LeadSponsorName",
            "Phase",
            "OverallStatus",
            "HasResults",
            "LastUpdatePostDate",
            "StartDate",
            "CompletionDate",
        ]
    )
    params = {
        "query.term": query_expr,
        "fields": fields,
        "countTotal": "false",
        "pageSize": str(page_size),
        "format": "json",
    }
    resp = requests.get(url, params=params, timeout=max(1.0, timeout_seconds))
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        return payload
    return {}


def _normalize_payload(payload: dict[str, Any]) -> tuple[list[HealthcareSourceEvent], int]:
    studies = list((payload or {}).get("studies", []) or [])
    out: list[HealthcareSourceEvent] = []
    suppressed = 0
    for raw in studies:
        proto = (((raw or {}).get("protocolSection") or {}).get("identificationModule") or {})
        status_module = (((raw or {}).get("protocolSection") or {}).get("statusModule") or {})
        design_module = (((raw or {}).get("protocolSection") or {}).get("designModule") or {})
        desc_module = (((raw or {}).get("protocolSection") or {}).get("descriptionModule") or {})
        cond_module = (((raw or {}).get("protocolSection") or {}).get("conditionsModule") or {})
        sponsor_module = (((raw or {}).get("protocolSection") or {}).get("sponsorCollaboratorsModule") or {})

        nct_id = str(proto.get("nctId") or "").strip()
        title = str(proto.get("briefTitle") or "").strip()
        summary = str(desc_module.get("briefSummary") or "").strip()
        if not nct_id or not title:
            suppressed += 1
            continue
        phase = _extract_phase(design_module.get("phases"))
        status = str(status_module.get("overallStatus") or "").strip().lower().replace(" ", "_")
        has_results = bool(status_module.get("hasResults"))
        event_type, severity = _event_type_and_severity(status=status, phase=phase, has_results=has_results)
        condition = _first_text(cond_module.get("conditions"))
        drug_name = _first_text((((raw or {}).get("protocolSection") or {}).get("armsInterventionsModule") or {}).get("interventions"))
        sponsor = str(sponsor_module.get("leadSponsor", {}).get("name") or "").strip()
        updated = _parse_date(status_module.get("lastUpdatePostDateStruct", {}).get("date"))
        if updated is None:
            updated = _parse_date(status_module.get("lastUpdatePostDate"))
        evt = HealthcareSourceEvent(
            source_key="clinicaltrials",
            source_tier="official",
            source_event_id=nct_id,
            stable_event_key=stable_source_event_key(
                source_key="clinicaltrials",
                source_event_id=nct_id,
                title=title,
                published_at=updated,
            ),
            title=title,
            summary=summary,
            source_url=f"https://clinicaltrials.gov/study/{nct_id}",
            published_at=updated,
            discovered_at=datetime.now(timezone.utc),
            company_name=sponsor,
            tickers=[],
            drug_name=drug_name,
            condition=condition,
            regulator="ClinicalTrials.gov",
            healthcare_event_type=event_type,
            trial_phase=phase,
            trial_status=status,
            severity=severity,
            confidence=0.8 if has_results else 0.7,
            freshness_state="new",
            suppress_reason="",
            raw_data={"nct_id": nct_id, "status": status, "has_results": has_results},
        )
        out.append(evt)
    return out, suppressed


def _event_type_and_severity(*, status: str, phase: str, has_results: bool) -> tuple[str, str]:
    if status in {"terminated", "suspended", "withdrawn"}:
        return "trial_hold_or_termination", "critical"
    if has_results and phase in {"Phase 3", "Phase 2/Phase 3"}:
        return "clinical_trial_result", "high"
    if status in {"completed", "active_not_recruiting"}:
        return "clinical_trial_result", "medium"
    if status in {"recruiting", "not_yet_recruiting"}:
        return "clinical_trial_start", "medium"
    return "clinical_trial_update", "low"


def _extract_phase(phases: Any) -> str:
    if isinstance(phases, list) and phases:
        return str(phases[0] or "").strip()
    if isinstance(phases, str):
        return phases.strip()
    return ""


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            for key in ("name", "interventionName"):
                if first.get(key):
                    return str(first.get(key)).strip()
    return ""


def _parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%B %Y", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None
