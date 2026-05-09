"""Vertical engine facade with non-blocking diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.verticals.registry import load_vertical_plugins

logger = get_logger("verticals")

_LAST_DIAGNOSTICS: dict[str, dict[str, dict[str, Any]]] = {}


def registered_verticals() -> dict[str, object]:
    return load_vertical_plugins()


def set_last_diagnostics(*, profile_name: str, vertical_key: str, metrics: dict[str, Any]) -> None:
    bucket = _LAST_DIAGNOSTICS.setdefault(profile_name, {})
    stamped = dict(metrics or {})
    stamped["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    bucket[vertical_key] = stamped


def get_last_diagnostics(*, profile_name: str) -> dict[str, dict[str, Any]]:
    return dict(_LAST_DIAGNOSTICS.get(profile_name, {}))


def vertical_mode_for_profile(*, profile, vertical_key: str) -> str:
    plugin = registered_verticals().get(vertical_key)
    if plugin is None:
        return "off"
    try:
        return str(plugin.resolve_mode(profile=profile))
    except Exception:
        logger.debug("vertical mode resolution failed for %s", vertical_key, exc_info=True)
        return "off"


def build_vertical_section(
    *,
    profile,
    vertical_key: str,
    session_key: str,
    candidate_events: list[NormalisedEvent],
):
    plugin = registered_verticals().get(vertical_key)
    if plugin is None:
        return None
    mode = vertical_mode_for_profile(profile=profile, vertical_key=vertical_key)
    try:
        active, reason = plugin.activation_state(
            profile=profile,
            mode=mode,
            session_key=session_key,
            candidate_events=list(candidate_events or []),
        )
    except Exception:
        logger.debug("vertical activation-state failed for %s", vertical_key, exc_info=True)
        active, reason = False, "activation_error"
    if not active:
        try:
            metrics = plugin.audit_metrics(
                profile=profile,
                session_key=session_key,
                candidate_events=list(candidate_events or []),
            )
            metrics.update(
                {
                    "vertical_key": vertical_key,
                    "mode": mode,
                    "activation_status": "inactive",
                    "activation_reason": reason,
                    "included_count": 0,
                }
            )
            set_last_diagnostics(profile_name=profile.name, vertical_key=vertical_key, metrics=metrics)
        except Exception:
            logger.debug("vertical audit-metrics failed for %s", vertical_key, exc_info=True)
        return None
    try:
        section = plugin.build_section(
            profile=profile,
            session_key=session_key,
            candidate_events=list(candidate_events or []),
        )
        metrics = plugin.audit_metrics(
            profile=profile,
            session_key=session_key,
            candidate_events=list(candidate_events or []),
        )
        item_count = len(getattr(section, "items", []) or []) if section is not None else 0
        metrics.update(
            {
                "vertical_key": vertical_key,
                "mode": mode,
                "activation_status": "active",
                "activation_reason": reason,
                "included_count": item_count,
                "suppressed_count": max(0, int(metrics.get("candidate_count", 0) or 0) - item_count),
            }
        )
        set_last_diagnostics(profile_name=profile.name, vertical_key=vertical_key, metrics=metrics)
        return section
    except Exception as exc:
        logger.warning("vertical build failed for %s", vertical_key, exc_info=True)
        try:
            metrics = plugin.audit_metrics(
                profile=profile,
                session_key=session_key,
                candidate_events=list(candidate_events or []),
            )
        except Exception:
            metrics = {}
        metrics.update(
            {
                "vertical_key": vertical_key,
                "mode": mode,
                "activation_status": "error",
                "activation_reason": reason,
                "plugin_error": str(exc),
            }
        )
        set_last_diagnostics(profile_name=profile.name, vertical_key=vertical_key, metrics=metrics)
        return None


def vertical_breaking_candidates(
    *,
    profile,
    vertical_key: str,
    events: list[NormalisedEvent],
) -> list[Any]:
    plugin = registered_verticals().get(vertical_key)
    if plugin is None:
        return []
    mode = vertical_mode_for_profile(profile=profile, vertical_key=vertical_key)
    try:
        active, reason = plugin.activation_state(
            profile=profile,
            mode=mode,
            session_key="breaking",
            candidate_events=list(events or []),
        )
        if not active:
            metrics = plugin.audit_metrics(
                profile=profile,
                session_key="breaking",
                candidate_events=list(events or []),
            )
            metrics.update(
                {
                    "vertical_key": vertical_key,
                    "mode": mode,
                    "activation_status": "inactive",
                    "activation_reason": reason,
                    "included_count": 0,
                }
            )
            set_last_diagnostics(profile_name=profile.name, vertical_key=vertical_key, metrics=metrics)
            return []
        candidates = list(plugin.breaking_candidates(profile=profile, events=list(events or [])) or [])
        metrics = plugin.audit_metrics(profile=profile, session_key="breaking", candidate_events=list(events or []))
        metrics.update(
            {
                "vertical_key": vertical_key,
                "mode": mode,
                "activation_status": "active",
                "activation_reason": reason,
                "included_count": len(candidates),
                "suppressed_count": max(0, int(metrics.get("candidate_count", 0) or 0) - len(candidates)),
            }
        )
        set_last_diagnostics(profile_name=profile.name, vertical_key=vertical_key, metrics=metrics)
        return candidates
    except Exception as exc:
        logger.warning("vertical breaking-candidates failed for %s", vertical_key, exc_info=True)
        set_last_diagnostics(
            profile_name=profile.name,
            vertical_key=vertical_key,
            metrics={
                "vertical_key": vertical_key,
                "mode": mode,
                "activation_status": "error",
                "activation_reason": "exception",
                "plugin_error": str(exc),
            },
        )
        return []


def verticals_status_for_profile(*, profile) -> list[dict[str, Any]]:
    diagnostics = get_last_diagnostics(profile_name=profile.name)
    out: list[dict[str, Any]] = []
    for key, plugin in registered_verticals().items():
        mode = vertical_mode_for_profile(profile=profile, vertical_key=key)
        base = {
            "vertical_key": key,
            "display_name": getattr(plugin, "display_name", key),
            "mode": mode,
            "activation_status": "inactive" if mode == "off" else "ready",
            "activation_reason": "mode_off" if mode == "off" else "configured",
            "candidate_count": None,
            "included_count": None,
            "suppressed_count": None,
            "source_status": {},
            "portfolio_exposure_summary": "",
            "watchlist_exposure_summary": "",
            "plugin_error": "",
            "updated_at_utc": None,
        }
        try:
            base_metrics = plugin.audit_metrics(profile=profile, session_key="status", candidate_events=[])
            for k, v in (base_metrics or {}).items():
                if k not in {"mode", "activation_status", "activation_reason"} and v is not None:
                    base[k] = v
        except Exception as exc:
            base["plugin_error"] = str(exc)
            base["activation_status"] = "error"
            base["activation_reason"] = "audit_metrics_error"
        if key in diagnostics:
            for k, v in diagnostics[key].items():
                if v is None:
                    continue
                if k in {"mode", "activation_status", "activation_reason"}:
                    continue
                base[k] = v
        out.append(base)
    return out
