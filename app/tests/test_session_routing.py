"""Tests for holiday-aware session routing.

Key test: 2026-05-25 13:30 Europe/Madrid must NOT produce "US Pre-Open Setup"
because NYSE is closed for Memorial Day.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.briefing.session_routing import (
    SESSION_WINDOWS,
    SATURDAY_WINDOW,
    SUNDAY_WINDOW,
    resolve_session_window,
)


def _dt(year: int, month: int, day: int, hour: int, minute: int, tz: str = "Europe/Madrid") -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


class TestMemorialDayRouting:
    """2026-05-25 is Memorial Day; US cash is closed."""

    def test_pre_open_slot_not_us_pre_open_on_memorial_day(self):
        """13:30 CEST on Memorial Day must NOT be 'US Pre-Open Setup'."""
        dt = _dt(2026, 5, 25, 13, 30)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key != "us_pre_open", (
            f"Expected holiday override, got '{window.key}' with title '{window.title}'"
        )

    def test_pre_open_slot_is_holiday_handoff_on_memorial_day(self):
        """13:30 CEST on Memorial Day should route to holiday handoff session."""
        dt = _dt(2026, 5, 25, 13, 30)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "us_holiday_handoff"
        assert "Holiday" in window.title

    def test_intraday_slot_is_holiday_risk_on_memorial_day(self):
        """15:30 CEST on Memorial Day should route to holiday risk session."""
        dt = _dt(2026, 5, 25, 15, 30)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key in {"us_holiday_risk", "us_holiday_handoff"}

    def test_morning_session_unchanged_on_memorial_day(self):
        """Morning session (08:00 CEST) is not affected by US holiday."""
        dt = _dt(2026, 5, 25, 8, 0)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "morning"

    def test_europe_midday_unchanged_on_memorial_day(self):
        """Europe Midday Check at 11:00 CEST is not affected by US holiday."""
        dt = _dt(2026, 5, 25, 11, 0)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "europe_midday"


class TestNormalWeekdayRouting:
    """Non-holiday weekday behaviour must be unchanged."""

    def test_us_pre_open_on_regular_tuesday(self):
        """13:30 CEST on a regular Tuesday should be 'US Pre-Open Setup'."""
        dt = _dt(2026, 5, 26, 13, 30)  # Tuesday 26 May 2026
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "us_pre_open"
        assert window.title == "US Pre-Open Setup"

    def test_morning_on_regular_tuesday(self):
        dt = _dt(2026, 5, 26, 8, 0)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "morning"

    def test_europe_midday_on_regular_tuesday(self):
        dt = _dt(2026, 5, 26, 11, 0)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "europe_midday"

    def test_us_intraday_on_regular_tuesday(self):
        dt = _dt(2026, 5, 26, 15, 30)
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "us_intraday_risk"


class TestWeekendRouting:
    def test_saturday_routes_to_weekend_briefing(self):
        dt = _dt(2026, 5, 23, 10, 0)  # Saturday
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "saturday_weekend_briefing"

    def test_sunday_routes_to_sunday_watch(self):
        dt = _dt(2026, 5, 24, 10, 0)  # Sunday
        window = resolve_session_window(now=dt, timezone_name="Europe/Madrid")
        assert window.key == "sunday_weekend_watch"
