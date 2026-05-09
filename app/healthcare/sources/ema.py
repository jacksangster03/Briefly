"""EMA source adapter (conservative, diagnostics-first)."""

from __future__ import annotations

from app.healthcare.schemas import HealthcareSourceEvent, HealthcareSourceHealth
from app.schemas.events import NormalisedEvent


def fetch_ema_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Legacy compatibility API (unused by vertical plugin ingestion)."""
    _ = limit
    return []


def fetch_ema_source_events(
    *,
    settings,
    budget_allowed: bool = True,
    limit: int = 20,
) -> tuple[list[HealthcareSourceEvent], HealthcareSourceHealth]:
    _ = budget_allowed, limit
    # Keep EMA integration conservative for now: no brittle scraping.
    # Report clean health so diagnostics are explicit.
    health = HealthcareSourceHealth(source_key="ema", status="stub_inactive")
    if not bool(getattr(settings, "enable_healthcare_official_sources", False)):
        health.status = "disabled"
        return [], health
    if not bool(getattr(settings, "enable_healthcare_ema_source", False)):
        health.status = "disabled"
        return [], health
    health.last_error = "ema_adapter_stub_no_stable_endpoint"
    return [], health
