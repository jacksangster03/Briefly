"""Application entry point and top-level orchestration functions.

Each function here represents a complete workflow:
- run_morning_briefing: fetch, process, format, deliver
- run_intraday_update: fetch new events, dedupe against sent, deliver
- run_breaking_check: poll for high-importance events, alert if threshold met
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.breaking_generator import BreakingAlertGenerator
from app.briefing.intraday_generator import IntradayGenerator
from app.briefing.morning_generator import MorningBriefingGenerator
from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.db.models import SentMessage
from app.db.session import get_session, init_db
from app.logger import get_logger
from app.messaging.email import EmailMessenger
from app.messaging.telegram import TelegramMessenger
from app.personalization.user_profile import UserProfile, load_user_profile
from app.processing.event_store import record_sent_events
from app.schemas.briefings import BreakingAlert, IntradayUpdate
from app.settings import Settings, get_settings
from app.universe.sector_universe import SectorUniverse, load_sector_universe

logger = get_logger("main")


def _build_services(settings: Settings):
    """Instantiate all services needed for briefing generation."""
    return (
        MarketDataService(settings),
        NewsDataService(settings),
        MacroDataService(settings),
    )


def _get_messengers(settings: Settings, profile: UserProfile):
    """Return configured messengers in priority order."""
    messengers = []
    tg = TelegramMessenger(settings)
    if tg.is_configured() or settings.dry_run:
        messengers.append(tg)
    email = EmailMessenger(settings)
    if email.is_configured():
        messengers.append(email)
    return messengers


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _print_terminal_output(messages: list[str], msg_type: str) -> None:
    """Print rendered Telegram payloads to stdout for manual inspection.

    Strips HTML tags so the terminal view matches what a reader would see,
    and labels each chunk so multi-message briefings stay readable.
    """
    banner = "=" * 60
    print(f"\n{banner}")
    print(f"[OUTPUT] {msg_type} ({len(messages)} message{'s' if len(messages) != 1 else ''})")
    print(banner)
    for idx, msg in enumerate(messages, 1):
        if len(messages) > 1:
            print(f"\n--- part {idx}/{len(messages)} ---")
        print(_HTML_TAG_RE.sub("", msg))
    print(f"{banner}\n")


def _deliver(
    messengers,
    messages: list[str],
    msg_type: str,
    events: list | None = None,
    settings: Settings | None = None,
):
    """Deliver messages via all configured channels and record to DB."""
    if settings and settings.show_output:
        _print_terminal_output(messages, msg_type)
    for messenger in messengers:
        success = messenger.send_messages(messages)
        is_dry_run = bool(getattr(messenger, "dry_run", False))
        try:
            if not is_dry_run:
                with get_session() as session:
                    import hashlib
                    content = "\n".join(messages)
                    record = SentMessage(
                        message_type=msg_type,
                        channel=messenger.name,
                        event_ids=[
                            evt.cluster_id or evt.content_hash or evt.event_id
                            for evt in (events or [])
                        ],
                        content_preview=content[:500],
                        content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                        sent_at=datetime.now(timezone.utc),
                        success=success,
                    )
                    session.add(record)
        except Exception:
            logger.debug("Failed to record sent message", exc_info=True)
        if success and not is_dry_run:
            try:
                record_sent_events(events or [])
            except Exception:
                logger.debug("Failed to record sent events", exc_info=True)


# -- Morning Briefing ---------------------------------------------------------

def run_morning_briefing(settings: Settings | None = None) -> None:
    """Generate and deliver the morning briefing."""
    settings = settings or get_settings()
    init_db()

    logger.info("Starting morning briefing pipeline...")
    profile = load_user_profile(settings)
    universe = load_sector_universe(settings)
    market_svc, news_svc, macro_svc = _build_services(settings)

    generator = MorningBriefingGenerator(
        settings=settings,
        profile=profile,
        universe=universe,
        market_data=market_svc,
        news_data=news_svc,
        macro_data=macro_svc,
    )

    briefing = generator.generate()
    formatter = TelegramFormatter(profile.timezone)
    messages = formatter.format_morning_briefing(briefing)

    messengers = _get_messengers(settings, profile)
    display_events = []
    seen_tracking_ids = set()
    for evt in briefing.top_themes + briefing.watchlist_events + briefing.portfolio_focus + [
        sector_evt
        for sector in briefing.sector_scan
        for sector_evt in sector.top_events
    ]:
        tracking_id = evt.cluster_id or evt.content_hash or evt.event_id
        if tracking_id in seen_tracking_ids:
            continue
        seen_tracking_ids.add(tracking_id)
        display_events.append(evt)
    _deliver(messengers, messages, "morning_brief", display_events, settings=settings)

    logger.info(
        "Morning briefing delivered: %d messages, %d events",
        len(messages), briefing.events_sent,
    )


# -- Intraday Update ----------------------------------------------------------

def run_intraday_update(settings: Settings | None = None) -> None:
    """Fetch new events, dedupe against recent history, send top items."""
    settings = settings or get_settings()
    init_db()

    logger.info("Starting intraday update...")
    profile = load_user_profile(settings)
    universe = load_sector_universe(settings)
    market_svc, news_svc, _ = _build_services(settings)
    generator = IntradayGenerator(
        settings=settings,
        profile=profile,
        universe=universe,
        market_data=market_svc,
        news_data=news_svc,
    )
    update = generator.generate()

    formatter = TelegramFormatter(profile.timezone)
    messages = formatter.format_intraday_update(update)

    messengers = _get_messengers(settings, profile)
    _deliver(messengers, messages, "intraday", update.new_events, settings=settings)

    logger.info(
        "Intraday update: %d fetched, %d deduped, %d sent",
        update.events_fetched,
        update.events_after_dedup,
        update.events_sent,
    )


# -- Breaking Alerts ----------------------------------------------------------

def run_breaking_check(settings: Settings | None = None) -> None:
    """Poll for high-importance events and send breaking alerts."""
    settings = settings or get_settings()
    init_db()

    profile = load_user_profile(settings)
    universe = load_sector_universe(settings)
    market_svc, news_svc, _ = _build_services(settings)
    generator = BreakingAlertGenerator(
        settings=settings,
        profile=profile,
        universe=universe,
        market_data=market_svc,
        news_data=news_svc,
    )
    alerts = generator.check()

    if not alerts:
        logger.debug("No breaking events above configured thresholds")
        return

    formatter = TelegramFormatter(profile.timezone)
    messengers = _get_messengers(settings, profile)

    for alert in alerts:
        messages = formatter.format_breaking_alert(alert)
        _deliver(messengers, messages, "breaking", [alert.event], settings=settings)
        logger.info(
            "Breaking alert sent: %s (score=%.3f)",
            alert.event.title[:60],
            alert.event.final_score,
        )


if __name__ == "__main__":
    from app.scheduler import start_scheduler

    start_scheduler()
