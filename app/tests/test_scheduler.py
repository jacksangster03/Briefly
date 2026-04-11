"""Scheduler tests for configured run windows."""

from app.scheduler import _breaking_run_times, _intraday_run_times, build_scheduler
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
    assert "morning_briefing" in job_ids
    assert "intraday_update_1430" in job_ids
    assert "intraday_update_2130" in job_ids
    assert "intraday_update_2230" not in job_ids


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
