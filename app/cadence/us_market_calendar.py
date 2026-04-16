"""NYSE session helpers with timezone-safe holiday/half-day handling.

Uses deterministic holiday calculations (no external API dependency).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class USMarketSession:
    """US regular session metadata for a New York calendar date."""

    ny_date: date
    is_open_day: bool
    is_half_day: bool
    reason: str = ""


def session_for_ny_date(ny_date: date) -> USMarketSession:
    """Return whether NYSE is open/full/half session for `ny_date`."""
    if ny_date.weekday() >= 5:
        return USMarketSession(ny_date=ny_date, is_open_day=False, is_half_day=False, reason="weekend")

    holidays = _nyse_holidays(ny_date.year)
    if ny_date in holidays:
        return USMarketSession(ny_date=ny_date, is_open_day=False, is_half_day=False, reason="nyse_holiday")

    half_days = _nyse_half_days(ny_date.year, holidays=holidays)
    if ny_date in half_days:
        return USMarketSession(ny_date=ny_date, is_open_day=True, is_half_day=True, reason="half_day")

    return USMarketSession(ny_date=ny_date, is_open_day=True, is_half_day=False, reason="regular_day")


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:  # Saturday -> Friday
        return actual - timedelta(days=1)
    if actual.weekday() == 6:  # Sunday -> Monday
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nyse_holidays(year: int) -> set[date]:
    """Primary NYSE closure set for cadence gating."""
    new_year = _observed_fixed_holiday(year, 1, 1)
    mlk = _nth_weekday(year, 1, weekday=0, n=3)  # third Monday in Jan
    presidents = _nth_weekday(year, 2, weekday=0, n=3)
    good_friday = _easter_sunday(year) - timedelta(days=2)
    memorial = _last_weekday(year, 5, weekday=0)  # last Monday in May
    juneteenth = _observed_fixed_holiday(year, 6, 19)
    independence = _observed_fixed_holiday(year, 7, 4)
    labor = _nth_weekday(year, 9, weekday=0, n=1)
    thanksgiving = _nth_weekday(year, 11, weekday=3, n=4)  # fourth Thursday
    christmas = _observed_fixed_holiday(year, 12, 25)
    return {
        new_year,
        mlk,
        presidents,
        good_friday,
        memorial,
        juneteenth,
        independence,
        labor,
        thanksgiving,
        christmas,
    }


def _nyse_half_days(year: int, *, holidays: set[date]) -> set[date]:
    """Common NYSE early-close days used for cadence awareness."""
    thanksgiving = _nth_weekday(year, 11, weekday=3, n=4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)

    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() >= 5 or christmas_eve in holidays:
        christmas_eve_half = None
    else:
        christmas_eve_half = christmas_eve

    independence_eve = date(year, 7, 3)
    if independence_eve.weekday() >= 5 or independence_eve in holidays:
        independence_eve_half = None
    else:
        independence_eve_half = independence_eve

    half_days = {day_after_thanksgiving}
    if christmas_eve_half:
        half_days.add(christmas_eve_half)
    if independence_eve_half:
        half_days.add(independence_eve_half)
    return half_days


def ny_open_datetime(ny_date: date) -> datetime:
    """09:30 local New York time for a given NY date (naive)."""
    return datetime(ny_date.year, ny_date.month, ny_date.day, 9, 30)
