"""Tests for app.markets.calendar: holiday predicates, session predicates, and
regional_market_status for 2026.

All tests are deterministic and require no live API calls.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.markets.calendar import (
    is_euronext_holiday,
    is_us_cash_open,
    is_us_market_holiday,
    is_uk_market_holiday,
    is_xetra_holiday,
    is_xetra_open,
    is_euronext_open,
    is_lse_open,
    regional_market_status,
    us_holiday_name,
    uk_holiday_name,
    us_cash_holiday_name,
)


# ---------------------------------------------------------------------------
# US holiday table
# ---------------------------------------------------------------------------

class TestUSHolidays:
    def test_memorial_day_2026(self):
        assert is_us_market_holiday(date(2026, 5, 25))

    def test_memorial_day_name(self):
        assert us_holiday_name(date(2026, 5, 25)) == "Memorial Day"

    def test_good_friday_2026(self):
        assert is_us_market_holiday(date(2026, 4, 3))

    def test_christmas_2026(self):
        assert is_us_market_holiday(date(2026, 12, 25))

    def test_regular_weekday_not_holiday(self):
        # 2026-05-26 is a regular Tuesday
        assert not is_us_market_holiday(date(2026, 5, 26))

    def test_weekend_not_in_us_holiday_table(self):
        # Weekends are handled separately by is_us_cash_open; not in holiday table
        assert not is_us_market_holiday(date(2026, 5, 23))  # Saturday


# ---------------------------------------------------------------------------
# UK holiday table
# ---------------------------------------------------------------------------

class TestUKHolidays:
    def test_spring_bank_holiday_2026(self):
        assert is_uk_market_holiday(date(2026, 5, 25))

    def test_spring_bank_holiday_name(self):
        assert uk_holiday_name(date(2026, 5, 25)) == "Spring Bank Holiday"

    def test_good_friday_2026(self):
        assert is_uk_market_holiday(date(2026, 4, 3))

    def test_easter_monday_2026(self):
        assert is_uk_market_holiday(date(2026, 4, 6))

    def test_regular_weekday_not_uk_holiday(self):
        assert not is_uk_market_holiday(date(2026, 5, 26))


# ---------------------------------------------------------------------------
# Xetra: Whit Monday is OPEN
# ---------------------------------------------------------------------------

class TestXetraHolidays:
    def test_whit_monday_not_xetra_holiday(self):
        """2026-05-25 (Whit Monday) is NOT a Xetra closure."""
        assert not is_xetra_holiday(date(2026, 5, 25))

    def test_good_friday_is_xetra_holiday(self):
        assert is_xetra_holiday(date(2026, 4, 3))

    def test_christmas_is_xetra_holiday(self):
        assert is_xetra_holiday(date(2026, 12, 25))

    def test_regular_day_not_xetra_holiday(self):
        assert not is_xetra_holiday(date(2026, 5, 26))


# ---------------------------------------------------------------------------
# Euronext: Whit Monday is OPEN
# ---------------------------------------------------------------------------

class TestEuronextHolidays:
    def test_whit_monday_not_euronext_holiday(self):
        """2026-05-25 (Whit Monday) is NOT a Euronext closure."""
        assert not is_euronext_holiday(date(2026, 5, 25))

    def test_good_friday_is_euronext_holiday(self):
        assert is_euronext_holiday(date(2026, 4, 3))

    def test_regular_day_not_euronext_holiday(self):
        assert not is_euronext_holiday(date(2026, 5, 26))


# ---------------------------------------------------------------------------
# is_us_cash_open: holiday + weekend + hours
# ---------------------------------------------------------------------------

class TestUSCashOpen:
    def _utc(self, y, m, d, h, mi=0) -> datetime:
        return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("UTC"))

    def test_memorial_day_cash_not_open(self):
        """NYSE is closed all day on Memorial Day 2026."""
        # 15:00 UTC = 11:00 ET (normally in session)
        dt = self._utc(2026, 5, 25, 15, 0)
        assert not is_us_cash_open(dt)

    def test_normal_weekday_during_session(self):
        # 2026-05-26 15:00 UTC = 11:00 ET, regular Tuesday
        dt = self._utc(2026, 5, 26, 15, 0)
        assert is_us_cash_open(dt)

    def test_normal_weekday_before_session(self):
        # 2026-05-26 12:00 UTC = 08:00 ET, pre-market
        dt = self._utc(2026, 5, 26, 12, 0)
        assert not is_us_cash_open(dt)

    def test_weekend_not_open(self):
        dt = self._utc(2026, 5, 23, 15, 0)  # Saturday
        assert not is_us_cash_open(dt)


# ---------------------------------------------------------------------------
# is_xetra_open: Whit Monday is open
# ---------------------------------------------------------------------------

class TestXetraOpen:
    def _utc(self, y, m, d, h, mi=0) -> datetime:
        return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("UTC"))

    def test_whit_monday_xetra_open_during_hours(self):
        """2026-05-25 10:00 UTC (12:00 CEST) is during Xetra hours and not a holiday."""
        dt = self._utc(2026, 5, 25, 10, 0)
        assert is_xetra_open(dt)

    def test_good_friday_xetra_closed(self):
        dt = self._utc(2026, 4, 3, 10, 0)
        assert not is_xetra_open(dt)

    def test_normal_weekday_during_hours(self):
        dt = self._utc(2026, 5, 26, 10, 0)
        assert is_xetra_open(dt)

    def test_weekend_xetra_closed(self):
        dt = self._utc(2026, 5, 23, 10, 0)
        assert not is_xetra_open(dt)


# ---------------------------------------------------------------------------
# is_euronext_open: Whit Monday is open
# ---------------------------------------------------------------------------

class TestEuronextOpen:
    def _utc(self, y, m, d, h, mi=0) -> datetime:
        return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("UTC"))

    def test_whit_monday_euronext_open(self):
        dt = self._utc(2026, 5, 25, 10, 0)
        assert is_euronext_open(dt)

    def test_good_friday_euronext_closed(self):
        dt = self._utc(2026, 4, 3, 10, 0)
        assert not is_euronext_open(dt)


# ---------------------------------------------------------------------------
# regional_market_status: Memorial Day / Spring Bank Holiday
# ---------------------------------------------------------------------------

class TestRegionalMarketStatus:
    def _utc(self, y, m, d, h, mi=0) -> datetime:
        return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("UTC"))

    def test_memorial_day_us_cash_is_holiday(self):
        dt = self._utc(2026, 5, 25, 15, 0)
        status = regional_market_status(dt)
        assert status["us_cash"].startswith("holiday:")
        assert "Memorial Day" in status["us_cash"]

    def test_memorial_day_uk_equities_is_holiday(self):
        dt = self._utc(2026, 5, 25, 10, 0)
        status = regional_market_status(dt)
        assert status["uk_equities"].startswith("holiday:")

    def test_memorial_day_continental_europe_open(self):
        """Continental Europe is open on 2026-05-25 (Whit Monday not a closure)."""
        dt = self._utc(2026, 5, 25, 10, 0)
        status = regional_market_status(dt)
        # Should be "partial:UK closed, continental open" or "open"
        assert status["continental_europe"] in {"open", "partial:UK closed, continental open"}

    def test_normal_tuesday_us_cash_open(self):
        dt = self._utc(2026, 5, 26, 15, 0)
        status = regional_market_status(dt)
        assert status["us_cash"] == "open"

    def test_normal_tuesday_uk_equities_open(self):
        dt = self._utc(2026, 5, 26, 9, 0)
        status = regional_market_status(dt)
        assert status["uk_equities"] == "open"

    def test_weekend_all_regions_weekend(self):
        dt = self._utc(2026, 5, 23, 12, 0)  # Saturday
        status = regional_market_status(dt)
        assert status["us_cash"] == "weekend"
        assert status["continental_europe"] == "weekend"

    def test_us_cash_holiday_name_helper(self):
        assert us_cash_holiday_name(date(2026, 5, 25)) == "Memorial Day"

    def test_us_cash_holiday_name_none_on_regular_day(self):
        assert us_cash_holiday_name(date(2026, 5, 26)) is None
