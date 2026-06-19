"""Shared event contract for vertical-intelligence sources."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def compute_payload_hash(
    *,
    source_name: str,
    source_url: str,
    published_at: datetime | None,
    title: str,
) -> str:
    """Deterministic bounded hash used for dedupe/persistence keys."""
    ts = published_at.astimezone(timezone.utc).isoformat() if published_at else ""
    raw = "|".join(
        [
            str(source_name or "").strip().lower(),
            str(source_url or "").strip().lower(),
            ts,
            str(title or "").strip().lower(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class VerticalEvent(BaseModel):
    """Normalised cross-vertical source event (metadata only)."""

    vertical: str
    source_name: str = ""
    source_tier: str = "primary"  # official | primary | trusted_media | broad_media | social_optional
    source_url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str = ""
    summary: str = ""
    entities: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    asset_classes: list[str] = Field(default_factory=list)
    event_type: str = ""
    causal_channel: str = ""
    source_count: int = 1
    novelty_score: float = 0.5
    relevance_score: float = 0.0
    portfolio_relevance: float = 0.0
    market_relevance: float = 0.0
    confidence: float = 0.5
    payload_hash: str = ""
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def with_computed_hash(self) -> "VerticalEvent":
        if not self.payload_hash:
            self.payload_hash = compute_payload_hash(
                source_name=self.source_name,
                source_url=self.source_url,
                published_at=self.published_at,
                title=self.title,
            )
        return self
