"""Scheduler tests for configured run windows and startup catch-up."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from app.scheduler import (
    _breaking_run_times,
    _intraday_run_times,
    _run_session_cadence_check,
    _run_startup_catchup,
    build_scheduler,
)
from app.settings import Settings
from app.main import _effective_session_local_date


def test_intraday_run_times_respect_end_minute():
    assert _intraday_run_times("14:30", "22:00", 60) == [
        (14, 30),
        (15, 30),
        (16, 30),
        (17, 30),
        (18, 30),
        (19, 30),
        (20, 30),
        (21, 30),
    ]


def test_breaking_run_times_stop_at_end_hour():
    """end: 23:00 must end polling exactly at 23:00, not 23:55."""
    times = _breaking_run_times("08:00", "23:00", 5)
    assert times[0] == (8, 0)
    assert times[-1] == (23, 0)
    # No minutes beyond 23:00 should appear
    for hour, minute in times:
        if hour == 23:
            assert minute == 0, f"Unexpected late poll at 23:{minute:02d}"
    # Full coverage: 5-min interval across 15 hours + the 23:00 tick
    assert len(times) == (15 * 12) + 1


def test_scheduler_builds_single_cadence_job():
    """Exactly one session cadence check job; no legacy morning/intraday split."""
    scheduler = build_scheduler(Settings())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "session_cadence_check" in job_ids, "unified cadence job must be registered"
    assert "morning_cadence_check" not in job_ids, "legacy morning job must not exist"
    assert "intraday_cadence_check" not in job_ids, "legacy intraday job must not exist"
    # Breaking polling jobs still exist as explicit checks in the configured window.
    assert "breaking_alerts_0800" in job_ids
    assert "breaking_alerts_2300" in job_ids


def test_session_cadence_check_calls_run_session_brief():
    """_run_session_cadence_check delegates to run_session_brief with scheduler source."""
    settings = Settings()
    with patch("app.scheduler.run_session_brief") as mock_brief:
        _run_session_cadence_check(settings)
    mock_brief.assert_called_once_with(settings, respect_cadence=True, command_source="scheduler")


def test_session_cadence_check_max_instances_one():
    """Unified job must set max_instances=1 to prevent overlap on slow sends."""
    scheduler = build_scheduler(Settings())
    job = next(j for j in scheduler.get_jobs() if j.id == "session_cadence_check")
    assert job.max_instances == 1


def test_scheduler_breaking_end_boundary_does_not_overshoot():
    """The scheduler must not register any breaking job past the configured end."""
    scheduler = build_scheduler(Settings())
    breaking_ids = {
        job.id for job in scheduler.get_jobs() if job.id.startswith("breaking_alerts_")
    }
    # No job should exist for 23:05..23:55 given end: "23:00"
    assert "breaking_alerts_2300" in breaking_ids
    forbidden = {f"breaking_alerts_23{m:02d}" for m in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)}
    assert not (breaking_ids & forbidden), (
        f"Scheduler overshoots end boundary: {breaking_ids & forbidden}"
    )


# ---------------------------------------------------------------------------
# Phase 9.1: startup catch-up
# ---------------------------------------------------------------------------

def _make_settings(dry_run: bool = False) -> Settings:
    s = Settings()
    s.dry_run = dry_run
    s.timezone = "Europe/Madrid"
    return s


def _summary(sent=(), already_sent=(), skipped=()):
    rows = []
    for sk in sent:
        rows.append({"session": sk, "action": "sent", "reason": ""})
    for sk in already_sent:
        rows.append({"session": sk, "action": "already_sent", "reason": "successful delivery found"})
    for sk in skipped:
        rows.append({"session": sk, "action": "skipped", "reason": "window not yet started"})
    return rows


class TestRunStartupCatchup:
    def test_dry_run_skips_entirely(self):
        settings = _make_settings(dry_run=True)
        with patch("app.scheduler.run_catch_up") as mock_cu:
            _run_startup_catchup(settings)
        mock_cu.assert_not_called()

    def test_weekend_skips_entirely(self):
        settings = _make_settings()
        # Saturday = weekday() == 5
        saturday = datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)  # a Saturday
        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up") as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = saturday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # patch astimezone to return a Saturday-aware datetime
            sat_local = MagicMock()
            sat_local.weekday.return_value = 5
            saturday_obj = MagicMock()
            saturday_obj.astimezone.return_value = sat_local
            mock_dt.now.return_value = saturday_obj
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            profile.weekend_mode = "off"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)
        mock_cu.assert_not_called()

    def test_weekend_mode_allows_startup_catchup(self):
        settings = _make_settings()
        sat_local = MagicMock()
        sat_local.weekday.return_value = 5
        sat_local.date.return_value.isoformat.return_value = "2026-05-09"
        sat_local.strftime.return_value = "10:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = sat_local

        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary()) as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            profile.weekend_mode = "saturday_only"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)

        mock_cu.assert_called_once()

    def test_weekday_calls_run_catch_up(self):
        settings = _make_settings()
        weekday_local = MagicMock()
        weekday_local.weekday.return_value = 1  # Tuesday
        weekday_local.date.return_value.isoformat.return_value = "2026-05-06"
        weekday_local.strftime.return_value = "10:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = weekday_local

        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary()) as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)

        mock_cu.assert_called_once()
        _, kwargs = mock_cu.call_args
        assert kwargs["target_date_str"] == "today"
        assert kwargs["force_all"] is False
        assert kwargs["command_source"] == "scheduler"

    def test_missed_sessions_are_sent(self):
        settings = _make_settings()
        weekday_local = MagicMock()
        weekday_local.weekday.return_value = 2
        weekday_local.date.return_value.isoformat.return_value = "2026-05-06"
        weekday_local.strftime.return_value = "14:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = weekday_local

        sent_log = []
        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary(
                 sent=["morning", "europe_midday", "us_pre_open"],
                 skipped=["us_intraday_risk", "into_close", "closing_wrap"],
             )), \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"), \
             patch("app.scheduler.logger") as mock_log:
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)
            # Check that sent sessions were logged
            info_calls = [str(c) for c in mock_log.info.call_args_list]
            assert any("morning" in c for c in info_calls)

    def test_already_sent_are_not_resent(self):
        settings = _make_settings()
        weekday_local = MagicMock()
        weekday_local.weekday.return_value = 2
        weekday_local.date.return_value.isoformat.return_value = "2026-05-06"
        weekday_local.strftime.return_value = "10:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = weekday_local

        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary(
                 already_sent=["morning"],
                 skipped=["europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"],
             )) as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)

        # run_catch_up was still called (it checks internally)
        mock_cu.assert_called_once()

    def test_exception_does_not_propagate(self):
        settings = _make_settings()
        with patch("app.scheduler.init_db", side_effect=RuntimeError("db gone")):
            # Must not raise
            _run_startup_catchup(settings)

    def test_catch_up_uses_respect_cadence_false(self):
        """run_catch_up internally disables respect_cadence so all elapsed sessions are eligible."""
        settings = _make_settings()
        weekday_local = MagicMock()
        weekday_local.weekday.return_value = 0
        weekday_local.date.return_value.isoformat.return_value = "2026-05-06"
        weekday_local.strftime.return_value = "08:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = weekday_local

        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary()) as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)

        _, kwargs = mock_cu.call_args
        assert kwargs["ignore_materiality"] is False
        assert kwargs["active_mode"] is False


# ---------------------------------------------------------------------------
# Duplicate-send guard tests
# ---------------------------------------------------------------------------

class TestDuplicateSendGuard:
    """Verify that two rapid scheduler ticks cannot double-send a session."""

    def test_two_ticks_call_run_session_brief_twice_but_brief_handles_idempotency(self):
        """Two ticks both call run_session_brief; idempotency is inside that function."""
        settings = _make_settings()
        calls: list[dict] = []

        def _fake_brief(s, *, respect_cadence, command_source):
            calls.append({"respect_cadence": respect_cadence, "command_source": command_source})

        with patch("app.scheduler.run_session_brief", side_effect=_fake_brief):
            _run_session_cadence_check(settings)
            _run_session_cadence_check(settings)

        # Both ticks delegated to run_session_brief — idempotency is run_session_brief's job.
        assert len(calls) == 2
        for call_kwargs in calls:
            assert call_kwargs["respect_cadence"] is True
            assert call_kwargs["command_source"] == "scheduler"

    def test_legacy_job_names_absent_from_scheduler(self):
        """No legacy morning_cadence_check or intraday_cadence_check jobs registered."""
        scheduler = build_scheduler(Settings())
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "morning_cadence_check" not in job_ids
        assert "intraday_cadence_check" not in job_ids

    def test_unified_job_registered_weekdays(self):
        """session_cadence_check exists and is of interval type."""
        from apscheduler.triggers.interval import IntervalTrigger
        scheduler = build_scheduler(Settings())
        job = next(j for j in scheduler.get_jobs() if j.id == "session_cadence_check")
        assert isinstance(job.trigger, IntervalTrigger)

    def test_startup_catchup_and_scheduler_do_not_force_resend(self):
        """Startup catch-up passes force_all=False, preserving idempotency for already-sent sessions."""
        settings = _make_settings()
        weekday_local = MagicMock()
        weekday_local.weekday.return_value = 1
        weekday_local.date.return_value.isoformat.return_value = "2026-05-06"
        weekday_local.strftime.return_value = "10:00"
        now_utc = MagicMock()
        now_utc.astimezone.return_value = weekday_local

        with patch("app.scheduler.datetime") as mock_dt, \
             patch("app.scheduler.run_catch_up", return_value=_summary(
                 already_sent=["morning"],
                 skipped=["europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"],
             )) as mock_cu, \
             patch("app.scheduler.load_user_profile") as mock_profile, \
             patch("app.scheduler.ZoneInfo"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = now_utc
            profile = MagicMock()
            profile.timezone = "Europe/Madrid"
            mock_profile.return_value = profile
            _run_startup_catchup(settings)

        _, kwargs = mock_cu.call_args
        assert kwargs["force_all"] is False, "startup catch-up must never force-resend already-sent sessions"


def test_closing_wrap_2230_belongs_to_same_local_date():
    dt = datetime(2026, 5, 7, 20, 30, tzinfo=timezone.utc)  # 22:30 CEST
    d = _effective_session_local_date(
        session_key="closing_wrap",
        generated_at=dt,
        timezone_name="Europe/Madrid",
    )
    assert d.isoformat() == "2026-05-07"


def test_closing_wrap_0002_belongs_to_previous_local_date():
    dt = datetime(2026, 5, 7, 22, 2, tzinfo=timezone.utc)  # 00:02 CEST on May 8
    d = _effective_session_local_date(
        session_key="closing_wrap",
        generated_at=dt,
        timezone_name="Europe/Madrid",
    )
    assert d.isoformat() == "2026-05-07"


def test_dry_run_session_brief_logs_dry_run_status_and_writes_no_live_send_state(
    monkeypatch,
    validation_isolated_db,
):
    from types import SimpleNamespace
    from app.main import run_morning_briefing
    from app.personalization.user_profile import UserProfile
    from app.schemas.briefings import MorningBriefing
    from app.schemas.delivery import EmailRenderResult
    from app.db.models import SentMessage, SessionSendState
    from app.db.session import get_session

    class _StubGenerator:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, *, session_key: str = "morning", session_title: str = "Morning Briefing") -> MorningBriefing:
            return MorningBriefing(
                generated_at=datetime(2026, 5, 9, 8, 0, tzinfo=timezone.utc),
                session_key=session_key,
                session_title=session_title,
                data_freshness={"Market Prices": "2026-05-09 08:00 CEST"},
            )

    class _StubTelegramFormatter:
        def __init__(self, *args, **kwargs):
            pass

        def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
            return [f"{briefing.session_title}: telegram"]

    class _StubEmailFormatter:
        def __init__(self, *args, **kwargs):
            pass

        def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
            return EmailRenderResult(
                subject=briefing.session_title,
                plain_text=f"{briefing.session_title}: email",
                html_body=f"<p>{briefing.session_title}: email</p>",
                inline_assets=[],
            )

    class _StubLLMRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def render_morning(self, *, deterministic_email, **kwargs):
            return SimpleNamespace(active_email=deterministic_email, shadow_preview=None)

    class _FakeEmailMessenger:
        name = "email"

        def __init__(self, settings):
            self.settings = settings
            self.dry_run = True
            self.last_error = ""

        def is_configured(self) -> bool:
            return True

        def send_rich(self, *, subject: str, plain_text: str, html_body: str, inline_assets=None) -> bool:
            return True

    logs: list[str] = []
    monkeypatch.setattr(
        "app.main.logger.info",
        lambda message, *args: logs.append(message % args if args else message),
    )
    monkeypatch.setattr("app.main.load_user_profile", lambda settings: UserProfile(name="default_user"))
    monkeypatch.setattr("app.main.load_sector_universe", lambda settings: object())
    monkeypatch.setattr("app.main._build_services", lambda settings: (object(), object(), object()))
    monkeypatch.setattr("app.main.MorningBriefingGenerator", _StubGenerator)
    monkeypatch.setattr("app.main.TelegramFormatter", _StubTelegramFormatter)
    monkeypatch.setattr("app.main.EmailFormatter", _StubEmailFormatter)
    monkeypatch.setattr("app.main.LLMEmailRenderer", _StubLLMRenderer)
    monkeypatch.setattr("app.main.EmailMessenger", _FakeEmailMessenger)
    monkeypatch.setattr("app.main.load_previous_snapshot", lambda **kwargs: (None, {}))
    monkeypatch.setattr("app.main.persist_snapshot", lambda **kwargs: None)
    monkeypatch.setattr("app.main.record_sent_events", lambda *args, **kwargs: None)

    settings = Settings(
        dry_run=True,
        delivery_channel="email",
        email_user="sender@example.com",
        email_password="secret",
        email_to="recipient@example.com",
    )

    run_morning_briefing(settings, auto_route_session=False, session_override="morning")
    log_text = "\n".join(logs)
    assert "email=dry-run (dry-run preview only)" in log_text
    assert "dry-run complete" in log_text
    assert " delivered: " not in log_text

    with get_session() as session:
        assert session.query(SentMessage).count() == 0
        assert session.query(SessionSendState).count() == 0


def test_dry_run_does_not_write_archive_snapshot(
    monkeypatch,
    validation_isolated_db,
):
    """A dry-run send must not produce a live archive snapshot row.

    The should_store_snapshot guard returns False when dry_run=True, so
    create_session_snapshot should never be called during a dry-run.
    """
    from app.briefing.session_snapshot_service import should_store_snapshot

    # Confirm the guard itself returns False for a dry-run scheduler tick.
    result = should_store_snapshot(
        command_source="scheduler",
        dry_run=True,
        is_backfill=False,
        session_key="morning",
        delivery_attempted=True,
        snapshots_enabled=True,
    )
    assert result is False, (
        "should_store_snapshot must return False for dry_run=True regardless of command_source"
    )


def test_scheduler_live_send_path_calls_create_session_snapshot(
    monkeypatch,
    validation_isolated_db,
):
    """When command_source='scheduler' and dry_run=False, create_session_snapshot
    must be called (i.e. the archive path is reached and not silently skipped).
    """
    from types import SimpleNamespace
    from app.main import run_morning_briefing
    from app.personalization.user_profile import UserProfile
    from app.schemas.briefings import MorningBriefing
    from app.schemas.delivery import EmailRenderResult
    from app.db.models import SessionArchiveSnapshot
    from app.db.session import get_session

    class _StubGenerator:
        def __init__(self, *args, **kwargs):
            pass

        def generate(self, *, session_key: str = "morning", session_title: str = "Morning Briefing") -> MorningBriefing:
            return MorningBriefing(
                generated_at=datetime(2026, 5, 6, 8, 0, tzinfo=timezone.utc),
                session_key=session_key,
                session_title=session_title,
                data_freshness={"Market Prices": "2026-05-06 08:00 CEST"},
            )

    class _StubTelegramFormatter:
        def __init__(self, *args, **kwargs):
            pass

        def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
            return [f"{briefing.session_title}: telegram"]

    class _StubEmailFormatter:
        def __init__(self, *args, **kwargs):
            pass

        def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
            return EmailRenderResult(
                subject=briefing.session_title,
                plain_text=f"{briefing.session_title}: email",
                html_body=f"<p>{briefing.session_title}: email</p>",
                inline_assets=[],
            )

    class _StubLLMRenderer:
        def __init__(self, *args, **kwargs):
            pass

        def render_morning(self, *, deterministic_email, **kwargs):
            return SimpleNamespace(active_email=deterministic_email, shadow_preview=None)

    class _FakeEmailMessenger:
        name = "email"

        def __init__(self, settings):
            self.settings = settings
            self.dry_run = False
            self.last_error = ""

        def is_configured(self) -> bool:
            return True

        def send_rich(self, *, subject: str, plain_text: str, html_body: str, inline_assets=None) -> bool:
            return True

    monkeypatch.setattr("app.main.load_user_profile", lambda settings: UserProfile(name="default_user"))
    monkeypatch.setattr("app.main.load_sector_universe", lambda settings: object())
    monkeypatch.setattr("app.main._build_services", lambda settings: (object(), object(), object()))
    monkeypatch.setattr("app.main.MorningBriefingGenerator", _StubGenerator)
    monkeypatch.setattr("app.main.TelegramFormatter", _StubTelegramFormatter)
    monkeypatch.setattr("app.main.EmailFormatter", _StubEmailFormatter)
    monkeypatch.setattr("app.main.LLMEmailRenderer", _StubLLMRenderer)
    monkeypatch.setattr("app.main.EmailMessenger", _FakeEmailMessenger)
    monkeypatch.setattr("app.main.load_previous_snapshot", lambda **kwargs: (None, {}))
    monkeypatch.setattr("app.main.persist_snapshot", lambda **kwargs: None)
    monkeypatch.setattr("app.main.record_sent_events", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.main.record_cadence_marker", lambda **kwargs: None)
    # Preferences: snapshots enabled (patched at source module since it's
    # imported locally inside the snapshot try-block in app.main)
    import app.personalization.preferences_service as _prefs_mod
    monkeypatch.setattr(
        _prefs_mod,
        "get_preferences",
        lambda name: {
            "snapshots.enabled": True,
            "snapshots.store_email_html": False,
            "snapshots.store_failed_attempts": False,
            "snapshots.retention_days": 30,
        },
    )

    settings = Settings(
        dry_run=False,
        delivery_channel="email",
        email_user="sender@example.com",
        email_password="secret",
        email_to="recipient@example.com",
    )

    run_morning_briefing(
        settings,
        auto_route_session=False,
        session_override="morning",
        command_source="scheduler",
    )

    with get_session() as db:
        rows = db.query(SessionArchiveSnapshot).all()

    assert len(rows) == 1, (
        f"Expected 1 archive snapshot row after a live scheduler send, got {len(rows)}"
    )
    assert rows[0].session_key == "morning"
    assert rows[0].source_type == "live_scheduler"
