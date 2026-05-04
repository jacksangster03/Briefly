"""APScheduler-based scheduling for briefings, updates, and alerts.

Timezone-aware, market-session-aware, weekday-only by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.logger import get_logger
from app.main import run_breaking_check, run_session_brief
from app.settings import Settings, get_settings

logger = get_logger("scheduler")


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """Build and configure the APScheduler with all scheduled jobs."""
    settings = settings or get_settings()
    tz = pytz.timezone(settings.timezone)

    scheduler = BlockingScheduler(timezone=tz)

    # Load schedule config
    schedule_config = _load_schedule_config(settings)

    # Cadence checks: polling decisions, not fixed send cadence.
    # Morning send still happens once/day via idempotency + window gating.
    morning_check_mins = int(schedule_config.get("morning_briefing", {}).get("check_interval_minutes", 5))
    scheduler.add_job(
        _run_morning_cadence_check,
        IntervalTrigger(minutes=max(1, morning_check_mins), timezone=tz),
        id="morning_cadence_check",
        name="Morning Cadence Check",
        kwargs={"settings": settings},
        misfire_grace_time=120,
    )
    logger.info("Scheduled morning cadence checks every %d minute(s)", max(1, morning_check_mins))

    # Intraday send is aligned dynamically to US 09:30 NY open converted to local.
    intraday_check_mins = int(schedule_config.get("hourly_intraday", {}).get("check_interval_minutes", 2))
    scheduler.add_job(
        _run_intraday_cadence_check,
        IntervalTrigger(minutes=max(1, intraday_check_mins), timezone=tz),
        id="intraday_cadence_check",
        name="Intraday Cadence Check",
        kwargs={"settings": settings},
        misfire_grace_time=90,
    )
    logger.info("Scheduled intraday cadence checks every %d minute(s)", max(1, intraday_check_mins))

    # Breaking alerts (polling)
    breaking = schedule_config.get("breaking_alerts", {})
    if breaking.get("enabled", True):
        poll_mins = int(breaking.get("poll_interval_minutes", 5))
        breaking_days = _days_expr(breaking.get("days"))
        start = breaking.get("start", "07:00")
        end = breaking.get("end", "23:00")
        run_times = _breaking_run_times(start, end, poll_mins)
        for hour, minute in run_times:
            scheduler.add_job(
                run_breaking_check,
                trigger="cron",
                hour=hour,
                minute=minute,
                day_of_week=breaking_days,
                timezone=tz,
                kwargs={"settings": settings},
                id=f"breaking_alerts_{hour:02d}{minute:02d}",
                name=f"Breaking Alert Check {hour:02d}:{minute:02d}",
                misfire_grace_time=60,
            )
        logger.info(
            "Scheduled %d breaking polling checks (%s-%s every %d min); sending remains event-driven",
            len(run_times), start, end, poll_mins,
        )

    return scheduler


def _load_schedule_config(settings: Settings) -> dict:
    """Load schedule configuration from YAML."""
    path = Path(settings.configs_dir) / "schedules.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def start_scheduler() -> None:
    """Build and start the scheduler (blocking)."""
    settings = get_settings()
    scheduler = build_scheduler(settings)
    logger.info("Starting scheduler (tz=%s, dry_run=%s)...", settings.timezone, settings.dry_run)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


def _days_expr(days: list[str] | None) -> str:
    """Convert YAML day list into APScheduler day-of-week expression."""
    return ",".join(days) if days else "mon-fri"


def _intraday_run_times(start: str, end: str, interval_minutes: int) -> list[tuple[int, int]]:
    """Expand an intraday schedule window into explicit HH:MM run times."""
    start_dt = datetime.strptime(start, "%H:%M")
    end_dt = datetime.strptime(end, "%H:%M")
    run_times: list[tuple[int, int]] = []
    current = start_dt
    while current <= end_dt:
        run_times.append((current.hour, current.minute))
        current += timedelta(minutes=interval_minutes)
    return run_times


def _breaking_run_times(start: str, end: str, interval_minutes: int) -> list[tuple[int, int]]:
    """Expand a breaking-alert polling window into explicit HH:MM run times.

    ``end`` is treated inclusively: a window of 08:00–23:00 with a 5-minute
    interval ends with a poll at exactly 23:00, not 23:55. The previous
    CronTrigger-based implementation used ``hour="8-23" minute="*/5"`` which
    happily fired every 5 minutes throughout the 23rd hour.
    """
    return _intraday_run_times(start, end, interval_minutes)


def _run_morning_cadence_check(settings: Settings) -> None:
    """Run session-aware cadence check across all windows."""
    run_session_brief(settings, respect_cadence=True)


def _run_intraday_cadence_check(settings: Settings) -> None:
    """Run session-aware cadence check across all windows."""
    run_session_brief(settings, respect_cadence=True)
