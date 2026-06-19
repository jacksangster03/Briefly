"""Healthcare vertical plugin wrapper (Phase 1 compatibility layer)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.healthcare.section_builder import build_healthcare_section, filter_breaking_healthcare_events
from app.healthcare.source_store import upsert_healthcare_source_events
from app.healthcare.sources import clinicaltrials, company_ir, ema, fda
from app.healthcare.taxonomy import HEALTHCARE_EVENT_TYPES
from app.settings import get_settings
from app.schemas.events import NormalisedEvent
from app.verticals.base import VerticalMode
from app.verticals.config import infer_healthcare_mode_from_legacy_enabled, normalize_vertical_mode
from app.verticals.healthcare_bridge import healthcare_source_to_vertical_event
from app.verticals.source_store import upsert_vertical_source_events


class HealthcareVerticalPlugin:
    vertical_key = "healthcare"
    display_name = "Healthcare / Biotech"
    default_mode: VerticalMode = "off"

    def __init__(self) -> None:
        self._last_source_health: dict[str, dict[str, Any]] = {}
        self._last_official_candidates: list[NormalisedEvent] = []
        self._last_official_candidate_count: int = 0
        self._last_official_suppressed_count: int = 0
        self._last_official_fetch_at: datetime | None = None

    def resolve_mode(self, *, profile) -> VerticalMode:
        prefs = profile.healthcare_preferences if hasattr(profile, "healthcare_preferences") else {}
        overrides = getattr(profile, "preference_overrides", {}) or {}
        explicit_mode = normalize_vertical_mode(overrides.get("verticals.healthcare.mode", prefs.get("mode")), default="")
        if explicit_mode:
            return explicit_mode
        return infer_healthcare_mode_from_legacy_enabled(bool(prefs.get("enabled", False)))

    def activation_state(
        self,
        *,
        profile,
        mode: VerticalMode,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ) -> tuple[bool, str]:
        if mode == "off":
            return False, "mode_off"
        if mode == "portfolio_linked":
            has_portfolio = bool(set(getattr(profile, "portfolio_symbols", [])) & set(getattr(profile, "all_watchlist_tickers", [])))
            if has_portfolio:
                return True, "portfolio_linked_overlap"
            return False, "portfolio_linked_no_overlap"
        if mode == "watch":
            return True, "watch_mode"
        return True, "active_mode"

    def collect_candidates(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ) -> list[NormalisedEvent]:
        _ = profile, session_key
        return list(candidate_events or [])

    def classify(self, *, profile, events: list[NormalisedEvent]) -> list[Any]:
        _ = profile
        return list(events or [])

    def score(self, *, profile, classified_events: list[Any]) -> list[Any]:
        _ = profile
        return list(classified_events or [])

    def build_section(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent],
    ):
        merged_events, _ = self._merge_official_candidates(
            profile=profile,
            session_key=session_key,
            candidate_events=list(candidate_events or []),
            mode=self.resolve_mode(profile=profile),
        )
        return build_healthcare_section(
            profile=profile,
            session_key=session_key,
            events=merged_events,
            verbose_when_empty=False,
        )

    def breaking_candidates(self, *, profile, events: list[NormalisedEvent]):
        return filter_breaking_healthcare_events(profile=profile, events=list(events or []))

    def audit_metrics(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent] | None = None,
    ) -> dict[str, Any]:
        mode = self.resolve_mode(profile=profile)
        candidates = list(candidate_events or [])
        activated, reason = self.activation_state(
            profile=profile,
            mode=mode,
            session_key=session_key,
            candidate_events=candidates,
        )
        prefs = profile.healthcare_preferences if hasattr(profile, "healthcare_preferences") else {}
        watch = {str(s).upper() for s in getattr(profile, "all_watchlist_tickers", [])}
        holdings = {str(s).upper() for s in getattr(profile, "portfolio_symbols", [])}
        pref_tickers = {str(s).upper() for s in prefs.get("tickers", [])}
        portfolio_overlap = sorted((watch | holdings) & pref_tickers)
        watch_overlap = sorted(watch & pref_tickers)

        source_status = self._source_status_snapshot()
        fetched_at = self._last_official_fetch_at.isoformat() if self._last_official_fetch_at else ""
        return {
            "vertical_key": self.vertical_key,
            "display_name": self.display_name,
            "mode": mode,
            "activation_status": "active" if activated else "inactive",
            "activation_reason": reason,
            "candidate_count": len(candidates),
            "included_count": None,
            "suppressed_count": None,
            "source_status": source_status,
            "official_candidate_count": self._last_official_candidate_count,
            "official_suppressed_count": self._last_official_suppressed_count,
            "official_last_fetch_at": fetched_at,
            "portfolio_exposure_summary": (
                f"healthcare_ticker_overlap={len(portfolio_overlap)} ({', '.join(portfolio_overlap[:6]) or '-'})"
            ),
            "watchlist_exposure_summary": (
                f"watchlist_overlap={len(watch_overlap)} ({', '.join(watch_overlap[:6]) or '-'})"
            ),
            "event_type_catalog_size": len(HEALTHCARE_EVENT_TYPES),
        }

    def _merge_official_candidates(
        self,
        *,
        profile,
        session_key: str,
        candidate_events: list[NormalisedEvent],
        mode: VerticalMode,
    ) -> tuple[list[NormalisedEvent], list[NormalisedEvent]]:
        # Keep disabled mode silent and avoid provider calls.
        if mode == "off":
            self._last_source_health = self._source_status_disabled()
            self._last_official_candidates = []
            self._last_official_candidate_count = 0
            self._last_official_suppressed_count = 0
            self._last_official_fetch_at = datetime.now(timezone.utc)
            return list(candidate_events or []), []

        settings = get_settings()
        if not bool(getattr(settings, "enable_healthcare_official_sources", False)):
            self._last_source_health = self._source_status_disabled()
            self._last_official_candidates = []
            self._last_official_candidate_count = 0
            self._last_official_suppressed_count = 0
            self._last_official_fetch_at = datetime.now(timezone.utc)
            return list(candidate_events or []), []

        official_events, source_health = self._collect_official_source_events(profile=profile, settings=settings)
        self._last_source_health = source_health
        self._last_official_fetch_at = datetime.now(timezone.utc)
        self._last_official_candidate_count = len(official_events)
        self._last_official_suppressed_count = sum(
            int((entry or {}).get("suppressed_count") or 0)
            for entry in source_health.values()
            if isinstance(entry, dict)
        )
        if official_events:
            upsert_healthcare_source_events(official_events)
            try:
                upsert_vertical_source_events([healthcare_source_to_vertical_event(evt) for evt in official_events])
            except Exception:
                # Keep healthcare path fail-soft; generic archive is non-critical.
                pass

        normalized_official = [self._to_normalized_event(evt) for evt in official_events]
        self._last_official_candidates = normalized_official
        if not normalized_official:
            return list(candidate_events or []), []

        deduped = self._dedupe_events(normalized_official + list(candidate_events or []))
        return deduped, normalized_official

    def _collect_official_source_events(self, *, profile, settings) -> tuple[list[Any], dict[str, dict[str, Any]]]:
        out: list[Any] = []
        status_map: dict[str, dict[str, Any]] = {}

        sec_events, sec_health = company_ir.fetch_sec_healthcare_source_events(
            settings=settings,
            profile=profile,
            budget_allowed=bool(getattr(settings, "healthcare_sec_daily_call_budget", 100) > 0),
            limit=40,
        )
        out.extend(sec_events)
        status_map["sec"] = sec_health.model_dump()

        ct_events, ct_health = clinicaltrials.fetch_clinicaltrials_source_events(
            settings=settings,
            profile=profile,
            budget_allowed=bool(getattr(settings, "healthcare_clinicaltrials_daily_call_budget", 100) > 0),
            limit=30,
        )
        out.extend(ct_events)
        status_map["clinicaltrials"] = ct_health.model_dump()

        fda_events, fda_health = fda.fetch_openfda_source_events(
            settings=settings,
            budget_allowed=bool(getattr(settings, "healthcare_openfda_daily_call_budget", 100) > 0),
            limit=25,
        )
        out.extend(fda_events)
        status_map["openfda"] = fda_health.model_dump()

        ema_events, ema_health = ema.fetch_ema_source_events(
            settings=settings,
            budget_allowed=bool(getattr(settings, "healthcare_ema_daily_call_budget", 50) > 0),
            limit=20,
        )
        out.extend(ema_events)
        status_map["ema"] = ema_health.model_dump()
        return out, status_map

    def _to_normalized_event(self, evt) -> NormalisedEvent:
        raw_data = dict(evt.raw_data or {})
        raw_data.update(
            {
                "healthcare_source_key": evt.source_key,
                "healthcare_source_tier": evt.source_tier,
                "healthcare_event_type": evt.healthcare_event_type,
                "healthcare_trial_phase": evt.trial_phase,
                "healthcare_trial_status": evt.trial_status,
                "healthcare_severity": evt.severity,
                "stable_event_key": evt.stable_event_key,
            }
        )
        return NormalisedEvent(
            event_id=evt.source_event_id or evt.stable_event_key,
            content_hash=evt.stable_event_key,
            title=evt.title,
            summary=evt.summary,
            source=f"healthcare_{evt.source_key}",
            url=evt.source_url,
            published_at=evt.published_at,
            tickers=list(evt.tickers or []),
            event_type="healthcare_official",
            confidence=evt.confidence,
            impact_severity=evt.severity,
            raw_data=raw_data,
        )

    def _dedupe_events(self, events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        seen: set[str] = set()
        out: list[NormalisedEvent] = []
        for evt in events:
            key = str((evt.raw_data or {}).get("stable_event_key") or evt.event_id or evt.content_hash or evt.title).strip()
            if not key:
                key = evt.title
            key_norm = key.lower()
            if key_norm in seen:
                continue
            seen.add(key_norm)
            out.append(evt)
        return out

    def _source_status_disabled(self) -> dict[str, dict[str, Any]]:
        return {
            "sec": {"source_key": "sec", "status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
            "clinicaltrials": {"source_key": "clinicaltrials", "status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
            "openfda": {"source_key": "openfda", "status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
            "ema": {"source_key": "ema", "status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
        }

    def _source_status_snapshot(self) -> dict[str, dict[str, Any]]:
        if self._last_source_health:
            return dict(self._last_source_health)
        return self._source_status_disabled()
