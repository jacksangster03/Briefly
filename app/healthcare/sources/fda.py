"""FDA source adapter placeholder for healthcare vertical intelligence."""

from __future__ import annotations

from app.schemas.events import NormalisedEvent


def fetch_fda_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Phase-1 stub. TODO: wire FDA approvals/safety feeds."""
    _ = limit
    return []

