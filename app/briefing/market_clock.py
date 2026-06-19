"""Deterministic market clock context for session outputs.

Holiday awareness is applied via app.markets.calendar so that US cash
shows "closed: <holiday name>" rather than "opening next" on NYSE holidays.
"""

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

# Exchange names that map to holiday checks in app.markets.calendar.
_US_CASH_EXCHANGE_NAME = "US cash"
_UK_CASH_EXCHANGE_NAME = "UK cash"


def _us_holiday_name_for_utc(now_utc: datetime) -> str | None:
    """Return the US market holiday name for the NYSE local date, or None."""
    try:
        from app.markets.calendar import us_cash_holiday_name
        ny_date = now_utc.astimezone(ZoneInfo("America/New_York")).date()
        return us_cash_holiday_name(ny_date)
    except Exception:
        return None


def _uk_holiday_for_utc(now_utc: datetime) -> str | None:
    """Return the UK bank holiday name for the LSE local date, or None."""
    try:
        from app.markets.calendar import uk_holiday_name, is_uk_market_holiday
        ld = now_utc.astimezone(ZoneInfo("Europe/London")).date()
        if is_uk_market_holiday(ld):
            return uk_holiday_name(ld)
        return None
    except Exception:
        return None


def _status(now_utc: datetime, exchange: dict[str, object]) -> str:
    local = now_utc.astimezone(ZoneInfo(str(exchange["tz"])))
    if local.weekday() >= 5:
        return "closed"

    # Apply US holiday check before time-of-day check.
    name = str(exchange["name"])
    if name == _US_CASH_EXCHANGE_NAME:
        h = _us_holiday_name_for_utc(now_utc)
        if h:
            return f"holiday:{h}"

    if name == _UK_CASH_EXCHANGE_NAME:
        h = _uk_holiday_for_utc(now_utc)
        if h:
            return f"holiday:{h}"

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
    holiday_notes: list[str] = []

    for exchange in _EXCHANGES:
        s = _status(now_utc, exchange)
        name = str(exchange["name"])
        if s == "open":
            open_now.append(name)
        elif s == "recently_closed":
            recently_closed.append(name)
        elif s == "opening_next":
            opening_next.append(name)
        elif s.startswith("holiday:"):
            holiday_name = s[len("holiday:"):]
            holiday_notes.append(f"{name} closed: {holiday_name}")

    open_now_values = open_now[:4]
    open_now_source = "computed"
    if not open_now_values:
        open_now_values = [session_template_item.open_now_hint] if session_template_item.open_now_hint else []
        open_now_source = "fallback"

    recently_closed_values = recently_closed[:4]
    recently_closed_source = "computed"
    if not recently_closed_values:
        recently_closed_values = [session_template_item.recently_closed_hint] if session_template_item.recently_closed_hint else []
        recently_closed_source = "fallback"

    # On US holidays, do NOT add US cash to opening_next.
    opening_next_values = opening_next[:4]
    opening_next_source = "computed"
    if not opening_next_values:
        opening_next_values = [session_template_item.opening_next_hint] if session_template_item.opening_next_hint else []
        opening_next_source = "fallback"

    return {
        "open_now": open_now_values,
        "recently_closed": recently_closed_values,
        "opening_next": opening_next_values,
        "holiday_notes": holiday_notes,
        "open_now_source": open_now_source,
        "recently_closed_source": recently_closed_source,
        "opening_next_source": opening_next_source,
        "focus": session_template_item.focus,
        "timezone": tz_name,
        "local_time": now.strftime("%Y-%m-%d %H:%M"),
    }
