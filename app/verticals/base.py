"""Base interface for deterministic vertical-intelligence plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.events import NormalisedEvent


VerticalMode = str  # off | watch | active | portfolio_linked


@dataclass
class VerticalRunDiagnostics:
    """Per-run plugin diagnostics (audit only, never delivery-critical)."""

    vertical_key: str
    mode: VerticalMode
    activated: bool
    activation_reason: str
    candidate_count: int = 0
    included_count: int = 0
    suppressed_count: int = 0
    source_status: dict[str, str] = field(default_factory=dict)
    portfolio_exposure_summary: str = ""
    watchlist_exposure_summary: str = ""
    plugin_error: str = ""


class VerticalPlugin(Protocol):
    """Interface implemented by all vertical plugins."""

    vertical_key: str
    display_name: str
    default_mode: VerticalMode

    def resolve_mode(self, *, profile) -> VerticalMode:
        ...

    def activation_state(
        self,
        *,
        profile,
        mode: VerticalMode,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ) -> tuple[bool, str]:
        ...

    def collect_candidates(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ) -> list[NormalisedEvent]:
        ...

    def classify(self, *, profile, events: list[NormalisedEvent]) -> list[Any]:
        ...

    def score(self, *, profile, classified_events: list[Any]) -> list[Any]:
        ...

    def build_section(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ) -> Any | None:
        ...

    def breaking_candidates(self, *, profile, events: list[NormalisedEvent]) -> list[Any]:
        ...

    def audit_metrics(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent] | None = None,
    ) -> dict[str, Any]:
        ...

