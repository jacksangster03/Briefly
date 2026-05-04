"""Session-aware routing labels for briefing title/mode selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionWindow:
    key: str
    title: str
    start: time
    end: time


SESSION_WINDOWS: tuple[SessionWindow, ...] = (
    SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30)),
    SessionWindow("late_morning", "Late Morning Update", time(10, 30), time(13, 30)),
    SessionWindow("pre_us_open", "Pre-US Open Update", time(13, 30), time(15, 25)),
    SessionWindow("intraday", "Intraday Update", time(15, 30), time(17, 30)),
    SessionWindow("into_close", "Into Close Update", time(17, 30), time(22, 0)),
)


def resolve_session_window(*, now: datetime, timezone_name: str) -> SessionWindow:
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    tod = local_now.timetz().replace(tzinfo=None)
    for window in SESSION_WINDOWS:
        if window.start <= tod < window.end:
            return window
    return SessionWindow("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59))

