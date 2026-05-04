"""ClinicalTrials.gov adapter placeholder for healthcare vertical intelligence."""

from __future__ import annotations

from app.schemas.events import NormalisedEvent


def fetch_clinicaltrials_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Phase-1 stub. TODO: wire ClinicalTrials.gov status/readout updates."""
    _ = limit
    return []

