"""Healthcare vertical plugin wrapper (Phase 1 compatibility layer)."""

from __future__ import annotations

from typing import Any

from app.healthcare.section_builder import build_healthcare_section, filter_breaking_healthcare_events
from app.healthcare.sources import clinicaltrials, company_ir, ema, fda
from app.healthcare.taxonomy import HEALTHCARE_EVENT_TYPES
from app.schemas.events import NormalisedEvent
from app.verticals.base import VerticalMode
from app.verticals.config import infer_healthcare_mode_from_legacy_enabled, normalize_vertical_mode


class HealthcareVerticalPlugin:
    vertical_key = "healthcare"
    display_name = "Healthcare / Biotech"
    default_mode: VerticalMode = "off"

    def resolve_mode(self, *, profile) -> VerticalMode:
        prefs = profile.healthcare_preferences if hasattr(profile, "healthcare_preferences") else {}
        explicit_mode = normalize_vertical_mode(prefs.get("mode"), default="")
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
        return build_healthcare_section(
            profile=profile,
            session_key=session_key,
            events=list(candidate_events or []),
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

        source_status = {
            "fda": "stub_inactive" if callable(getattr(fda, "fetch_fda_events", None)) else "missing",
            "clinicaltrials": "stub_inactive" if callable(getattr(clinicaltrials, "fetch_clinicaltrials_events", None)) else "missing",
            "ema": "stub_inactive" if callable(getattr(ema, "fetch_ema_events", None)) else "missing",
            "company_ir": "stub_inactive" if callable(getattr(company_ir, "fetch_company_ir_events", None)) else "missing",
        }
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
            "portfolio_exposure_summary": (
                f"healthcare_ticker_overlap={len(portfolio_overlap)} ({', '.join(portfolio_overlap[:6]) or '-'})"
            ),
            "watchlist_exposure_summary": (
                f"watchlist_overlap={len(watch_overlap)} ({', '.join(watch_overlap[:6]) or '-'})"
            ),
            "event_type_catalog_size": len(HEALTHCARE_EVENT_TYPES),
        }

