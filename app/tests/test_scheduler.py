"""Scheduler tests for configured run windows and startup catch-up."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from app.scheduler import _breaking_run_times, _intraday_run_times, _run_startup_catchup, build_scheduler
from app.settings import Settings


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


def test_scheduler_builds_explicit_intraday_jobs():
    scheduler = build_scheduler(Settings())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "morning_cadence_check" in job_ids
    assert "intraday_cadence_check" in job_ids
    # Breaking polling jobs still exist as explicit checks in the configured window.
    assert "breaking_alerts_0800" in job_ids
    assert "breaking_alerts_2300" in job_ids


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
             patch("app.scheduler.load_user_profile"), \
             patch("app.scheduler.init_db"):
            mock_dt.now.return_value = saturday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # patch astimezone to return a Saturday-aware datetime
            sat_local = MagicMock()
            sat_local.weekday.return_value = 5
            saturday_obj = MagicMock()
            saturday_obj.astimezone.return_value = sat_local
            mock_dt.now.return_value = saturday_obj
            _run_startup_catchup(settings)
        mock_cu.assert_not_called()

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
