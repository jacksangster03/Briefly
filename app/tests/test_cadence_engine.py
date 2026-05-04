"""Tests for cadence and decision engines (timezone/DST/market window)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.cadence.engine import CadenceEngine, DecisionEngine
from app.cadence.state_store import record_cadence_marker
from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "cadence_test.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()


def test_intraday_window_uses_dynamic_tz_conversion_across_dst():
    settings = Settings(timezone="Europe/Madrid")
    cadence = CadenceEngine(settings, local_timezone="Europe/Madrid")

    # Before EU DST switch (NY already in DST): typical open is 14:30 Madrid.
    before_dst = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
    before = cadence.intraday_preopen_window(before_dst)

    # After EU DST switch: open shifts to 15:30 Madrid.
    after_dst = datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc)
    after = cadence.intraday_preopen_window(after_dst)

    assert before.us_cash_open_local.hour != after.us_cash_open_local.hour


def test_intraday_decision_skips_us_holiday(isolated_db):
    settings = Settings(timezone="Europe/Madrid")
    decision_engine = DecisionEngine(settings, profile_name="default_user", local_timezone="Europe/Madrid")
    now = datetime(2026, 12, 25, 13, 0, tzinfo=timezone.utc)  # Christmas Day (NYSE closed)
    decision = decision_engine.decide_intraday(now=now)
    assert decision.action_type == "no_action"
    assert "US market closed" in decision.reason


def test_intraday_decision_sends_once_per_intraday_slot(isolated_db):
    settings = Settings(timezone="Europe/Madrid")
    decision_engine = DecisionEngine(settings, profile_name="default_user", local_timezone="Europe/Madrid")
    # 2026-04-13 12:31 UTC => 14:31 local, inside first configured slot window
    now = datetime(2026, 4, 13, 12, 31, tzinfo=timezone.utc)
    decision = decision_engine.decide_intraday(now=now)
    assert decision.action_type == "send_intraday"
    marker_key = decision.metadata.get("marker_key")
    assert marker_key
    assert marker_key.startswith("intraday:2026-04-13:")

    record_cadence_marker(
        profile_name="default_user",
        marker_key=marker_key,
        action_type="intraday",
        local_date=decision_engine.cadence.now_local(now).date(),
        local_timezone="Europe/Madrid",
        sent_at_local=decision_engine.cadence.now_local(now),
    )
    second = decision_engine.decide_intraday(now=now + timedelta(seconds=30))
    assert second.action_type == "no_action"
    assert "slot already sent" in second.reason.lower()


def test_intraday_decision_between_slots_is_no_action(isolated_db):
    settings = Settings(timezone="Europe/Madrid")
    decision_engine = DecisionEngine(settings, profile_name="default_user", local_timezone="Europe/Madrid")
    # 14:40 local is outside the 2-minute slot capture window when start=14:30 interval=60
    now = datetime(2026, 4, 13, 12, 40, tzinfo=timezone.utc)
    decision = decision_engine.decide_intraday(now=now)
    assert decision.action_type == "no_action"
    assert "between intraday schedule slots" in decision.reason.lower()


def test_morning_decision_respects_marker(isolated_db):
    settings = Settings(timezone="Europe/Madrid")
    decision_engine = DecisionEngine(settings, profile_name="default_user", local_timezone="Europe/Madrid")
    now = datetime(2026, 4, 14, 7, 0, tzinfo=timezone.utc)  # 09:00 local (in morning window)
    first = decision_engine.decide_morning(now=now, target_hhmm="08:45")
    assert first.action_type == "send_morning"

    local_now = decision_engine.cadence.now_local(now)
    record_cadence_marker(
        profile_name="default_user",
        marker_key=f"morning:{local_now.date().isoformat()}",
        action_type="morning",
        local_date=local_now.date(),
        local_timezone="Europe/Madrid",
        sent_at_local=local_now,
    )
    second = decision_engine.decide_morning(now=now + timedelta(minutes=10), target_hhmm="08:45")
    assert second.action_type == "no_action"
