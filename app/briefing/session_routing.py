"""Session-aware routing labels for briefing title/mode selection.

Holiday overrides are applied via app.markets.calendar so that US market
holidays reroute the 13:30-15:25 slot away from "US Pre-Open Setup" and
into "US Holiday / Europe Handoff".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.briefing.session_templates import get_template_items


@dataclass(frozen=True)
class SessionWindow:
    key: str
    title: str
    start: time
    end: time


SESSION_WINDOWS: tuple[SessionWindow, ...] = (
    SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30)),
    SessionWindow("europe_midday", "Europe Midday Check", time(10, 30), time(13, 30)),
    SessionWindow("us_pre_open", "US Pre-Open Setup", time(13, 30), time(15, 25)),
    SessionWindow("us_intraday_risk", "US Intraday Risk Check", time(15, 30), time(17, 30)),
    SessionWindow("into_close", "Into Close Update", time(17, 30), time(22, 0)),
)

SATURDAY_WINDOW = SessionWindow("saturday_weekend_briefing", "Weekend Briefing", time(6, 0), time(23, 59))
SUNDAY_WINDOW = SessionWindow("sunday_weekend_watch", "Sunday Weekend Watch", time(9, 0), time(23, 59))

# Holiday-aware override windows used when US cash is closed on a weekday.
_US_HOLIDAY_HANDOFF_WINDOW = SessionWindow(
    "us_holiday_handoff",
    "US Holiday / Europe Handoff",
    time(13, 30),
    time(15, 25),
)
_US_HOLIDAY_RISK_WINDOW = SessionWindow(
    "us_holiday_risk",
    "Holiday Risk Check",
    time(15, 30),
    time(17, 30),
)
_US_HOLIDAY_CLOSING_WINDOW = SessionWindow(
    "closing_wrap",
    "Holiday / Next-Day Setup",
    time(22, 0),
    time(23, 59),
)


def _is_us_cash_closed_today(local_now: datetime) -> bool:
    """Return True when US cash equity markets are closed on the local date."""
    try:
        from app.markets.calendar import is_us_market_holiday
        d = local_now.date()
        return d.weekday() >= 5 or is_us_market_holiday(d)
    except Exception:
        return False


def _windows_for_template(template_name: str | None) -> tuple[SessionWindow, ...]:
    name = (template_name or "emea_global").strip().lower()
    if name == "emea_global":
        return SESSION_WINDOWS
    return tuple(
        SessionWindow(
            key=item.key,
            title=item.label,
            start=item.window_start,
            end=item.window_end,
        )
        for item in get_template_items(name)
    )


def resolve_session_window(*, now: datetime, timezone_name: str, session_template: str | None = None) -> SessionWindow:
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    if local_now.weekday() == 5:
        return SATURDAY_WINDOW
    if local_now.weekday() == 6:
        return SUNDAY_WINDOW

    us_holiday = _is_us_cash_closed_today(local_now)
    tod = local_now.timetz().replace(tzinfo=None)
    windows = _windows_for_template(session_template)

    for window in windows:
        if window.start <= tod < window.end:
            # Apply US holiday overrides for US-centric windows.
            if us_holiday and window.key == "us_pre_open":
                return _US_HOLIDAY_HANDOFF_WINDOW
            if us_holiday and window.key == "us_intraday_risk":
                return _US_HOLIDAY_RISK_WINDOW
            return window

    # Small handoff gap between pre-open and intraday belongs to pre-open context.
    if session_template in (None, "", "emea_global") and time(15, 25) <= tod < time(15, 30):
        if us_holiday:
            return _US_HOLIDAY_HANDOFF_WINDOW
        return SessionWindow("us_pre_open", "US Pre-Open Setup", time(13, 30), time(15, 25))

    # Pre-06:00 reads as previous-session wrap until the morning cycle starts.
    if session_template in (None, "", "emea_global"):
        if us_holiday:
            return _US_HOLIDAY_CLOSING_WINDOW
        if tod < time(6, 0):
            return SessionWindow("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59))
        return SessionWindow("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59))
    return windows[0] if windows else SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30))


def session_window_for_key(key: str) -> SessionWindow:
    """Resolve canonical session metadata by key."""
    normalised = (key or "").strip().lower()
    aliases = {
        "midday": "europe_midday",
        "preopen": "us_pre_open",
        "intraday": "us_intraday_risk",
        "close": "into_close",
    }
    mapped = aliases.get(normalised, normalised)
    for window in _windows_for_template("emea_global"):
        if window.key == mapped:
            return window
    if mapped == "saturday_weekend_briefing":
        return SATURDAY_WINDOW
    if mapped == "sunday_weekend_watch":
        return SUNDAY_WINDOW
    if mapped == "closing_wrap":
        return SessionWindow("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59))
    return SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30))


def next_session_window(*, now: datetime, timezone_name: str, session_template: str | None = None) -> SessionWindow:
    """Return the next chronological session window from current local time."""
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    if local_now.weekday() == 5:
        return SUNDAY_WINDOW
    if local_now.weekday() == 6:
        return SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30))
    tod = local_now.timetz().replace(tzinfo=None)
    windows = _windows_for_template(session_template)
    for window in windows:
        if tod < window.start:
            return window
    # After the last daytime window, next session is closing wrap.
    if session_template in (None, "", "emea_global") and tod >= time(22, 0):
        return SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30))
    if session_template in (None, "", "emea_global"):
        return SessionWindow("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59))
    return windows[0] if windows else SessionWindow("morning", "Morning Briefing", time(6, 0), time(10, 30))
