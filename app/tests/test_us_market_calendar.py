"""NYSE holiday and half-day calendar tests for cadence gating."""

from __future__ import annotations

from datetime import date

from app.cadence.us_market_calendar import session_for_ny_date


def test_nyse_closed_on_good_friday_2026():
    # 2026-04-03 is Good Friday.
    session = session_for_ny_date(date(2026, 4, 3))
    assert session.is_open_day is False
    assert session.reason == "nyse_holiday"


def test_nyse_half_day_day_after_thanksgiving_2026():
    # Thanksgiving 2026 is Nov 26; half-day is Nov 27.
    session = session_for_ny_date(date(2026, 11, 27))
    assert session.is_open_day is True
    assert session.is_half_day is True


def test_nyse_closed_on_weekend():
    session = session_for_ny_date(date(2026, 4, 11))  # Saturday
    assert session.is_open_day is False
    assert session.reason == "weekend"
