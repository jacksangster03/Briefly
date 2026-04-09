"""Application entry point and top-level orchestration functions.

Each function here represents a complete workflow:
- run_morning_briefing: fetch, process, format, deliver
- run_intraday_update: fetch new events, dedupe against sent, deliver
- run_breaking_check: poll for high-importance events, alert if threshold met
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
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
from app.processing.dedupe import deduplicate_events
from app.processing.relevance_scoring import score_events
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


def _deliver(messengers, messages: list[str], msg_type: str, event_ids: list[str] | None = None):
    """Deliver messages via all configured channels and record to DB."""
    for messenger in messengers:
        success = messenger.send_messages(messages)
        try:
            with get_session() as session:
                import hashlib
                content = "\n".join(messages)
                record = SentMessage(
                    message_type=msg_type,
                    channel=messenger.name,
                    event_ids=event_ids or [],
                    content_preview=content[:500],
                    content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                    sent_at=datetime.now(timezone.utc),
                    success=success,
                )
                session.add(record)
        except Exception:
            logger.debug("Failed to record sent message", exc_info=True)


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
    formatter = TelegramFormatter()
    messages = formatter.format_morning_briefing(briefing)

    messengers = _get_messengers(settings, profile)
    event_ids = [e.event_id for e in briefing.top_themes]
    _deliver(messengers, messages, "morning_brief", event_ids)

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

    # Fetch new events
    events = news_svc.fetch_market_news()
    fetched = len(events)

    # Enrich sectors
    for evt in events:
        for ticker in evt.tickers:
            sectors = universe.sectors_for_ticker(ticker)
            evt.sectors.extend(s for s in sectors if s not in evt.sectors)

    # Dedupe (includes already-sent check)
    deduped = deduplicate_events(events)

    # Score
    scored = score_events(deduped, profile)

    # Filter to top items above threshold
    threshold = 0.4
    top = [e for e in scored if e.final_score >= threshold][:7]

    # Market snapshot
    snapshot = market_svc.get_quotes(universe.all_index_symbols[:4])

    now = datetime.now()
    update = IntradayUpdate(
        generated_at=now,
        hour_label=now.strftime("%H:%M"),
        market_snapshot=snapshot,
        new_events=top,
        events_fetched=fetched,
        events_after_dedup=len(deduped),
        events_sent=len(top),
    )

    formatter = TelegramFormatter()
    messages = formatter.format_intraday_update(update)

    messengers = _get_messengers(settings, profile)
    event_ids = [e.event_id for e in top]
    _deliver(messengers, messages, "intraday", event_ids)

    logger.info("Intraday update: %d fetched, %d deduped, %d sent", fetched, len(deduped), len(top))


# -- Breaking Alerts ----------------------------------------------------------

def run_breaking_check(settings: Settings | None = None) -> None:
    """Poll for high-importance events and send breaking alerts."""
    settings = settings or get_settings()
    init_db()

    profile = load_user_profile(settings)
    universe = load_sector_universe(settings)
    _, news_svc, _ = _build_services(settings)
    market_svc = MarketDataService(settings)

    # Fetch latest news
    events = news_svc.fetch_market_news()

    # Quick enrich + dedupe + score
    for evt in events:
        for ticker in evt.tickers:
            sectors = universe.sectors_for_ticker(ticker)
            evt.sectors.extend(s for s in sectors if s not in evt.sectors)

    deduped = deduplicate_events(events)
    scored = score_events(deduped, profile)

    # Breaking threshold (configurable)
    threshold = 0.80
    breaking = [e for e in scored if e.final_score >= threshold and not e.already_sent]

    if not breaking:
        logger.debug("No breaking events above threshold %.2f", threshold)
        return

    formatter = TelegramFormatter()
    messengers = _get_messengers(settings, profile)

    for evt in breaking[:3]:  # max 3 breaking alerts per check
        context_quotes = market_svc.get_quotes(universe.all_index_symbols[:4])
        alert = BreakingAlert(
            event=evt,
            market_context=context_quotes,
            reason=evt.score_explanation,
        )
        messages = formatter.format_breaking_alert(alert)
        _deliver(messengers, messages, "breaking", [evt.event_id])
        logger.info("Breaking alert sent: %s (score=%.3f)", evt.title[:60], evt.final_score)
