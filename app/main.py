"""Application entry point and top-level orchestration functions.

Each function here represents a complete workflow:
- run_morning_briefing: fetch, process, format, deliver
- run_intraday_update: fetch new events, dedupe against sent, deliver
- run_breaking_check: poll for high-importance events, alert if threshold met
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.briefing.breaking_generator import BreakingAlertGenerator
from app.briefing.intraday_generator import IntradayGenerator
from app.briefing.llm_email_renderer import LLMEmailRenderer
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


def _resolve_llm_delivery_overrides(profile: UserProfile) -> tuple[bool | None, bool | None]:
    """Resolve profile-level LLM email overrides from delivery preferences."""
    enabled_raw = profile.delivery.get("llm_email_morning")
    shadow_raw = profile.delivery.get("llm_shadow_mode")
    enabled = enabled_raw if isinstance(enabled_raw, bool) else None
    shadow = shadow_raw if isinstance(shadow_raw, bool) else None
    return enabled, shadow


def _get_messengers(settings: Settings, profile: UserProfile, *, message_type: str = ""):
    """Return configured messengers in priority order."""
    messengers = []
    channel = settings.normalized_delivery_channel
    preferred_channels = set(profile.channels_for(message_type))
    if message_type == "breaking":
        # Breaking alerts are intentionally Telegram-only to avoid
        # duplicate push surfaces and noisy email pings.
        preferred_channels = {"telegram"}
    if not preferred_channels:
        preferred_channels = {"telegram"} if message_type == "breaking" else {"telegram", "email"}

    include_telegram = channel in {"all", "telegram"} and "telegram" in preferred_channels
    include_email = channel in {"all", "email"} and "email" in preferred_channels

    if include_telegram:
        tg = TelegramMessenger(settings)
        if tg.is_configured() or settings.dry_run:
            messengers.append(tg)

    if include_email:
        email = EmailMessenger(settings)
        # Keep default behavior unchanged (only include configured email),
        # but allow explicit email-only dry-run testing without credentials.
        if email.is_configured() or (settings.dry_run and channel == "email"):
            messengers.append(email)

    if not messengers:
        logger.warning(
            "No delivery channels active for this run (delivery_channel=%s, dry_run=%s)",
            channel,
            settings.dry_run,
        )
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


def _print_email_output(subject: str, plain_text: str, inline_assets, msg_type: str) -> None:
    """Print a concise preview of rich email output for local verification."""
    banner = "=" * 60
    print(f"\n{banner}")
    print(f"[EMAIL OUTPUT] {msg_type}")
    print(banner)
    print(subject)
    if inline_assets:
        print(f"Inline charts: {', '.join(asset.title for asset in inline_assets)}")
    print(plain_text[:2400])
    print(f"{banner}\n")


def _record_delivery(
    *,
    channel: str,
    msg_type: str,
    success: bool,
    content_preview: str,
    events: list | None = None,
    tracking_ids: list[str] | None = None,
) -> None:
    """Persist a delivery attempt to SentMessage."""
    with get_session() as session:
        import hashlib

        event_ids: list[str] = []
        for evt in events or []:
            preferred_ids: list[str] = []
            if getattr(evt, "cluster_id", None):
                preferred_ids.append(evt.cluster_id)
            if getattr(evt, "content_hash", None):
                preferred_ids.append(evt.content_hash)
            if getattr(evt, "event_id", None):
                preferred_ids.append(evt.event_id)
            storyline_key = getattr(evt, "raw_data", {}).get("storyline_key")
            if isinstance(storyline_key, str) and storyline_key:
                preferred_ids.append(f"storyline:{storyline_key}")
            for value in preferred_ids:
                if value and value not in event_ids:
                    event_ids.append(value)
        for value in tracking_ids or []:
            if isinstance(value, str) and value and value not in event_ids:
                event_ids.append(value)

        record = SentMessage(
            message_type=msg_type,
            channel=channel,
            event_ids=event_ids,
            content_preview=content_preview[:500],
            content_hash=hashlib.sha256(content_preview.encode()).hexdigest()[:16],
            sent_at=datetime.now(timezone.utc),
            success=success,
        )
        session.add(record)


def _load_recent_sent_tracking_ids(lookback_hours: int = 24) -> set[str]:
    """Return recently delivered event tracking IDs from SentMessage rows."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    tracking_ids: set[str] = set()
    with get_session() as session:
        rows = (
            session.query(SentMessage)
            .filter(SentMessage.sent_at >= cutoff, SentMessage.success.is_(True))
            .all()
        )
    for row in rows:
        for value in row.event_ids or []:
            if isinstance(value, str) and value:
                tracking_ids.add(value)
    return tracking_ids


def _extract_breaking_title_from_preview(preview: str) -> str:
    """Best-effort extraction of a breaking headline from stored preview text."""
    if not preview:
        return ""
    text = _HTML_TAG_RE.sub("", preview)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].upper() == "BREAKING" and len(lines) > 1:
        return lines[1]
    for line in lines:
        if line.upper() != "BREAKING":
            return line
    return ""


def _title_token_set(title: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    return {
        token
        for token in cleaned.split()
        if token and token not in {"the", "a", "an", "and", "or", "to", "of", "in", "for", "on", "with"}
    }


def _title_similarity(left: str, right: str) -> float:
    left_tokens = _title_token_set(left)
    right_tokens = _title_token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _load_recent_breaking_titles(lookback_hours: int = 6) -> list[str]:
    """Load recently-sent breaking titles for short-term repeat suppression."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    with get_session() as session:
        rows = (
            session.query(SentMessage)
            .filter(
                SentMessage.message_type == "breaking",
                SentMessage.sent_at >= cutoff,
                SentMessage.success.is_(True),
            )
            .order_by(SentMessage.sent_at.desc())
            .all()
        )

    titles: list[str] = []
    for row in rows:
        title = _extract_breaking_title_from_preview(row.content_preview or "")
        if title:
            titles.append(title)
    return titles


def _is_repetitive_breaking_title(
    candidate_title: str,
    recent_titles: list[str],
    *,
    similarity_threshold: float = 0.55,
) -> bool:
    return any(_title_similarity(candidate_title, previous) >= similarity_threshold for previous in recent_titles)


@contextmanager
def _breaking_run_lock(settings: Settings):
    """Prevent concurrent breaking checks across multiple local processes."""
    if fcntl is None:
        yield True
        return
    lock_path = Path(settings.data_dir) / "state" / "breaking_run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.warning("Another breaking-check process is already running; skipping this cycle.")
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _deliver(
    messengers,
    messages: list[str],
    msg_type: str,
    events: list | None = None,
    settings: Settings | None = None,
    tracking_ids: list[str] | None = None,
):
    """Deliver messages via all configured channels and record to DB."""
    if settings and settings.show_output:
        _print_terminal_output(messages, msg_type)
    for messenger in messengers:
        success = messenger.send_messages(messages)
        is_dry_run = bool(getattr(messenger, "dry_run", False))
        try:
            if not is_dry_run:
                _record_delivery(
                    channel=messenger.name,
                    msg_type=msg_type,
                    success=success,
                    content_preview="\n".join(messages),
                    events=events,
                    tracking_ids=tracking_ids,
                )
        except Exception:
            logger.debug("Failed to record sent message", exc_info=True)
        if success and not is_dry_run:
            try:
                record_sent_events(events or [])
            except Exception:
                logger.debug("Failed to record sent events", exc_info=True)


def _deliver_rich_email(
    messenger: EmailMessenger,
    email_content,
    msg_type: str,
    events: list | None = None,
    settings: Settings | None = None,
) -> None:
    """Deliver a rich HTML email with inline assets and record the result."""
    if settings and settings.show_output:
        _print_email_output(
            email_content.subject,
            email_content.plain_text,
            email_content.inline_assets,
            msg_type,
        )

    success = messenger.send_rich(
        subject=email_content.subject,
        plain_text=email_content.plain_text,
        html_body=email_content.html_body,
        inline_assets=email_content.inline_assets,
    )
    is_dry_run = bool(getattr(messenger, "dry_run", False))
    try:
        if not is_dry_run:
            _record_delivery(
                channel=messenger.name,
                msg_type=msg_type,
                success=success,
                content_preview=email_content.plain_text,
                events=events,
            )
    except Exception:
        logger.debug("Failed to record rich email message", exc_info=True)
    if success and not is_dry_run:
        try:
            record_sent_events(events or [])
        except Exception:
            logger.debug("Failed to record sent events for rich email", exc_info=True)


def _send_telegram_chart_preview(
    messenger: TelegramMessenger,
    chart_assets,
    settings: Settings,
) -> None:
    """Optionally send the first morning chart to Telegram before text content."""
    if not getattr(settings, "telegram_send_charts", False) or not chart_assets:
        return
    hero = chart_assets[0]
    if not messenger.send_photo(hero, caption=hero.title):
        logger.debug("Telegram chart preview failed for %s", hero.title)


def _apply_morning_section_preferences(briefing, profile: UserProfile) -> None:
    """Apply per-profile morning section visibility overrides."""
    if not profile.morning_section_enabled("market_setup"):
        briefing.market_setup.index_quotes = []
        briefing.market_setup.macro_quotes = []
        briefing.market_setup.treasury_10y = None
        briefing.market_setup.treasury_2y = None
        briefing.market_setup.vix = None
    if not profile.morning_section_enabled("macro_context"):
        briefing.macro_context = []
    if not profile.morning_section_enabled("global_news"):
        briefing.global_news = []
    if not profile.morning_section_enabled("top_themes"):
        briefing.top_themes = []
    if not profile.morning_section_enabled("portfolio_focus"):
        briefing.portfolio_focus = []
        briefing.portfolio_quotes = []
    if not profile.morning_section_enabled("sector_scan"):
        briefing.sector_scan = []
    if not profile.morning_section_enabled("watchlist"):
        briefing.watchlist_events = []
        briefing.watchlist_quotes = []


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
    _apply_morning_section_preferences(briefing, profile)
    formatter = TelegramFormatter(profile.timezone)
    messages = formatter.format_morning_briefing(briefing)
    email_content = EmailFormatter(profile.timezone).format_morning_briefing(briefing)

    messengers = _get_messengers(settings, profile, message_type="morning")
    display_events = []
    seen_tracking_ids = set()
    for evt in briefing.global_news + briefing.top_themes + briefing.watchlist_events + briefing.portfolio_focus + [
        sector_evt
        for sector in briefing.sector_scan
        for sector_evt in sector.top_events
    ]:
        tracking_id = evt.cluster_id or evt.content_hash or evt.event_id
        if tracking_id in seen_tracking_ids:
            continue
        seen_tracking_ids.add(tracking_id)
        display_events.append(evt)

    llm_enabled_override, llm_shadow_override = _resolve_llm_delivery_overrides(profile)
    llm_decision = LLMEmailRenderer(settings).render_morning(
        briefing=briefing,
        deterministic_email=email_content,
        selected_events=display_events,
        enabled_override=llm_enabled_override,
        shadow_mode_override=llm_shadow_override,
    )
    active_email_content = llm_decision.active_email
    if settings.show_output and llm_decision.shadow_preview:
        _print_email_output(
            llm_decision.shadow_preview.subject,
            llm_decision.shadow_preview.plain_text,
            llm_decision.shadow_preview.inline_assets,
            "morning_email_llm_shadow",
        )

    telegram_messengers = [m for m in messengers if isinstance(m, TelegramMessenger)]
    email_messengers = [m for m in messengers if isinstance(m, EmailMessenger)]

    for messenger in telegram_messengers:
        _send_telegram_chart_preview(messenger, briefing.chart_assets, settings)
    if telegram_messengers:
        _deliver(telegram_messengers, messages, "morning_brief", display_events, settings=settings)
    if (
        settings.show_output
        and settings.normalized_delivery_channel != "telegram"
        and not email_messengers
        and (briefing.chart_assets or settings.enable_llm_email_render)
    ):
        _print_email_output(
            active_email_content.subject,
            active_email_content.plain_text,
            active_email_content.inline_assets,
            "morning_email_preview",
        )
    for messenger in email_messengers:
        _deliver_rich_email(messenger, active_email_content, "morning_brief", display_events, settings=settings)

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

    messengers = _get_messengers(settings, profile, message_type="intraday")
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

    with _breaking_run_lock(settings) as acquired:
        if not acquired:
            return

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
        # Keep breaking delivery concise: one highest-priority alert per cycle.
        alerts = generator.check(max_alerts=1)

        if not alerts:
            logger.debug("No breaking events above configured thresholds")
            return

        recently_sent_ids = _load_recent_sent_tracking_ids(lookback_hours=24)
        fresh_alerts: list[BreakingAlert] = []
        for alert in alerts:
            evt = alert.event
            tracking_ids = {evt.cluster_id, evt.content_hash, evt.event_id}
            if any(value in recently_sent_ids for value in tracking_ids if value):
                logger.info("Skipping duplicate breaking alert already sent recently: %s", evt.title[:80])
                continue
            fresh_alerts.append(alert)

        if not fresh_alerts:
            logger.debug("No fresh breaking alerts after recent-delivery suppression")
            return

        tier_filtered_alerts: list[BreakingAlert] = []
        for alert in fresh_alerts:
            if alert.classification.tier != "breaking":
                logger.info(
                    "Skipping non-breaking tier candidate: %s (tier=%s)",
                    alert.event.title[:90],
                    alert.classification.tier,
                )
                continue
            tier_filtered_alerts.append(alert)

        if not tier_filtered_alerts:
            logger.debug("No breaking-tier candidates after classification")
            return

        recent_breaking_titles = _load_recent_breaking_titles(lookback_hours=6)
        filtered_alerts: list[BreakingAlert] = []
        for alert in tier_filtered_alerts:
            repetitive = _is_repetitive_breaking_title(alert.event.title, recent_breaking_titles)
            # Suppress near-duplicate thread churn unless significance is
            # clearly above normal breaking noise.
            if repetitive and alert.event.final_score < 0.90 and alert.event.cluster_size < 8:
                logger.info(
                    "Suppressing repetitive breaking headline in cooldown window: %s",
                    alert.event.title[:90],
                )
                continue
            filtered_alerts.append(alert)

        if not filtered_alerts:
            logger.debug("No fresh breaking alerts after repetitive-headline cooldown")
            return

        storyline_filtered_alerts: list[BreakingAlert] = []
        for alert in filtered_alerts:
            storyline_id = f"storyline:{alert.classification.storyline_key}" if alert.classification.storyline_key else ""
            if not storyline_id:
                storyline_filtered_alerts.append(alert)
                continue
            is_recent_storyline = storyline_id in recently_sent_ids
            if is_recent_storyline and alert.classification.impact_score < 5 and alert.event.final_score < 0.93:
                logger.info(
                    "Suppressing repeat storyline within cooldown: %s (storyline=%s)",
                    alert.event.title[:90],
                    alert.classification.storyline_key,
                )
                continue
            storyline_filtered_alerts.append(alert)

        if not storyline_filtered_alerts:
            logger.debug("No fresh breaking alerts after storyline cooldown")
            return

        formatter = TelegramFormatter(profile.timezone)
        messengers = _get_messengers(settings, profile, message_type="breaking")

        for alert in storyline_filtered_alerts:
            messages = formatter.format_breaking_alert(alert)
            _deliver(
                messengers,
                messages,
                "breaking",
                [alert.event],
                settings=settings,
                tracking_ids=alert.tracking_ids,
            )
            logger.info(
                "Breaking alert sent: %s (score=%.3f)",
                alert.event.title[:60],
                alert.event.final_score,
            )


if __name__ == "__main__":
    from app.scheduler import start_scheduler

    start_scheduler()
