"""Tests for breaking storyline follow-up state machine persistence."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.cadence.state_store import (
    close_breaking_story,
    due_breaking_followups,
    get_breaking_story_state,
    mark_followup_sent,
    upsert_breaking_story_initial,
)
from app.db import session as db_session
from app.db.base import Base, create_app_engine


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "followup_state.db"
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


def test_followup_due_then_sent_once(isolated_db):
    now = datetime.now(timezone.utc)
    upsert_breaking_story_initial(
        profile_name="default_user",
        storyline_key="story-123",
        local_date=date(2026, 4, 16),
        category="geopolitics",
        event_id="evt-1",
        event_title="Iran-US talks turn to interim deal",
        why_markets_care="Risk premium may reprice oil and volatility.",
        watch_symbols=["CL=F", "^VIX", "SPY"],
        first_sent_at=now - timedelta(minutes=12),
        followup_due_at=now - timedelta(minutes=2),
        reason="initial_breaking_sent",
    )

    due = due_breaking_followups(profile_name="default_user", now_utc=now)
    assert len(due) == 1
    assert due[0].storyline_key == "story-123"

    mark_followup_sent(
        profile_name="default_user",
        storyline_key="story-123",
        local_date=date(2026, 4, 16),
        sent_at=now,
        reason="Follow-up: mapped assets confirmed move.",
    )

    updated = get_breaking_story_state(
        profile_name="default_user",
        storyline_key="story-123",
        local_date=date(2026, 4, 16),
    )
    assert updated is not None
    assert updated.state == "followup_sent"
    assert updated.closed is True

    # Once closed, no additional due follow-up should appear.
    due_again = due_breaking_followups(profile_name="default_user", now_utc=now + timedelta(minutes=10))
    assert due_again == []


def test_followup_closed_without_send(isolated_db):
    now = datetime.now(timezone.utc)
    upsert_breaking_story_initial(
        profile_name="default_user",
        storyline_key="story-closed",
        local_date=date(2026, 4, 16),
        category="geopolitics",
        event_id="evt-2",
        event_title="Pakistan says no dates set for second round of US-Iran talks",
        why_markets_care="Geopolitical uncertainty could influence risk sentiment.",
        watch_symbols=["CL=F", "^VIX", "SPY"],
        first_sent_at=now - timedelta(minutes=15),
        followup_due_at=now - timedelta(minutes=1),
        reason="initial_breaking_sent",
    )

    close_breaking_story(
        profile_name="default_user",
        storyline_key="story-closed",
        local_date=date(2026, 4, 16),
        reason="Follow-up closed without confirmation.",
    )

    state = get_breaking_story_state(
        profile_name="default_user",
        storyline_key="story-closed",
        local_date=date(2026, 4, 16),
    )
    assert state is not None
    assert state.state == "closed"
    assert state.closed is True
