"""Vertical-mode resolution and normalization helpers."""

from __future__ import annotations

from app.verticals.base import VerticalMode

ALLOWED_VERTICAL_MODES: tuple[VerticalMode, ...] = (
    "off",
    "watch",
    "active",
    "portfolio_linked",
)


def normalize_vertical_mode(value: object, *, default: VerticalMode = "off") -> VerticalMode:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_VERTICAL_MODES:
        return raw
    return default


def infer_healthcare_mode_from_legacy_enabled(enabled: bool) -> VerticalMode:
    """Backwards-compatible mapping for current healthcare.enabled behavior."""
    return "active" if bool(enabled) else "off"

