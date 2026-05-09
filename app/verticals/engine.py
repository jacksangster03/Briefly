"""Vertical engine facade with non-blocking diagnostics."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import VerticalRunDiagnostics
from app.db.session import get_session
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


def _local_date_for_profile(*, profile, now_utc: datetime | None = None) -> date:
    now = now_utc or datetime.now(timezone.utc)
    tz_name = getattr(profile, "timezone", "") or "Europe/Madrid"
    return now.astimezone(ZoneInfo(tz_name)).date()


def persist_vertical_diagnostics(
    *,
    profile,
    session_key: str,
    vertical_key: str,
    metrics: dict[str, Any],
) -> None:
    """Best-effort persistence for audit diagnostics; never raises."""
    try:
        local_date = _local_date_for_profile(profile=profile)
        now = datetime.now(timezone.utc)
        with get_session() as db:
            row = (
                db.query(VerticalRunDiagnostics)
                .filter(
                    VerticalRunDiagnostics.profile_name == profile.name,
                    VerticalRunDiagnostics.local_date == local_date,
                    VerticalRunDiagnostics.session_key == str(session_key or ""),
                    VerticalRunDiagnostics.vertical_key == str(vertical_key or ""),
                )
                .one_or_none()
            )
            if row is None:
                row = VerticalRunDiagnostics(
                    profile_name=profile.name,
                    local_date=local_date,
                    session_key=str(session_key or ""),
                    vertical_key=str(vertical_key or ""),
                    created_at=now,
                )
                db.add(row)
            row.mode = str(metrics.get("mode", ""))
            row.status = str(metrics.get("activation_status", ""))
            row.activation_reason = str(metrics.get("activation_reason", ""))
            row.candidate_count = _to_int_or_none(metrics.get("candidate_count"))
            row.included_count = _to_int_or_none(metrics.get("included_count"))
            row.suppressed_count = _to_int_or_none(metrics.get("suppressed_count"))
            row.source_status_json = metrics.get("source_status") or {}
            row.portfolio_exposure_summary = str(metrics.get("portfolio_exposure_summary", ""))
            row.watchlist_exposure_summary = str(metrics.get("watchlist_exposure_summary", ""))
            row.error_message = str(metrics.get("plugin_error", ""))
            row.updated_at = now
    except Exception:
        logger.debug("vertical diagnostics persistence failed (non-fatal)", exc_info=True)


def _to_int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def latest_persisted_vertical_diagnostics(*, profile_name: str, vertical_key: str) -> dict[str, Any] | None:
    try:
        with get_session() as db:
            row = (
                db.query(VerticalRunDiagnostics)
                .filter(
                    VerticalRunDiagnostics.profile_name == profile_name,
                    VerticalRunDiagnostics.vertical_key == vertical_key,
                )
                .order_by(VerticalRunDiagnostics.updated_at.desc(), VerticalRunDiagnostics.id.desc())
                .first()
            )
        if row is None:
            return None
        return _row_to_dict(row)
    except Exception:
        logger.debug("failed loading latest vertical diagnostics", exc_info=True)
        return None


def persisted_vertical_diagnostics_for_date(*, profile_name: str, local_date: date) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    try:
        with get_session() as db:
            rows = (
                db.query(VerticalRunDiagnostics)
                .filter(
                    VerticalRunDiagnostics.profile_name == profile_name,
                    VerticalRunDiagnostics.local_date == local_date,
                )
                .order_by(VerticalRunDiagnostics.updated_at.desc(), VerticalRunDiagnostics.id.desc())
                .all()
            )
        for row in rows:
            key = f"{row.session_key}:{row.vertical_key}"
            if key in out:
                continue
            out[key] = _row_to_dict(row)
    except Exception:
        logger.debug("failed loading dated vertical diagnostics", exc_info=True)
    return out


def vertical_diagnostics_history(
    *,
    profile_name: str,
    from_date: date | None = None,
    to_date: date | None = None,
    vertical_key: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    try:
        with get_session() as db:
            q = db.query(VerticalRunDiagnostics).filter(VerticalRunDiagnostics.profile_name == profile_name)
            if from_date is not None:
                q = q.filter(VerticalRunDiagnostics.local_date >= from_date)
            if to_date is not None:
                q = q.filter(VerticalRunDiagnostics.local_date <= to_date)
            if vertical_key:
                q = q.filter(VerticalRunDiagnostics.vertical_key == vertical_key)
            rows = (
                q.order_by(VerticalRunDiagnostics.local_date.desc(), VerticalRunDiagnostics.updated_at.desc(), VerticalRunDiagnostics.id.desc())
                .limit(max(1, int(limit)))
                .all()
            )
        return [_row_to_dict(row) for row in rows]
    except Exception:
        logger.debug("failed loading vertical diagnostics history", exc_info=True)
        return []


def _row_to_dict(row: VerticalRunDiagnostics) -> dict[str, Any]:
    return {
        "id": row.id,
        "profile_name": row.profile_name,
        "local_date": row.local_date,
        "session_key": row.session_key,
        "vertical_key": row.vertical_key,
        "mode": row.mode,
        "activation_status": row.status,
        "activation_reason": row.activation_reason,
        "candidate_count": row.candidate_count,
        "included_count": row.included_count,
        "suppressed_count": row.suppressed_count,
        "source_status": row.source_status_json or {},
        "portfolio_exposure_summary": row.portfolio_exposure_summary or "",
        "watchlist_exposure_summary": row.watchlist_exposure_summary or "",
        "plugin_error": row.error_message or "",
        "updated_at_utc": row.updated_at.isoformat() if row.updated_at else None,
    }


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
            persist_vertical_diagnostics(
                profile=profile,
                session_key=session_key,
                vertical_key=vertical_key,
                metrics=metrics,
            )
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
        persist_vertical_diagnostics(
            profile=profile,
            session_key=session_key,
            vertical_key=vertical_key,
            metrics=metrics,
        )
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
        persist_vertical_diagnostics(
            profile=profile,
            session_key=session_key,
            vertical_key=vertical_key,
            metrics=metrics,
        )
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
            persist_vertical_diagnostics(
                profile=profile,
                session_key="breaking",
                vertical_key=vertical_key,
                metrics=metrics,
            )
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
        persist_vertical_diagnostics(
            profile=profile,
            session_key="breaking",
            vertical_key=vertical_key,
            metrics=metrics,
        )
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
        persist_vertical_diagnostics(
            profile=profile,
            session_key="breaking",
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
        latest = latest_persisted_vertical_diagnostics(profile_name=profile.name, vertical_key=key)
        if latest:
            base["latest_stored_diagnostic"] = latest
            base["last_session_key"] = latest.get("session_key")
            base["last_local_date"] = latest.get("local_date")
            base["last_updated_at_utc"] = latest.get("updated_at_utc")
            # Only merge latest-run metrics into current status when modes align
            # and current mode is not explicitly off.
            latest_mode = str(latest.get("mode") or "").strip().lower()
            current_mode = str(mode or "").strip().lower()
            merge_latest_into_current = bool(
                current_mode != "off"
                and latest_mode
                and latest_mode == current_mode
            )
            if merge_latest_into_current:
                for field in (
                    "candidate_count",
                    "included_count",
                    "suppressed_count",
                    "activation_reason",
                    "source_status",
                    "plugin_error",
                ):
                    val = latest.get(field)
                    if val not in (None, ""):
                        base[field] = val
        out.append(base)
    return out
