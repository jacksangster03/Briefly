"""Deterministic geopolitics vertical plugin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.events import NormalisedEvent
from app.settings import get_settings
from app.verticals.base import VerticalMode
from app.verticals.events import VerticalEvent
from app.verticals.scoring import rank_vertical_events
from app.verticals.source_store import upsert_vertical_source_events
from app.verticals.sources.geopolitics import collect_geopolitics_events


class GeopoliticsVerticalPlugin:
    vertical_key = "geopolitics"
    display_name = "Geopolitics"
    default_mode: VerticalMode = "off"

    def __init__(self) -> None:
        self._last_source_health: dict[str, dict[str, Any]] = {}
        self._last_events: list[VerticalEvent] = []
        self._last_fetch_at: datetime | None = None

    def resolve_mode(self, *, profile) -> VerticalMode:
        raw = (getattr(profile, "preference_overrides", {}) or {}).get("verticals.geopolitics.mode")
        if not raw:
            raw = (getattr(profile, "delivery", {}) or {}).get("verticals_geopolitics_mode", "off")
        mode = str(raw or "off").strip().lower()
        return mode if mode in {"off", "watch", "active", "portfolio_linked"} else "off"

    def activation_state(self, *, profile, mode: VerticalMode, session_key: str, candidate_events: list[NormalisedEvent]) -> tuple[bool, str]:
        _ = profile, session_key, candidate_events
        if mode == "off":
            return False, "mode_off"
        return True, "configured"

    def collect_candidates(self, *, profile, session_key: str, candidate_events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        _ = profile, session_key
        return list(candidate_events or [])

    def classify(self, *, profile, events: list[NormalisedEvent]) -> list[Any]:
        _ = profile
        return list(events or [])

    def score(self, *, profile, classified_events: list[Any]) -> list[Any]:
        _ = profile
        return list(classified_events or [])

    def _collect(self, *, profile) -> list[VerticalEvent]:
        settings = get_settings()
        events, health = collect_geopolitics_events(settings=settings, watchlist=getattr(profile, "all_watchlist_tickers", []))
        ranked = rank_vertical_events(events)
        self._last_source_health = health
        self._last_events = ranked
        self._last_fetch_at = datetime.now(timezone.utc)
        if ranked:
            upsert_vertical_source_events(ranked)
        return ranked

    def build_section(self, *, profile, session_key: str, candidate_events: list[NormalisedEvent]):
        _ = session_key, candidate_events
        return None

    def breaking_candidates(self, *, profile, events: list[NormalisedEvent]) -> list[Any]:
        _ = events
        return [e for e in self._collect(profile=profile) if float((e.diagnostics or {}).get("deterministic_score", 0.0)) >= 0.8][:3]

    def audit_metrics(self, *, profile, session_key: str, candidate_events: list[NormalisedEvent] | None = None) -> dict[str, Any]:
        _ = candidate_events
        mode = self.resolve_mode(profile=profile)
        activated, reason = self.activation_state(profile=profile, mode=mode, session_key=session_key, candidate_events=[])
        events = self._collect(profile=profile) if mode != "off" else []
        return {
            "vertical_key": self.vertical_key,
            "display_name": self.display_name,
            "mode": mode,
            "activation_status": "active" if activated else "inactive",
            "activation_reason": reason,
            "candidate_count": len(events),
            "included_count": min(3, len(events)) if events else 0,
            "suppressed_count": max(0, len(events) - 3),
            "source_status": self._last_source_health or {"gdelt": {"status": "disabled"}},
            "portfolio_exposure_summary": f"watchlist_overlap={len(set(getattr(profile, 'all_watchlist_tickers', [])))}",
            "watchlist_exposure_summary": f"geo_event_count={len(events)}",
            "official_candidate_count": 0,
            "official_suppressed_count": 0,
            "official_last_fetch_at": self._last_fetch_at.isoformat() if self._last_fetch_at else "",
        }
