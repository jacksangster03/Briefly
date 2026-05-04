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
from app.briefing.llm_email_renderer import LLMEmailRenderer
from app.briefing.morning_generator import MorningBriefingGenerator
from app.briefing.session_materiality import compute_materiality
from app.briefing.session_routing import next_session_window, resolve_session_window, session_window_for_key
from app.briefing.session_snapshot import load_previous_snapshot, persist_snapshot, snapshot_metrics
from app.cadence.engine import DecisionEngine
from app.cadence.state_store import (
    breaking_sends_last_hour,
    close_breaking_story,
    due_breaking_followups,
    has_cadence_marker,
    record_cadence_marker,
    recent_storyline_send_exists,
    upsert_breaking_story_initial,
    mark_followup_sent,
)
from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.db.models import BreakingStoryState
from app.db.models import SentMessage
from app.db.session import get_session, init_db
from app.logger import get_logger
from app.healthcare.section_builder import filter_breaking_healthcare_events
from app.messaging.email import EmailMessenger
from app.messaging.telegram import TelegramMessenger
from app.personalization.user_profile import UserProfile, load_user_profile
from app.processing.event_store import record_sent_events
from app.schemas.briefings import BreakingAlert, BreakingClassification
from app.schemas.events import NormalisedEvent
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


def _allowed_sessions_for_mode(mode: str) -> set[str]:
    normalised = (mode or "default").strip().lower()
    if normalised == "quiet":
        return {"morning"}
    if normalised == "active":
        return {"morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"}
    return {"morning", "us_pre_open"}


def _session_marker_key(session_key: str, local_now: datetime) -> str:
    return f"session:{session_key}:{local_now.date().isoformat()}"


def _get_messengers(settings: Settings, profile: UserProfile, *, message_type: str = ""):
    """Return configured messengers in priority order."""
    plan = _delivery_channel_plan(settings=settings, profile=profile, message_type=message_type)
    messengers = [item["messenger"] for item in plan if item.get("attempted") and item.get("messenger")]
    if not messengers:
        logger.warning(
            "No delivery channels active for this run (delivery_channel=%s, dry_run=%s)",
            settings.normalized_delivery_channel,
            settings.dry_run,
        )
    return messengers


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _delivery_channel_plan(*, settings: Settings, profile: UserProfile, message_type: str) -> list[dict[str, object]]:
    """Resolve channel attempt/skip decisions with explicit reasons."""
    channel_override = settings.normalized_delivery_channel
    preferred_channels = set(profile.channels_for(message_type))
    if message_type == "breaking":
        preferred_channels = {"telegram"}
    if not preferred_channels:
        preferred_channels = {"telegram"} if message_type == "breaking" else {"telegram", "email"}

    plan: list[dict[str, object]] = []
    for channel_name in ("telegram", "email"):
        if channel_name not in preferred_channels:
            plan.append(
                {
                    "channel": channel_name,
                    "attempted": False,
                    "status": "skipped",
                    "reason": f"profile delivery for {message_type or 'default'} excludes {channel_name}",
                    "messenger": None,
                }
            )
            continue
        if channel_override not in {"all", channel_name}:
            plan.append(
                {
                    "channel": channel_name,
                    "attempted": False,
                    "status": "skipped",
                    "reason": f"delivery_channel={channel_override} excludes {channel_name}",
                    "messenger": None,
                }
            )
            continue

        messenger = TelegramMessenger(settings) if channel_name == "telegram" else EmailMessenger(settings)
        is_configured = messenger.is_configured()
        allow_dry_run_email_probe = bool(settings.dry_run and channel_override == "email" and channel_name == "email")
        if is_configured or (channel_name == "telegram" and settings.dry_run) or allow_dry_run_email_probe:
            plan.append(
                {
                    "channel": channel_name,
                    "attempted": True,
                    "status": "attempted",
                    "reason": "delivery allowed",
                    "messenger": messenger,
                }
            )
            continue

        reason = "not configured"
        if channel_name == "email":
            reason += " (set EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO)"
        plan.append(
            {
                "channel": channel_name,
                "attempted": False,
                "status": "skipped",
                "reason": reason,
                "messenger": None,
            }
        )
    return plan


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
    error_message: str | None = None,
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
            error_message=(error_message or "")[:500] or None,
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
    any_success = False
    if settings and settings.show_output:
        _print_terminal_output(messages, msg_type)
    for messenger in messengers:
        success = messenger.send_messages(messages)
        any_success = any_success or bool(success)
        is_dry_run = bool(getattr(messenger, "dry_run", False))
        try:
            if not is_dry_run:
                _record_delivery(
                    channel=messenger.name,
                    msg_type=msg_type,
                    success=success,
                    content_preview="\n".join(messages),
                    error_message=None if success else str(getattr(messenger, "last_error", "") or "delivery returned unsuccessful status"),
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
    return any_success


def _deliver_rich_email(
    messenger: EmailMessenger,
    email_content,
    msg_type: str,
    events: list | None = None,
    settings: Settings | None = None,
) -> bool:
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
                error_message=None if success else str(getattr(messenger, "last_error", "") or "email send returned unsuccessful status"),
                events=events,
            )
    except Exception:
        logger.debug("Failed to record rich email message", exc_info=True)
    if success and not is_dry_run:
        try:
            record_sent_events(events or [])
        except Exception:
            logger.debug("Failed to record sent events for rich email", exc_info=True)
    return bool(success)


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


def _build_followup_alert_from_state(
    state: BreakingStoryState,
    *,
    context_quotes,
    reason: str,
) -> BreakingAlert:
    synthetic_event = NormalisedEvent(
        event_id=state.event_id or "",
        source="followup_state",
        source_type="news",
        title=state.event_title,
        summary="Follow-up check after initial breaking alert.",
        published_at=state.first_sent_at,
        event_type="followup",
        final_score=0.9,
        factual_confidence_score=0.8,
        cluster_size=1,
        raw_data={"storyline_key": state.storyline_key},
    )
    classification = BreakingClassification(
        tier="breaking",
        category=state.category or "macro",
        impact_score=4,
        confidence_score=3,
        novelty_score=3,
        immediacy_score=3,
        breadth_score=3,
        why_markets_care=reason,
        watch_assets=[],
        watch_symbols=[str(symbol) for symbol in (state.watch_symbols or [])],
        confirm_signals=[],
        invalidate_signals=[],
        storyline_key=state.storyline_key,
    )
    return BreakingAlert(
        event=synthetic_event,
        market_context=context_quotes or [],
        reason=reason,
        classification=classification,
        tracking_ids=[
            f"storyline:{state.storyline_key}",
            f"breaking:{state.storyline_key}:followup",
        ],
    )


def _evaluate_followup_confirmation(
    state: BreakingStoryState,
    *,
    market_svc: MarketDataService,
    fallback_symbols: list[str],
    min_asset_move_pct: float,
) -> tuple[bool, str, list]:
    symbols = [str(symbol) for symbol in (state.watch_symbols or []) if str(symbol).strip()]
    if not symbols:
        symbols = fallback_symbols
    # Keep quote fanout small and predictable.
    symbols = symbols[:6]

    quotes = market_svc.get_quotes(symbols)
    if not quotes:
        return False, "No quote data available for follow-up confirmation.", []

    movers = [
        quote
        for quote in quotes
        if abs(float(getattr(quote, "change_percent", 0.0) or 0.0)) >= min_asset_move_pct
    ]
    if movers:
        label = movers[0].display_name or movers[0].symbol
        move = float(movers[0].change_percent or 0.0)
        return True, f"Follow-up: mapped assets confirmed with {label} {move:+.2f}% move.", quotes
    return False, "Follow-up closed: mapped assets did not confirm meaningful move.", quotes


# -- Morning Briefing ---------------------------------------------------------

def run_morning_briefing(
    settings: Settings | None = None,
    *,
    respect_cadence: bool = False,
    force_morning: bool = False,
    auto_route_session: bool = True,
    session_override: str | None = None,
) -> None:
    """Generate and deliver the morning briefing."""
    settings = settings or get_settings()
    init_db()

    logger.info("Starting session briefing pipeline...")
    profile = load_user_profile(settings)
    now_utc = datetime.now(timezone.utc)
    session_window = resolve_session_window(now=now_utc, timezone_name=profile.timezone or settings.timezone)
    session_key = "morning"
    session_title = "Morning Briefing"
    if session_override:
        explicit = session_window_for_key(session_override)
        session_key = explicit.key
        session_title = explicit.title
    elif auto_route_session and not force_morning:
        session_key = session_window.key
        session_title = session_window.title
        if session_key != "morning":
            logger.info(
                "Outside morning window. Rendering %s; use --force-morning to override.",
                session_title,
            )
    decision_engine = DecisionEngine(
        settings,
        profile_name=profile.name,
        local_timezone=profile.timezone or settings.timezone,
    )
    local_now = decision_engine.cadence.now_local()
    session_marker = _session_marker_key(session_key, local_now)
    if respect_cadence:
        allowed = _allowed_sessions_for_mode(profile.session_mode)
        always_send = set(profile.always_send_sessions)
        if session_key not in allowed and session_key not in always_send:
            logger.info(
                "Cadence decision (session): suppress | session=%s mode=%s reason=not_in_mode_schedule",
                session_key,
                profile.session_mode,
            )
            return
        if has_cadence_marker(profile.name, session_marker):
            logger.info(
                "Cadence decision (session): suppress | session=%s reason=already_sent_today",
                session_key,
            )
            return

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

    briefing = generator.generate(session_key=session_key, session_title=session_title)
    _apply_morning_section_preferences(briefing, profile)
    previous_ts, previous_snapshot = load_previous_snapshot(
        profile_name=profile.name,
        session_key=briefing.session_key,
        before=briefing.generated_at,
    )

    if respect_cadence:
        always_send = set(profile.always_send_sessions)
        materiality = compute_materiality(briefing, previous=previous_snapshot)
        logger.info(
            "Session materiality | session=%s score=%d decision=%s reasons=%s",
            briefing.session_key,
            materiality.score,
            materiality.decision,
            "; ".join(materiality.reasons),
        )
        if (
            profile.suppress_low_materiality
            and briefing.session_key != "morning"
            and briefing.session_key not in always_send
        ):
            if materiality.decision in {"suppress", "hold_for_next_session"}:
                next_window = next_session_window(
                    now=briefing.generated_at,
                    timezone_name=profile.timezone or settings.timezone,
                )
                logger.info(
                    "Cadence decision (session): %s | session=%s next_eligible_session=%s",
                    materiality.decision,
                    briefing.session_key,
                    next_window.key,
                )
                return
            if materiality.decision == "breaking_alert":
                logger.info(
                    "Cadence decision (session): escalate_to_breaking | session=%s score=%d",
                    briefing.session_key,
                    materiality.score,
                )
                run_breaking_check(settings)
                return

    formatter = TelegramFormatter(profile.timezone)
    messages = formatter.format_morning_briefing(briefing)
    email_content = EmailFormatter(profile.timezone).format_morning_briefing(briefing)

    message_type = "morning" if session_key == "morning" else "intraday"
    delivery_plan = _delivery_channel_plan(settings=settings, profile=profile, message_type=message_type)
    messengers = [item["messenger"] for item in delivery_plan if item.get("attempted") and item.get("messenger")]
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
    channel_status: dict[str, str] = {}
    channel_reason: dict[str, str] = {}
    for item in delivery_plan:
        channel = str(item.get("channel"))
        if item.get("attempted"):
            channel_status[channel] = "attempted"
            channel_reason[channel] = str(item.get("reason") or "")
        else:
            channel_status[channel] = "skipped"
            channel_reason[channel] = str(item.get("reason") or "")

    delivered_ok = False
    for messenger in telegram_messengers:
        _send_telegram_chart_preview(messenger, briefing.chart_assets, settings)
    delivery_msg_type = f"session_brief:{briefing.session_key or session_key}"
    if telegram_messengers:
        for messenger in telegram_messengers:
            telegram_ok = _deliver(
                [messenger],
                messages,
                delivery_msg_type,
                display_events,
                settings=settings,
            )
            delivered_ok = telegram_ok or delivered_ok
            channel_status["telegram"] = "sent" if telegram_ok else "failed"
            channel_reason["telegram"] = (
                "delivered"
                if telegram_ok
                else str(getattr(messenger, "last_error", "") or "telegram transport failed")
            )
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
            f"session_email_preview:{briefing.session_key or session_key}",
        )
    for messenger in email_messengers:
        email_ok = _deliver_rich_email(
            messenger,
            active_email_content,
            delivery_msg_type,
            display_events,
            settings=settings,
        )
        delivered_ok = email_ok or delivered_ok
        channel_status["email"] = "sent" if email_ok else "failed"
        channel_reason["email"] = (
            "delivered"
            if email_ok
            else str(getattr(messenger, "last_error", "") or "email transport failed")
        )
        if not email_ok:
            logger.warning(
                "Email delivery failed. Next checks: run `python -m app.cli preflight` and "
                "`python -m app.cli --show-output --email-only morning`."
            )

    logger.info(
        "%s delivery status | telegram=%s (%s) | email=%s (%s)",
        briefing.session_title or "Session",
        channel_status.get("telegram", "skipped"),
        channel_reason.get("telegram", "n/a"),
        channel_status.get("email", "skipped"),
        channel_reason.get("email", "n/a"),
    )

    if respect_cadence and delivered_ok and not settings.dry_run:
        local_now = decision_engine.cadence.now_local()
        record_cadence_marker(
            profile_name=profile.name,
            marker_key=session_marker,
            action_type=briefing.session_key,
            local_date=local_now.date(),
            local_timezone=profile.timezone or settings.timezone,
            sent_at_local=local_now,
        )
    if delivered_ok and (not settings.dry_run or settings.persist_dry_run_session_snapshots):
        try:
            persist_snapshot(
                profile_name=profile.name,
                session_key=briefing.session_key,
                generated_at=briefing.generated_at,
                metrics=snapshot_metrics(briefing),
            )
        except Exception:
            logger.debug("Failed to persist session snapshot", exc_info=True)

    logger.info(
        "%s delivered: %d messages, %d events",
        briefing.session_title,
        len(messages),
        briefing.events_sent,
    )


def run_session_brief(settings: Settings | None = None, *, respect_cadence: bool = False) -> None:
    """Render the correct session-aware briefing for local time."""
    run_morning_briefing(
        settings,
        force_morning=False,
        auto_route_session=True,
        respect_cadence=respect_cadence,
    )


# -- Intraday Update ----------------------------------------------------------

def run_intraday_update(settings: Settings | None = None, *, respect_cadence: bool = False) -> None:
    """Render a session-based US intraday risk check."""
    run_morning_briefing(
        settings,
        respect_cadence=respect_cadence,
        auto_route_session=False,
        session_override="us_intraday_risk",
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
        if not profile.breaking_alerts_enabled:
            logger.info("Breaking alerts disabled for profile '%s'; skipping cycle.", profile.name)
            return

        decision_engine = DecisionEngine(
            settings,
            profile_name=profile.name,
            local_timezone=profile.timezone or settings.timezone,
        )
        universe = load_sector_universe(settings)
        market_svc, news_svc, _ = _build_services(settings)

        # 1) Due follow-up checks (event-driven, one max follow-up per storyline).
        now_utc = datetime.now(timezone.utc)
        due_states = due_breaking_followups(profile_name=profile.name, now_utc=now_utc)
        if due_states:
            formatter = TelegramFormatter(profile.timezone)
            messengers = _get_messengers(settings, profile, message_type="breaking")
            for state in due_states:
                local_date = decision_engine.cadence.now_local().date()
                confirmed, followup_reason, context_quotes = _evaluate_followup_confirmation(
                    state,
                    market_svc=market_svc,
                    fallback_symbols=universe.all_index_symbols[:4],
                    min_asset_move_pct=settings.breaking_followup_min_asset_move_pct,
                )
                if not confirmed:
                    close_breaking_story(
                        profile_name=profile.name,
                        storyline_key=state.storyline_key,
                        local_date=state.local_date,
                        reason=followup_reason,
                    )
                    logger.info(
                        "Breaking follow-up closed without send (storyline=%s): %s",
                        state.storyline_key,
                        followup_reason,
                    )
                    continue

                followup_alert = _build_followup_alert_from_state(
                    state,
                    context_quotes=context_quotes,
                    reason=followup_reason,
                )
                messages = formatter.format_breaking_alert(followup_alert)
                tracking_ids = [
                    f"storyline:{state.storyline_key}",
                    f"breaking:{state.storyline_key}:followup",
                ]
                _deliver(
                    messengers,
                    messages,
                    "breaking",
                    [followup_alert.event],
                    settings=settings,
                    tracking_ids=tracking_ids,
                )
                mark_followup_sent(
                    profile_name=profile.name,
                    storyline_key=state.storyline_key,
                    local_date=state.local_date if state.local_date else local_date,
                    sent_at=now_utc,
                    reason=followup_reason,
                )
                logger.info(
                    "Breaking follow-up sent for storyline=%s",
                    state.storyline_key,
                )

        # 2) Initial breaking detection and sends.
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

        if profile.healthcare_enabled and bool(profile.healthcare_preferences.get("breaking_alerts", False)):
            hc_by_event_id = {
                evt.title: evt for evt in filter_breaking_healthcare_events(
                    profile=profile,
                    events=[alert.event for alert in fresh_alerts],
                )
            }
            for alert in fresh_alerts:
                hc_evt = hc_by_event_id.get(alert.event.title)
                if hc_evt is None:
                    continue
                alert.classification.tier = "breaking"
                alert.classification.category = "healthcare_biotech"
                alert.classification.impact_score = max(alert.classification.impact_score, 6)
                alert.classification.confidence_score = max(alert.classification.confidence_score, 5)
                alert.reason = (
                    f"Healthcare catalyst ({hc_evt.event_type}, {hc_evt.severity}): {hc_evt.market_relevance} "
                    f"{hc_evt.portfolio_lens}".strip()
                )

        tier_filtered_alerts: list[BreakingAlert] = []
        for alert in fresh_alerts:
            breaking_decision = decision_engine.decide_breaking(
                classification=alert.classification,
                related_event_id=alert.event.event_id,
            )
            if breaking_decision.action_type != "send_breaking":
                logger.info(
                    "Skipping non-breaking candidate: %s (%s)",
                    alert.event.title[:90],
                    breaking_decision.reason,
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

        flood_filtered_alerts: list[BreakingAlert] = []
        local_now = decision_engine.cadence.now_local()
        for alert in storyline_filtered_alerts:
            storyline_key = alert.classification.storyline_key
            if not storyline_key:
                flood_filtered_alerts.append(alert)
                continue

            sends_last_hour = breaking_sends_last_hour(
                profile_name=profile.name,
                now_utc=now_utc,
            )
            if sends_last_hour >= settings.breaking_max_alerts_per_hour:
                logger.info(
                    "Flood control blocked breaking send (storyline=%s): %d in last hour",
                    storyline_key,
                    sends_last_hour,
                )
                continue

            if recent_storyline_send_exists(
                profile_name=profile.name,
                storyline_key=storyline_key,
                now_utc=now_utc,
                cooldown_minutes=settings.breaking_storyline_cooldown_minutes,
            ):
                logger.info(
                    "Cooldown blocked storyline repeat: %s",
                    storyline_key,
                )
                continue
            flood_filtered_alerts.append(alert)

        if not flood_filtered_alerts:
            logger.debug("No fresh breaking alerts after flood/cooldown guards")
            return

        formatter = TelegramFormatter(profile.timezone)
        messengers = _get_messengers(settings, profile, message_type="breaking")

        for alert in flood_filtered_alerts:
            storyline_key = alert.classification.storyline_key
            if storyline_key:
                alert.tracking_ids = list(dict.fromkeys(
                    (alert.tracking_ids or []) + [
                        f"storyline:{storyline_key}",
                        f"breaking:{storyline_key}:initial",
                    ]
                ))
            messages = formatter.format_breaking_alert(alert)
            _deliver(
                messengers,
                messages,
                "breaking",
                [alert.event],
                settings=settings,
                tracking_ids=alert.tracking_ids,
            )
            if storyline_key and not settings.dry_run:
                upsert_breaking_story_initial(
                    profile_name=profile.name,
                    storyline_key=storyline_key,
                    local_date=local_now.date(),
                    category=alert.classification.category,
                    event_id=alert.event.event_id,
                    event_title=alert.event.title,
                    why_markets_care=alert.reason or alert.classification.why_markets_care,
                    watch_symbols=alert.classification.watch_symbols,
                    first_sent_at=now_utc,
                    followup_due_at=now_utc + timedelta(minutes=settings.breaking_followup_delay_minutes),
                    reason="initial_breaking_sent",
                )
                followup_decision = decision_engine.decide_schedule_followup(
                    related_event_id=alert.event.event_id,
                    now=now_utc,
                    reason=(
                        "Initial BREAKING sent; follow-up due in "
                        f"{settings.breaking_followup_delay_minutes}m."
                    ),
                )
                logger.info(
                    "Cadence decision (breaking follow-up): %s | %s",
                    followup_decision.action_type,
                    followup_decision.reason,
                )
            logger.info(
                "Breaking alert sent: %s (score=%.3f)",
                alert.event.title[:60],
                alert.event.final_score,
            )


if __name__ == "__main__":
    from app.scheduler import start_scheduler

    start_scheduler()
