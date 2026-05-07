from __future__ import annotations

from datetime import date, datetime, timezone

from app.briefing.session_delta import previous_session_context, split_news_since_previous
from app.db.models import SessionArchiveSnapshot, SessionSendState
from app.db.session import get_session
from app.schemas.events import NormalisedEvent


def test_previous_session_context_for_intraday(validation_isolated_db):
    sent_at = datetime(2026, 5, 7, 11, 30, tzinfo=timezone.utc)
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="us_pre_open",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:us_pre_open",
                idempotency_key="k",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=sent_at,
                updated_at=sent_at,
            )
        )
    key, label, _ = previous_session_context(
        profile_name="default_user",
        session_key="us_intraday_risk",
        local_date=date(2026, 5, 7),
        timezone_name="Europe/Madrid",
    )
    assert key == "us_pre_open"
    assert "Pre-Open" in label


def test_split_news_suppresses_repeated_titles(validation_isolated_db):
    with get_session() as db:
        db.add(
            SessionArchiveSnapshot(
                profile_name="default_user",
                local_date=date(2026, 5, 7),
                session_key="morning",
                session_title="Morning Briefing",
                generated_at_utc=datetime(2026, 5, 7, 8, 0, tzinfo=timezone.utc),
                source_type="live_scheduler",
                delivery_attempted=True,
                delivery_success=True,
                portfolio_summary_json=[{"title": "AMD Stock Jumps on Solid Earnings"}],
            )
        )
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="morning",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:morning",
                idempotency_key="k2",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=datetime(2026, 5, 7, 8, 0, tzinfo=timezone.utc),
                updated_at=datetime(2026, 5, 7, 8, 0, tzinfo=timezone.utc),
            )
        )

    events = [
        NormalisedEvent(title="AMD Stock Jumps on Solid Earnings", summary="repeat"),
        NormalisedEvent(title="New WTI Slide Extends", summary="new"),
    ]
    delta = split_news_since_previous(
        profile_name="default_user",
        session_key="europe_midday",
        local_date=date(2026, 5, 7),
        timezone_name="Europe/Madrid",
        events=events,
    )
    assert any(evt.title == "New WTI Slide Extends" for evt in delta.new_news_items)
    assert any(evt.title == "AMD Stock Jumps on Solid Earnings" for evt in delta.repeated_news_items)
    assert "WHAT CHANGED SINCE MORNING BRIEFING" in delta.what_changed_header

