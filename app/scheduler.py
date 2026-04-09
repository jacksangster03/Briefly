"""APScheduler-based scheduling for briefings, updates, and alerts.

Timezone-aware, market-session-aware, weekday-only by default.
"""

from __future__ import annotations

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
    scheduler.add_job(
        run_morning_briefing,
        CronTrigger(
            hour=int(h),
            minute=int(m),
            day_of_week="mon-fri",
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
        start_h, start_m = intraday.get("start", "14:30").split(":")
        end_h, end_m = intraday.get("end", "22:00").split(":")
        scheduler.add_job(
            run_intraday_update,
            CronTrigger(
                hour=f"{start_h}-{end_h}",
                minute=int(start_m),
                day_of_week="mon-fri",
                timezone=tz,
            ),
            id="intraday_update",
            name="Hourly Intraday Update",
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
        scheduler.add_job(
            run_breaking_check,
            IntervalTrigger(minutes=poll_mins, timezone=tz),
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
