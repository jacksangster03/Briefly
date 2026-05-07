from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import MagicMock

from click.testing import CliRunner

from app.briefing.email_formatter import EmailFormatter
from app.briefing.session_delivery import canonical_session_message_key
from app.cli import cli
from app.main import run_daily_summary
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.db.models import SessionSendState
from app.db.session import get_session


def test_non_emea_idempotency_message_key_is_supported() -> None:
    assert canonical_session_message_key("americas_midday") == "session_brief:americas_midday"
    assert canonical_session_message_key("apac_morning") == "session_brief:apac_morning"


def test_non_emea_email_subject_uses_session_title() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 7, 12, 0),
        session_key="americas_midday",
        session_title="Midday / Europe Close Check",
    )
    subject = EmailFormatter("America/New_York")._subject(briefing)
    assert "Midday / Europe Close Check" in subject


def test_schedule_status_displays_americas_template_sessions(monkeypatch) -> None:
    profile = UserProfile(
        name="default_user",
        timezone="America/New_York",
        market_region="Americas",
        sub_region="US",
        session_template="americas_global",
    )
    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *a, **k: profile)
    monkeypatch.setattr("app.cli.init_db", lambda: None, raising=False)

    @contextmanager
    def _fake_session():
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        yield db

    monkeypatch.setattr("app.db.session.get_session", _fake_session, raising=False)
    monkeypatch.setattr("app.cli.get_session", _fake_session, raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["schedule-status"])
    assert result.exit_code == 0, result.output
    assert "Template:         americas_global" in result.output
    assert "americas_morning" in result.output
    assert "americas_midday" in result.output


def test_daily_summary_displays_apac_template_sessions(monkeypatch) -> None:
    profile = UserProfile(
        name="default_user",
        timezone="Asia/Tokyo",
        market_region="APAC",
        sub_region="Japan/Korea",
        session_template="apac_global",
    )
    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *a, **k: profile)

    @contextmanager
    def _fake_session():
        mock = MagicMock()
        mock.query.return_value.filter.return_value.all.return_value = []
        mock.query.return_value.filter.return_value.count.return_value = 0
        yield mock

    monkeypatch.setattr("app.main.get_session", _fake_session)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )

    summary = run_daily_summary(target_date_str="2026-05-07", profile_name="default_user")
    assert "APAC Morning Briefing" in summary
    assert "Europe Open Handoff" in summary


def test_delivery_log_displays_non_emea_session_key(validation_isolated_db) -> None:
    sent_at = datetime(2026, 5, 7, 14, 0, 0)
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="americas_morning",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:americas_morning",
                idempotency_key="x",
                success=True,
                in_progress=False,
                command_source="cli",
                sent_at=sent_at,
                updated_at=sent_at,
            )
        )
    runner = CliRunner()
    result = runner.invoke(cli, ["delivery-log", "--date", "2026-05-07"])
    assert result.exit_code == 0, result.output
    assert "americas_morning" in result.output
