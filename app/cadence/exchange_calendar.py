"""Lightweight exchange calendar for non-US markets.

Covers the most common European bank holidays that cause quote staleness
in a morning briefing context. Intentionally minimal: no external dependency,
no full calendar library. Add exchange codes as needed.

Supported exchanges:
  - LON / FTSE: UK bank holidays
  - EPA / PAR: French public holidays
  - XETRA / FRA: German public holidays

Usage:
    is_closed, reason = is_exchange_closed("FTSE", date(2026, 5, 4))
    # True, "UK Early May Bank Holiday"
"""

from __future__ import annotations

from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Holiday helpers
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of `weekday` (Mon=0) in a given month."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of `weekday` in a given month."""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    cursor = next_month - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm for Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Per-exchange holiday sets
# ---------------------------------------------------------------------------

def _uk_holidays(year: int) -> set[date]:
    """UK bank holidays relevant to LSE/FTSE."""
    easter_sunday = _easter(year)
    holidays = {
        date(year, 1, 1),                          # New Year's Day
        easter_sunday - timedelta(days=2),          # Good Friday
        easter_sunday + timedelta(days=1),          # Easter Monday
        _nth_weekday(year, 5, 0, 1),               # Early May bank holiday
        _last_weekday(year, 5, 0),                 # Late May bank holiday (Spring)
        _last_weekday(year, 8, 0),                 # August bank holiday
        date(year, 12, 25),                        # Christmas Day
        date(year, 12, 26),                        # Boxing Day
    }
    # Roll Saturday → Friday, Sunday → Monday for fixed-date holidays
    rolled: set[date] = set()
    for h in holidays:
        if h.weekday() == 5:
            rolled.add(h - timedelta(days=1))
        elif h.weekday() == 6:
            rolled.add(h + timedelta(days=1))
        else:
            rolled.add(h)
    return rolled


def _french_holidays(year: int) -> set[date]:
    easter_sunday = _easter(year)
    return {
        date(year, 1, 1),
        easter_sunday + timedelta(days=1),
        date(year, 5, 1),
        date(year, 5, 8),
        easter_sunday + timedelta(days=39),        # Ascension
        date(year, 7, 14),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 11, 11),
        date(year, 12, 25),
    }


def _german_holidays(year: int) -> set[date]:
    easter_sunday = _easter(year)
    return {
        date(year, 1, 1),
        easter_sunday - timedelta(days=2),         # Good Friday
        easter_sunday + timedelta(days=1),          # Easter Monday
        date(year, 5, 1),
        easter_sunday + timedelta(days=39),         # Ascension
        easter_sunday + timedelta(days=50),         # Whit Monday
        date(year, 10, 3),
        date(year, 12, 25),
        date(year, 12, 26),
    }


# Exchange code → holiday generator
_EXCHANGE_HOLIDAYS: dict[str, type[set]] = {}

_HOLIDAY_GENERATORS: dict[str, object] = {
    "LON": _uk_holidays,
    "FTSE": _uk_holidays,
    "UKX": _uk_holidays,
    "LSE": _uk_holidays,
    "EPA": _french_holidays,
    "PAR": _french_holidays,
    "CAC": _french_holidays,
    "XETRA": _german_holidays,
    "FRA": _german_holidays,
    "DAX": _german_holidays,
}

# Cache computed holiday sets so we don't recompute per call
_CACHE: dict[tuple[str, int], set[date]] = {}


def _holidays_for(exchange: str, year: int) -> set[date]:
    key = (exchange.upper(), year)
    if key not in _CACHE:
        generator = _HOLIDAY_GENERATORS.get(exchange.upper())
        _CACHE[key] = generator(year) if generator else set()
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_exchange_closed(exchange_or_symbol: str, dt: date) -> tuple[bool, str]:
    """Return (is_closed, reason) for a given exchange/symbol and date.

    Returns (False, "") when the exchange is open or unknown.
    """
    code = exchange_or_symbol.upper()
    # Weekends are always closed
    if dt.weekday() >= 5:
        return True, "weekend"
    holidays = _holidays_for(code, dt.year)
    if dt in holidays:
        # Find the holiday name by reconstruction
        names = _holiday_names(code, dt.year)
        return True, names.get(dt, "public holiday")
    return False, ""


def _holiday_names(exchange: str, year: int) -> dict[date, str]:
    """Build a best-effort name map for the holidays of a given exchange/year."""
    generator = _HOLIDAY_GENERATORS.get(exchange.upper())
    if not generator:
        return {}
    easter_sunday = _easter(year)
    # Build a rough name map — used only for display
    candidates: dict[date, str] = {
        date(year, 1, 1): "New Year's Day",
        easter_sunday - timedelta(days=2): "Good Friday",
        easter_sunday + timedelta(days=1): "Easter Monday",
        date(year, 5, 1): "Labour Day",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 26): "Boxing Day",
    }
    if exchange.upper() in {"LON", "FTSE", "UKX", "LSE"}:
        candidates[_nth_weekday(year, 5, 0, 1)] = "UK Early May Bank Holiday"
        candidates[_last_weekday(year, 5, 0)] = "UK Late May Bank Holiday"
        candidates[_last_weekday(year, 8, 0)] = "UK August Bank Holiday"
    if exchange.upper() in {"EPA", "PAR", "CAC"}:
        candidates[date(year, 5, 8)] = "Victory in Europe Day"
        candidates[date(year, 7, 14)] = "Bastille Day"
    if exchange.upper() in {"XETRA", "FRA", "DAX"}:
        candidates[date(year, 10, 3)] = "German Unity Day"
    return candidates


def exchange_for_symbol(symbol: str) -> str | None:
    """Map a known index/ETF symbol to an exchange code for holiday checks."""
    _MAP: dict[str, str] = {
        "^FTSE": "FTSE",
        "FTSE": "FTSE",
        "UKX": "FTSE",
        "^CAC40": "CAC",
        "CAC": "CAC",
        "^FCHI": "CAC",
        "^GDAXI": "DAX",
        "DAX": "DAX",
        "^STOXX50E": "XETRA",
    }
    return _MAP.get(symbol.upper())
