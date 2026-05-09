"""Deterministic macro policy calendar loader."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


def load_macro_policy_calendar(*, configs_dir: str) -> list[dict[str, Any]]:
    """Load deterministic macro catalyst events from local YAML.

    Never raises. Returns [] on missing/malformed config.
    """
    try:
        path = Path(configs_dir) / "macro_calendar.yaml"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        rows = payload.get("events", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            normalized = normalize_macro_calendar_event(item)
            if normalized:
                out.append(normalized)
        return out
    except Exception:
        return []


def normalize_macro_calendar_event(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title", "")).strip()
    date_str = str(item.get("date", "")).strip()
    if not title or not date_str:
        return None
    # Keep deterministic ordering and IDs.
    event_type = str(item.get("event_type", "macro_release")).strip().lower() or "macro_release"
    region = str(item.get("region", "global")).strip().lower() or "global"
    importance = str(item.get("importance", "medium")).strip().lower()
    if importance not in {"low", "medium", "high"}:
        importance = "medium"
    if not _is_iso_date(date_str):
        return None
    event_id = str(item.get("event_id", "")).strip() or _build_event_id(event_type=event_type, region=region, title=title, date_str=date_str)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "region": region,
        "title": title,
        "date": date_str,
        "importance": importance,
        "why_it_matters": str(item.get("why_it_matters", "")).strip(),
        "hawkish_if": str(item.get("hawkish_if", "")).strip(),
        "dovish_if": str(item.get("dovish_if", "")).strip(),
        "portfolio_lens": str(item.get("portfolio_lens", "")).strip(),
    }


def upcoming_macro_events(events: list[dict[str, Any]], *, today: date, horizon_days: int = 14) -> list[dict[str, Any]]:
    end = today.toordinal() + max(1, int(horizon_days))
    selected: list[dict[str, Any]] = []
    for row in events:
        d = _parse_iso_date(str(row.get("date", "")))
        if d is None:
            continue
        if today.toordinal() <= d.toordinal() <= end:
            selected.append(dict(row))
    selected.sort(key=lambda row: (str(row.get("date", "")), -_importance_rank(str(row.get("importance", "medium")))))
    return selected


def _build_event_id(*, event_type: str, region: str, title: str, date_str: str) -> str:
    slug = f"{event_type}:{region}:{title}:{date_str}".lower()
    clean = "".join(ch if ch.isalnum() else "_" for ch in slug)
    while "__" in clean:
        clean = clean.replace("__", "_")
    return clean.strip("_")[:120]


def _is_iso_date(value: str) -> bool:
    return _parse_iso_date(value) is not None


def _parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _importance_rank(value: str) -> int:
    raw = (value or "").strip().lower()
    if raw == "high":
        return 3
    if raw == "medium":
        return 2
    return 1

