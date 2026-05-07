from __future__ import annotations

from datetime import date, datetime

from click.testing import CliRunner

from app.cli import cli
from app.db.models import SentMessage, SessionSendState
from app.db.session import get_session
from app.personalization.user_profile import UserProfile


def _seed_sent_state() -> None:
    sent_naive_utc = datetime(2026, 5, 7, 4, 2, 0)  # 06:02 CEST
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="morning",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:morning",
                idempotency_key="k1",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=sent_naive_utc,
                updated_at=sent_naive_utc,
            )
        )
        db.add(
            SentMessage(
                message_type="session_brief:morning",
                channel="email",
                content_preview="Morning Briefing",
                content_hash="h1",
                sent_at=sent_naive_utc,
                success=True,
            )
        )


def _profile() -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        session_template="emea_global",
        market_region="EMEA",
        sub_region="Eurozone",
    )


def test_schedule_status_daily_summary_and_delivery_log_show_same_local_time(validation_isolated_db, monkeypatch) -> None:
    _seed_sent_state()
    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *a, **k: _profile())

    runner = CliRunner()
    status = runner.invoke(cli, ["schedule-status"])
    assert status.exit_code == 0, status.output
    assert "sent at 06:02" in status.output

    summary = runner.invoke(cli, ["daily-summary", "--date", "2026-05-07"])
    assert summary.exit_code == 0, summary.output
    assert "email: sent at 06:02" in summary.output

    log = runner.invoke(cli, ["delivery-log", "--date", "2026-05-07"])
    assert log.exit_code == 0, log.output
    assert "2026-05-07 06:02" in log.output
