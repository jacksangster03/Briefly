"""Compact optional briefing summaries for vertical plugins (shadow mode)."""

from __future__ import annotations

from typing import Any

from app.verticals.config import vertical_briefing_sessions, verticals_include_in_briefing
from app.verticals.engine import verticals_status_for_profile


def build_vertical_shadow_lines(*, profile, session_key: str) -> list[str]:
    if not verticals_include_in_briefing(profile=profile):
        return []
    if session_key not in set(vertical_briefing_sessions(profile=profile)):
        return []

    rows = verticals_status_for_profile(profile=profile)
    by_key = {str(row.get("vertical_key")): row for row in rows}
    lines: list[str] = []
    for key, title in (
        ("geopolitics", "GEO RISK RADAR"),
        ("healthcare", "HEALTHCARE CATALYST WATCH"),
        ("ai_tech", "AI/TECH CATALYST WATCH"),
    ):
        row = by_key.get(key) or {}
        mode = str(row.get("mode") or "off")
        status = str(row.get("activation_status") or "inactive")
        if mode == "off":
            continue
        source_status = row.get("source_status") or {}
        best_source = _best_source(source_status)
        count = int(row.get("included_count") or 0)
        note = f"{title}: {status.replace('_', ' ')}"
        if count > 0:
            note += f" · top items {min(3, count)}"
        if best_source:
            note += f" · source {best_source}"
        lines.append(note)
    return lines[:9]


def _best_source(source_status: dict[str, Any]) -> str:
    for key, value in source_status.items():
        if isinstance(value, dict) and str(value.get("status")) == "ok":
            return str(key)
    return ""
