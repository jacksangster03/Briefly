"""SEC/company-filing healthcare source adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data_sources.providers.sec_provider import SECProvider
from app.healthcare.schemas import (
    HealthcareSourceEvent,
    HealthcareSourceHealth,
    stable_source_event_key,
)
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("healthcare.source.sec")

_HEALTHCARE_FILING_TERMS = (
    "fda",
    "clinical trial",
    "phase 1",
    "phase 2",
    "phase 3",
    "pdufa",
    "approval",
    "complete response letter",
    "crl",
    "label update",
    "safety",
    "adverse event",
    "manufacturing",
    "facility inspection",
    "warning letter",
    "nda",
    "bla",
    "trial halt",
    "endpoint",
    "topline",
)


def fetch_company_ir_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Legacy compatibility API (unused by vertical plugin ingestion)."""
    _ = limit
    return []


def fetch_sec_healthcare_source_events(
    *,
    settings,
    profile,
    budget_allowed: bool = True,
    limit: int = 40,
) -> tuple[list[HealthcareSourceEvent], HealthcareSourceHealth]:
    health = HealthcareSourceHealth(source_key="sec", status="disabled")
    if not bool(getattr(settings, "enable_healthcare_official_sources", False)):
        return [], health
    if not bool(getattr(settings, "enable_healthcare_sec_source", True)):
        return [], health
    if not budget_allowed:
        health.status = "rate_limited"
        health.last_error = "daily_budget_exhausted"
        return [], health

    provider = SECProvider(
        user_agent=str(getattr(settings, "sec_user_agent", "")),
        timeout=int(getattr(settings, "healthcare_source_timeout_seconds", 20)),
        max_retries=1,
    )
    if not provider.is_configured():
        health.status = "error"
        health.last_error = "sec_user_agent_not_configured"
        return [], health

    query_terms = list({*(profile.healthcare_preferences.get("tickers", []) or []), *getattr(profile, "all_watchlist_tickers", [])})[:10]
    query = " OR ".join(query_terms) if query_terms else ""
    events: list[HealthcareSourceEvent] = []
    try:
        filings = provider.search_filings(query=query, forms=["8-K", "10-Q", "10-K"], limit=max(1, int(limit)))
        health.fetched_count = len(filings)
        for evt in filings:
            sev, h_type = _infer_type_and_severity(evt)
            suppress = _suppression_reason(evt)
            if suppress:
                health.suppressed_count += 1
                continue
            title = evt.title or ""
            summary = evt.summary or ""
            source_event_id = str((evt.raw_data or {}).get("adsh") or evt.event_id or evt.content_hash or "")
            published = evt.published_at
            normalized = HealthcareSourceEvent(
                source_key="sec",
                source_tier="official",
                source_event_id=source_event_id,
                stable_event_key=stable_source_event_key(
                    source_key="sec",
                    source_event_id=source_event_id,
                    title=title,
                    published_at=published,
                ),
                title=title,
                summary=summary,
                source_url=evt.url or "",
                published_at=published,
                discovered_at=datetime.now(timezone.utc),
                company_name=_company_name_from_title(title),
                tickers=list(evt.tickers or []),
                drug_name="",
                condition="",
                regulator="FDA" if "fda" in f"{title} {summary}".lower() else "",
                healthcare_event_type=h_type,
                trial_phase=_trial_phase_from_text(f"{title} {summary}"),
                trial_status="",
                severity=sev,
                confidence=0.88,
                freshness_state="new",
                suppress_reason="",
                raw_data=dict(evt.raw_data or {}),
            )
            events.append(normalized)
        health.normalized_count = len(events)
        health.status = "ok"
        health.last_success_at = datetime.now(timezone.utc)
    except Exception as exc:
        health.status = "error"
        health.last_error = str(exc)[:300]
        logger.debug("sec healthcare source fetch failed", exc_info=True)
    return events, health


def _suppression_reason(evt: NormalisedEvent) -> str:
    text = f"{evt.title} {evt.summary}".lower()
    if not any(token in text for token in _HEALTHCARE_FILING_TERMS):
        return "no_healthcare_mechanism"
    return ""


def _infer_type_and_severity(evt: NormalisedEvent) -> tuple[str, str]:
    text = f"{evt.title} {evt.summary}".lower()
    if "complete response letter" in text or " crl" in text:
        return "critical", "fda_rejection_crl"
    if "approval" in text and "fda" in text:
        return "critical", "fda_approval"
    if "trial halt" in text or "terminated" in text or "suspended" in text:
        return "critical", "trial_hold_or_termination"
    if "phase 3" in text or "topline" in text or "endpoint" in text:
        return "high", "clinical_trial_result"
    if "warning letter" in text or "facility inspection" in text or "manufacturing" in text:
        return "high", "manufacturing_supply"
    return "medium", "sec_filing"


def _trial_phase_from_text(text: str) -> str:
    t = text.lower()
    if "phase 3" in t:
        return "Phase 3"
    if "phase 2" in t:
        return "Phase 2"
    if "phase 1" in t:
        return "Phase 1"
    return ""


def _company_name_from_title(title: str) -> str:
    if ":" in title:
        return title.split(":", 1)[1].strip()[:180]
    return title[:180]

