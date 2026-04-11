"""APScheduler-based scheduling for briefings, updates, and alerts.

Timezone-aware, market-session-aware, weekday-only by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytz
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.logger import get_logger
from app.main import run_breaking_check, run_intraday_update, run_morning_briefing
from app.settings import Settings, get_settings

logger = get_logger("scheduler")


def build_scheduler(settings: Settings | None = None) -> BlockingScheduler:
    """Build and configure the APScheduler with all scheduled jobs."""
    settings = settings or get_settings()
    tz = pytz.timezone(settings.timezone)

    scheduler = BlockingScheduler(timezone=tz)

    # Load schedule config
    schedule_config = _load_schedule_config(settings)

    # Morning briefing
    morning = schedule_config.get("morning_briefing", {})
    morning_time = morning.get("time", "12:30")
    h, m = morning_time.split(":")
    morning_days = _days_expr(morning.get("days"))
    scheduler.add_job(
        run_morning_briefing,
        CronTrigger(
            hour=int(h),
            minute=int(m),
            day_of_week=morning_days,
            timezone=tz,
        ),
        id="morning_briefing",
        name="Morning Briefing",
        misfire_grace_time=300,
    )
    logger.info("Scheduled morning briefing at %s (weekdays)", morning_time)

    # Hourly intraday updates
    intraday = schedule_config.get("hourly_intraday", {})
    if intraday.get("interval_minutes"):
        intraday_days = _days_expr(intraday.get("days"))
        run_times = _intraday_run_times(
            intraday.get("start", "14:30"),
            intraday.get("end", "22:00"),
            int(intraday.get("interval_minutes", 60)),
        )
        for hour, minute in run_times:
            scheduler.add_job(
                run_intraday_update,
                CronTrigger(
                    hour=hour,
                    minute=minute,
                    day_of_week=intraday_days,
                    timezone=tz,
                ),
                id=f"intraday_update_{hour:02d}{minute:02d}",
                name=f"Intraday Update {hour:02d}:{minute:02d}",
                misfire_grace_time=120,
            )
        logger.info(
            "Scheduled intraday updates %s-%s (weekdays)",
            intraday.get("start"), intraday.get("end"),
        )

    # Breaking alerts (polling)
    breaking = schedule_config.get("breaking_alerts", {})
    if breaking.get("enabled", True):
        poll_mins = breaking.get("poll_interval_minutes", 5)
        breaking_days = _days_expr(breaking.get("days"))
        start_h, start_m = map(int, breaking.get("start", "07:00").split(":"))
        end_h, end_m = map(int, breaking.get("end", "23:00").split(":"))
        scheduler.add_job(
            run_breaking_check,
            CronTrigger(
                minute=f"*/{poll_mins}",
                hour=f"{start_h}-{end_h}",
                day_of_week=breaking_days,
                timezone=tz,
            ),
            id="breaking_alerts",
            name="Breaking Alert Check",
            misfire_grace_time=60,
        )
        logger.info("Scheduled breaking alert checks every %d min", poll_mins)

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
