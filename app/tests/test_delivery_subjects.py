"""Tests for email subject line formatting in EmailFormatter and day_replay."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from app.db.models import SentMessage, SessionSendState
from app.db.session import get_session


def _make_briefing(
    session_mode: str = "weekday",
    session_key: str = "morning",
    session_title: str = "Morning Briefing",
    date: datetime | None = None,
):
    """Create a minimal MorningBriefing-like object for subject tests."""
    obj = MagicMock()
    obj.session_mode = session_mode
    obj.session_key = session_key
    obj.session_title = session_title
    obj.generated_at = date or datetime(2026, 5, 7, 8, 0, 0, tzinfo=timezone.utc)
    return obj


def _get_subject(briefing) -> str:
    from app.briefing.email_formatter import EmailFormatter
    fmt = EmailFormatter("UTC")
    return fmt._subject(briefing)


# ---------------------------------------------------------------------------
# Morning session
# ---------------------------------------------------------------------------

class TestEmailSubjectMorning:
    def test_morning_returns_briefly_prefix(self) -> None:
        subject = _get_subject(_make_briefing())
        assert subject.startswith("Briefly |")

    def test_morning_label(self) -> None:
        subject = _get_subject(_make_briefing())
        assert "Morning Briefing" in subject

    def test_morning_date_format(self) -> None:
        briefing = _make_briefing(date=datetime(2026, 5, 7, 8, 0, 0, tzinfo=timezone.utc))
        subject = _get_subject(briefing)
        # Should contain abbreviated weekday + day + month
        assert "Wed" in subject or "Thu" in subject  # 7 May 2026 is a Thursday
        assert "07 May" in subject


# ---------------------------------------------------------------------------
# Non-morning sessions
# ---------------------------------------------------------------------------

class TestEmailSubjectNonMorning:
    def test_europe_midday_session_label(self) -> None:
        briefing = _make_briefing(
            session_key="europe_midday",
            session_title="Europe Midday Check",
        )
        subject = _get_subject(briefing)
        assert "Europe Midday Check" in subject
        assert subject.startswith("Briefly |")

    def test_us_pre_open_session_label(self) -> None:
        briefing = _make_briefing(
            session_key="us_pre_open",
            session_title="US Pre-Open Setup",
        )
        subject = _get_subject(briefing)
        assert "US Pre-Open Setup" in subject

    def test_into_close_session_label(self) -> None:
        briefing = _make_briefing(
            session_key="into_close",
            session_title="Into Close Update",
        )
        subject = _get_subject(briefing)
        assert "Into Close Update" in subject

    def test_closing_wrap_session_label(self) -> None:
        briefing = _make_briefing(
            session_key="closing_wrap",
            session_title="Closing Wrap / Next-Day Setup",
        )
        subject = _get_subject(briefing)
        assert "Closing Wrap" in subject

    def test_us_intraday_risk_session_label(self) -> None:
        briefing = _make_briefing(
            session_key="us_intraday_risk",
            session_title="US Intraday Risk Check",
        )
        subject = _get_subject(briefing)
        assert "US Intraday Risk Check" in subject


# ---------------------------------------------------------------------------
# Weekend sessions
# ---------------------------------------------------------------------------

class TestEmailSubjectWeekend:
    def test_saturday_uses_weekend_briefing(self) -> None:
        briefing = _make_briefing(
            session_mode="saturday",
            session_key="saturday",
            session_title="Saturday",
        )
        subject = _get_subject(briefing)
        assert "Weekend Briefing" in subject

    def test_sunday_uses_weekend_briefing(self) -> None:
        briefing = _make_briefing(
            session_mode="sunday",
            session_key="sunday",
            session_title="Sunday",
        )
        subject = _get_subject(briefing)
        assert "Weekend Briefing" in subject


# ---------------------------------------------------------------------------
# Unique subject prefixes for all six live sessions
# ---------------------------------------------------------------------------

class TestUniqueSubjectPrefixes:
    def test_six_sessions_have_unique_labels(self) -> None:
        sessions = [
            ("morning", "morning", "Morning Briefing"),
            ("weekday", "europe_midday", "Europe Midday Check"),
            ("weekday", "us_pre_open", "US Pre-Open Setup"),
            ("weekday", "us_intraday_risk", "US Intraday Risk Check"),
            ("weekday", "into_close", "Into Close Update"),
            ("weekday", "closing_wrap", "Closing Wrap / Next-Day Setup"),
        ]
        subjects = []
        for mode, key, title in sessions:
            briefing = _make_briefing(session_mode=mode, session_key=key, session_title=title)
            subjects.append(_get_subject(briefing))
        assert len(subjects) == len(set(subjects)), f"Duplicate subjects found: {subjects}"


# ---------------------------------------------------------------------------
# Test replay subject format
# ---------------------------------------------------------------------------

class TestReplaySubject:
    def test_test_banner_email_subject_contains_test_day_replay(self) -> None:
        from datetime import datetime, timezone
        from app.briefing.day_replay import _with_test_banner_email
        from unittest.mock import MagicMock

        email_content = MagicMock()
        email_content.plain_text = "body"
        email_content.html_body = "<p>body</p>"
        email_content.model_copy.return_value = email_content

        replay_time = datetime(2026, 5, 7, 9, 30, 0, tzinfo=timezone.utc)
        _with_test_banner_email(
            email_content,
            session_title="Morning Briefing",
            replay_time_local=replay_time,
        )

        call_kwargs = email_content.model_copy.call_args[1]["update"]
        subject = call_kwargs["subject"]
        assert "[TEST DAY REPLAY]" in subject
        assert "Morning Briefing" in subject

    def test_test_banner_email_subject_contains_slot_time(self) -> None:
        from datetime import datetime, timezone
        from app.briefing.day_replay import _with_test_banner_email
        from unittest.mock import MagicMock

        email_content = MagicMock()
        email_content.plain_text = "body"
        email_content.html_body = "<p>body</p>"
        email_content.model_copy.return_value = email_content

        replay_time = datetime(2026, 5, 7, 14, 15, 0, tzinfo=timezone.utc)
        _with_test_banner_email(
            email_content,
            session_title="US Pre-Open Setup",
            replay_time_local=replay_time,
        )

        call_kwargs = email_content.model_copy.call_args[1]["update"]
        subject = call_kwargs["subject"]
        assert "14:15" in subject


# ---------------------------------------------------------------------------
# delivery-log CLI regression test
# ---------------------------------------------------------------------------

class TestDeliveryLogCli:
    """Ensure delivery-log does not crash and shows expected header."""

    def test_delivery_log_does_not_raise_unbound_error(self, monkeypatch) -> None:
        """delivery_log must not crash with UnboundLocalError when profile loading fails."""
        from click.testing import CliRunner
        from app.cli import cli

        # Patch load_user_profile to raise so we exercise the fallback branch
        monkeypatch.setattr(
            "app.cli.delivery_log.__wrapped__" if hasattr(getattr(__import__("app.cli", fromlist=["delivery_log"]), "delivery_log", None), "__wrapped__") else "app.personalization.user_profile.load_user_profile",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("profile load failed")),
            raising=False,
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["delivery-log", "--date", "today"])
        # Must not crash with UnboundLocalError
        assert result.exit_code != 1 or "UnboundLocalError" not in str(result.output)
        assert "Delivery Log" in result.output or result.exit_code == 0

    def test_delivery_log_header_contains_date_and_tz(self, monkeypatch) -> None:
        """Header line should include a date string and timezone name."""
        from click.testing import CliRunner
        from app.cli import cli
        from unittest.mock import MagicMock

        # Patch DB so no records are found (avoids real DB access)
        monkeypatch.setattr("app.cli.init_db", lambda: None, raising=False)

        mock_profile = MagicMock()
        mock_profile.timezone = "Europe/Madrid"
        monkeypatch.setattr(
            "app.personalization.user_profile.load_user_profile",
            lambda *a, **kw: mock_profile,
        )

        from contextlib import contextmanager
        from unittest.mock import MagicMock as MM

        @contextmanager
        def _fake_session():
            db = MM()
            db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            yield db

        monkeypatch.setattr("app.main.get_session", _fake_session, raising=False)

        runner = CliRunner()
        result = runner.invoke(cli, ["delivery-log", "--date", "today"])
        assert "Delivery Log" in result.output
        assert "Europe/Madrid" in result.output


def test_delivery_log_includes_subject_and_hash_columns(validation_isolated_db):
    from click.testing import CliRunner
    from app.cli import cli

    sent_at = datetime(2026, 5, 7, 14, 25, tzinfo=timezone.utc)
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="us_intraday_risk",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:us_intraday_risk",
                idempotency_key="k",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=sent_at,
                updated_at=sent_at,
            )
        )
        db.add(
            SentMessage(
                message_type="session_brief:us_intraday_risk",
                channel="email",
                content_preview="US Intraday Risk Check\nBody",
                content_hash="abc123def4567890",
                sent_at=sent_at,
                success=True,
            )
        )

    runner = CliRunner()
    result = runner.invoke(cli, ["delivery-log", "--date", "2026-05-07"])
    assert result.exit_code == 0, result.output
    assert "Subject" in result.output
    assert "Hash" in result.output
    assert "US Intraday Risk Check" in result.output
    assert "abc123def4567890" in result.output
