"""EMA source adapter placeholder for healthcare vertical intelligence."""

from __future__ import annotations

from app.schemas.events import NormalisedEvent


def fetch_ema_events(*, limit: int = 50) -> list[NormalisedEvent]:
    """Phase-1 stub. TODO: wire EMA/CHMP opinion feeds."""
    _ = limit
    return []

