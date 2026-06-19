"""Deterministic, testable market holiday and session functions.

Holiday tables are static and require no live API calls.  All 2026 dates
are hardcoded; add subsequent years by extending the per-exchange sets.

Key facts for 2026-05-25 (Memorial Day / UK Spring Bank Holiday):
- NYSE (US cash) is CLOSED.
- LSE (UK equities) is CLOSED.
- Xetra (DAX) is OPEN  - Whit Monday is not an Xetra closure.
- Euronext (CAC, IBEX, EURO STOXX) is OPEN  - Whit Monday is not a
  Euronext closure.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# 2026 holiday tables (static, no external dependency)
# ---------------------------------------------------------------------------

_US_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 7, 3),   # Independence Day (observed Friday)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
})

_US_HOLIDAY_NAMES_2026: dict[date, str] = {
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 1, 19):  "Martin Luther King Jr. Day",
    date(2026, 2, 16):  "Presidents' Day",
    date(2026, 4, 3):   "Good Friday",
    date(2026, 5, 25):  "Memorial Day",
    date(2026, 7, 3):   "Independence Day (observed)",
    date(2026, 9, 7):   "Labor Day",
    date(2026, 11, 26): "Thanksgiving",
    date(2026, 12, 25): "Christmas Day",
}

_UK_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),   # New Year's Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 5, 5),   # Early May Bank Holiday
    date(2026, 5, 25),  # Spring Bank Holiday
    date(2026, 8, 31),  # Summer Bank Holiday
    date(2026, 12, 25), # Christmas Day
    date(2026, 12, 28), # Boxing Day (observed Monday)
})

_UK_HOLIDAY_NAMES_2026: dict[date, str] = {
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 4, 3):   "Good Friday",
    date(2026, 4, 6):   "Easter Monday",
    date(2026, 5, 5):   "Early May Bank Holiday",
    date(2026, 5, 25):  "Spring Bank Holiday",
    date(2026, 8, 31):  "Summer Bank Holiday",
    date(2026, 12, 25): "Christmas Day",
    date(2026, 12, 28): "Boxing Day (observed)",
}

# Xetra (Frankfurt / DAX): Whit Monday (2026-05-25) is NOT a closure.
_XETRA_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),   # New Year's Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 12, 24), # Christmas Eve (Xetra closes early / full closure)
    date(2026, 12, 25), # Christmas Day
    date(2026, 12, 31), # New Year's Eve (Xetra closes early / full closure)
})

_XETRA_HOLIDAY_NAMES_2026: dict[date, str] = {
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 4, 3):   "Good Friday",
    date(2026, 4, 6):   "Easter Monday",
    date(2026, 12, 24): "Christmas Eve",
    date(2026, 12, 25): "Christmas Day",
    date(2026, 12, 31): "New Year's Eve",
}

# Euronext (CAC, IBEX, EURO STOXX, AEX): Whit Monday is NOT a closure.
_EURONEXT_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 1),   # New Year's Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 12, 25), # Christmas Day
    date(2026, 12, 26), # Boxing Day
})

_EURONEXT_HOLIDAY_NAMES_2026: dict[date, str] = {
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 4, 3):   "Good Friday",
    date(2026, 4, 6):   "Easter Monday",
    date(2026, 12, 25): "Christmas Day",
    date(2026, 12, 26): "Boxing Day",
}


# ---------------------------------------------------------------------------
# Holiday predicate functions
# ---------------------------------------------------------------------------

def is_us_market_holiday(d: date) -> bool:
    """Return True when NYSE is closed for a US holiday on date d."""
    if d.year == 2026:
        return d in _US_HOLIDAYS_2026
    # Fallback: weekends are not holidays (caller handles weekends separately).
    return False


def us_holiday_name(d: date) -> str | None:
    """Return the NYSE holiday name for d, or None if not a holiday."""
    if d.year == 2026:
        return _US_HOLIDAY_NAMES_2026.get(d)
    return None


def is_uk_market_holiday(d: date) -> bool:
    """Return True when LSE is closed for a UK bank holiday on date d."""
    if d.year == 2026:
        return d in _UK_HOLIDAYS_2026
    return False


def uk_holiday_name(d: date) -> str | None:
    """Return the LSE holiday name for d, or None if not a holiday."""
    if d.year == 2026:
        return _UK_HOLIDAY_NAMES_2026.get(d)
    return None


def is_xetra_holiday(d: date) -> bool:
    """Return True when Xetra (DAX) is closed for a holiday on date d.

    Whit Monday (2026-05-25) is OPEN on Xetra.
    """
    if d.year == 2026:
        return d in _XETRA_HOLIDAYS_2026
    return False


def is_euronext_holiday(d: date) -> bool:
    """Return True when Euronext (CAC/IBEX/EURO STOXX) is closed on date d.

    Whit Monday (2026-05-25) is OPEN on Euronext.
    """
    if d.year == 2026:
        return d in _EURONEXT_HOLIDAYS_2026
    return False


# ---------------------------------------------------------------------------
# Exchange session predicates (takes UTC datetime)
# ---------------------------------------------------------------------------

_NYSE_OPEN = time(14, 30)   # 09:30 ET = 14:30 UTC (standard time offset May)
_NYSE_CLOSE = time(21, 0)   # 16:00 ET = 21:00 UTC

_XETRA_OPEN = time(8, 0)    # 09:00 CET = 08:00 UTC (CEST, +1 UTC in summer)
_XETRA_CLOSE = time(16, 30) # 17:30 CET = 16:30 UTC

_EURONEXT_OPEN = time(8, 0)
_EURONEXT_CLOSE = time(16, 30)


def is_us_cash_open(dt_utc: datetime) -> bool:
    """Return True when NYSE cash session is active at dt_utc.

    Accounts for weekends and US market holidays.
    """
    d = dt_utc.date()
    if d.weekday() >= 5:
        return False
    if is_us_market_holiday(d):
        return False
    tod = dt_utc.time().replace(tzinfo=None)
    return _NYSE_OPEN <= tod < _NYSE_CLOSE


def is_xetra_open(dt_utc: datetime) -> bool:
    """Return True when Xetra (DAX) cash session is active at dt_utc."""
    d = dt_utc.date()
    if d.weekday() >= 5:
        return False
    if is_xetra_holiday(d):
        return False
    tod = dt_utc.time().replace(tzinfo=None)
    return _XETRA_OPEN <= tod < _XETRA_CLOSE


def is_euronext_open(dt_utc: datetime) -> bool:
    """Return True when Euronext (CAC/IBEX/EURO STOXX) is active at dt_utc."""
    d = dt_utc.date()
    if d.weekday() >= 5:
        return False
    if is_euronext_holiday(d):
        return False
    tod = dt_utc.time().replace(tzinfo=None)
    return _EURONEXT_OPEN <= tod < _EURONEXT_CLOSE


def is_lse_open(dt_utc: datetime) -> bool:
    """Return True when LSE (UK equities) is active at dt_utc."""
    d = dt_utc.date()
    if d.weekday() >= 5:
        return False
    if is_uk_market_holiday(d):
        return False
    # LSE: 08:00-16:30 BST = 07:00-15:30 UTC (summer); use 07:00-15:30 UTC in May.
    tod = dt_utc.time().replace(tzinfo=None)
    return time(7, 0) <= tod < time(15, 30)


# ---------------------------------------------------------------------------
# Regional market status summary
# ---------------------------------------------------------------------------

def regional_market_status(dt_utc: datetime) -> dict[str, str]:
    """Return a status dict for each major region at dt_utc.

    Possible status values per region:
      "open"                  - cash session is live
      "closed"                - outside session hours, regular trading day
      "holiday:<name>"        - exchange closed for named holiday
      "pre_market"            - before cash open (US only, weekday non-holiday)
      "after_hours"           - after cash close (US only, weekday non-holiday)
      "weekend"               - Saturday or Sunday
      "partial:<detail>"      - some exchanges in the region open, some closed
    """
    d = dt_utc.date()
    result: dict[str, str] = {}

    # US cash
    if d.weekday() >= 5:
        result["us_cash"] = "weekend"
    elif is_us_market_holiday(d):
        name = us_holiday_name(d) or "market holiday"
        result["us_cash"] = f"holiday:{name}"
    else:
        tod = dt_utc.time().replace(tzinfo=None)
        if tod < _NYSE_OPEN:
            result["us_cash"] = "pre_market"
        elif _NYSE_OPEN <= tod < _NYSE_CLOSE:
            result["us_cash"] = "open"
        else:
            result["us_cash"] = "after_hours"

    # UK equities
    if d.weekday() >= 5:
        result["uk_equities"] = "weekend"
    elif is_uk_market_holiday(d):
        name = uk_holiday_name(d) or "bank holiday"
        result["uk_equities"] = f"holiday:{name}"
    elif is_lse_open(dt_utc):
        result["uk_equities"] = "open"
    else:
        result["uk_equities"] = "closed"

    # Continental Europe: Xetra + Euronext
    xetra_open = is_xetra_open(dt_utc)
    euronext_open = is_euronext_open(dt_utc)
    xetra_holiday = is_xetra_holiday(d)
    euronext_holiday = is_euronext_holiday(d)

    if d.weekday() >= 5:
        result["continental_europe"] = "weekend"
    elif xetra_open or euronext_open:
        if is_uk_market_holiday(d):
            result["continental_europe"] = "partial:UK closed, continental open"
        else:
            result["continental_europe"] = "open"
    elif xetra_holiday and euronext_holiday:
        result["continental_europe"] = "holiday:exchange closure"
    else:
        result["continental_europe"] = "closed"

    # Asia (heuristic: closed session check based on UTC time of generation)
    # Asian markets close before European/US open; classify as prior/closed context.
    if d.weekday() >= 5:
        result["asia"] = "weekend"
    else:
        # Tokyo 09:00-15:30 JST = 00:00-06:30 UTC; HK 09:30-16:00 HKT = 01:30-08:00 UTC
        tod = dt_utc.time().replace(tzinfo=None)
        if time(1, 30) <= tod < time(8, 0):
            result["asia"] = "open"
        elif tod < time(1, 30) or tod >= time(8, 0):
            result["asia"] = "closed"
        else:
            result["asia"] = "closed"

    return result


def us_cash_holiday_name(d: date) -> str | None:
    """Return the name of the US market holiday on d, or None."""
    return us_holiday_name(d) if is_us_market_holiday(d) else None
