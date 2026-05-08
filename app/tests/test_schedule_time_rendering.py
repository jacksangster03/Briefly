from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from click.testing import CliRunner

from app.cli import cli
from app.db.models import SentMessage, SessionSendState
from app.db.session import get_session
from app.personalization.user_profile import UserProfile


def _seed_sent_state() -> None:
    tz = ZoneInfo("Europe/Madrid")
    now_local = datetime.now(tz)
    target_date = now_local.date()
    sent_local = datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(hour=6, minute=2)
    sent_naive_utc = sent_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="morning",
                local_date=target_date,
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

    tz = ZoneInfo("Europe/Madrid")
    today_local = datetime.now(tz).date().isoformat()
    summary = runner.invoke(cli, ["daily-summary", "--date", today_local])
    assert summary.exit_code == 0, summary.output
    assert "email: sent at 06:02" in summary.output

    log = runner.invoke(cli, ["delivery-log", "--date", today_local])
    assert log.exit_code == 0, log.output
    assert "06:02" in log.output
