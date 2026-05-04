"""Company IR adapter placeholder for healthcare vertical intelligence."""

from __future__ import annotations

from app.schemas.events import NormalisedEvent


def fetch_company_ir_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Phase-1 stub. TODO: wire company IR press-release feeds."""
    _ = limit
    return []

