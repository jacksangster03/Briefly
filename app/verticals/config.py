"""Vertical-mode resolution and normalization helpers."""

from __future__ import annotations

from app.verticals.base import VerticalMode

ALLOWED_VERTICAL_MODES: tuple[VerticalMode, ...] = (
    "off",
    "watch",
    "active",
    "portfolio_linked",
)

ALLOWED_VERTICAL_BRIEFING_SESSIONS: tuple[str, ...] = ("morning",)


def normalize_vertical_mode(value: object, *, default: VerticalMode = "off") -> VerticalMode:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_VERTICAL_MODES:
        return raw
    return default


def infer_healthcare_mode_from_legacy_enabled(enabled: bool) -> VerticalMode:
    """Backwards-compatible mapping for current healthcare.enabled behavior."""
    return "active" if bool(enabled) else "off"


def verticals_include_in_briefing(*, profile) -> bool:
    overrides = getattr(profile, "preference_overrides", {}) or {}
    if "verticals.include_in_briefing" in overrides:
        return bool(overrides.get("verticals.include_in_briefing"))
    delivery = getattr(profile, "delivery", {}) or {}
    return bool(delivery.get("verticals_include_in_briefing", False))


def vertical_briefing_sessions(*, profile) -> list[str]:
    overrides = getattr(profile, "preference_overrides", {}) or {}
    raw = overrides.get("verticals.briefing_sessions", ["morning"])
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    else:
        parts = ["morning"]
    allowed = set(ALLOWED_VERTICAL_BRIEFING_SESSIONS)
    out: list[str] = []
    for p in parts:
        if p in allowed and p not in out:
            out.append(p)
    return out or ["morning"]
