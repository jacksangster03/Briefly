"""Scheduler tests for configured run windows."""

from app.scheduler import _intraday_run_times, build_scheduler
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


def test_scheduler_builds_explicit_intraday_jobs():
    scheduler = build_scheduler(Settings())
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "morning_briefing" in job_ids
    assert "breaking_alerts" in job_ids
    assert "intraday_update_1430" in job_ids
    assert "intraday_update_2130" in job_ids
    assert "intraday_update_2230" not in job_ids
