"""Deterministic market clock context for session outputs."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.briefing.session_templates import SessionTemplateItem


_EXCHANGES: tuple[dict[str, object], ...] = (
    {"name": "ASX (Australia)", "tz": "Australia/Sydney", "start": time(10, 0), "end": time(16, 0)},
    {"name": "Tokyo cash", "tz": "Asia/Tokyo", "start": time(9, 0), "end": time(15, 30)},
    {"name": "Hong Kong cash", "tz": "Asia/Hong_Kong", "start": time(9, 30), "end": time(16, 0)},
    {"name": "Shanghai cash", "tz": "Asia/Shanghai", "start": time(9, 30), "end": time(15, 0)},
    {"name": "India cash", "tz": "Asia/Kolkata", "start": time(9, 15), "end": time(15, 30)},
    {"name": "UK cash", "tz": "Europe/London", "start": time(8, 0), "end": time(16, 30)},
    {"name": "Europe cash", "tz": "Europe/Paris", "start": time(9, 0), "end": time(17, 30)},
    {"name": "US cash", "tz": "America/New_York", "start": time(9, 30), "end": time(16, 0)},
)


def _status(now_utc: datetime, exchange: dict[str, object]) -> str:
    local = now_utc.astimezone(ZoneInfo(str(exchange["tz"])))
    if local.weekday() >= 5:
        return "closed"
    tod = local.timetz().replace(tzinfo=None)
    start = exchange["start"]
    end = exchange["end"]
    if start <= tod < end:
        return "open"
    if tod >= end:
        return "recently_closed"
    return "opening_next"


def build_market_clock_context(profile, now_local: datetime, session_template_item: SessionTemplateItem) -> dict:
    tz_name = getattr(profile, "timezone", "UTC") or "UTC"
    now = now_local if now_local.tzinfo else now_local.replace(tzinfo=ZoneInfo(tz_name))
    now_utc = now.astimezone(ZoneInfo("UTC"))

    open_now: list[str] = []
    recently_closed: list[str] = []
    opening_next: list[str] = []

    for exchange in _EXCHANGES:
        s = _status(now_utc, exchange)
        name = str(exchange["name"])
        if s == "open":
            open_now.append(name)
        elif s == "recently_closed":
            recently_closed.append(name)
        elif s == "opening_next":
            opening_next.append(name)

    return {
        "open_now": open_now[:4] or ([session_template_item.open_now_hint] if session_template_item.open_now_hint else []),
        "recently_closed": recently_closed[:4] or ([session_template_item.recently_closed_hint] if session_template_item.recently_closed_hint else []),
        "opening_next": opening_next[:4] or ([session_template_item.opening_next_hint] if session_template_item.opening_next_hint else []),
        "focus": session_template_item.focus,
        "timezone": tz_name,
        "local_time": now.strftime("%Y-%m-%d %H:%M"),
    }

