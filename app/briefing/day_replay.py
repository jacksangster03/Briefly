"""Manual day replay/session tester for briefing QA.

Runs only when explicitly called from CLI and never writes real delivery
idempotency markers or sent-message records.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_generator import MorningBriefingGenerator
from app.briefing.session_materiality import compute_materiality
from app.briefing.session_routing import SessionWindow, session_window_for_key
from app.briefing.session_snapshot import build_what_changed_lines, snapshot_metrics
from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.db.models import MarketSnapshot
from app.db.session import get_session, init_db
from app.logger import get_logger
from app.messaging.email import EmailMessenger
from app.messaging.telegram import TelegramMessenger
from app.personalization.user_profile import load_user_profile
from app.schemas.briefings import session_mode_for
from app.settings import Settings
from app.universe.sector_universe import load_sector_universe

logger = get_logger("day_replay")


@dataclass
class DayReplaySession:
    session_key: str
    session_title: str
    replay_time_local: datetime
    eligible: bool
    future: bool = False
    future_reason: str = ""
    materiality_score: int | None = None
    materiality_action: str | None = None
    materiality_reasons: list[str] = field(default_factory=list)
    selected_charts: list[str] = field(default_factory=list)
    suppressed_charts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    send_action: str = "skipped"
    sent_channels: list[str] = field(default_factory=list)
    telegram_chars: int = 0
    email_chars: int = 0
    data_timestamp_local: str = ""
    replay_data_mode: str = "latest_available"


@dataclass
class DayReplayResult:
    replay_date: date
    timezone_name: str
    now_local: datetime
    mode: str
    data_note: str
    sessions: list[DayReplaySession]
    replay_namespace: str
    replay_data_mode: str = "latest_available"
    provider_cache_hits: int = 0
    provider_cache_misses: int = 0
    replay_summary_counts: dict[str, int] = field(default_factory=dict)
    chart_counts_by_session: dict[str, int] = field(default_factory=dict)
    healthcare_items_included: int = 0
    healthcare_items_suppressed: int = 0


_REPLAY_POINTS: tuple[tuple[str, time], ...] = (
    ("morning", time(10, 25)),
    ("europe_midday", time(13, 25)),
    ("us_pre_open", time(15, 20)),
    ("us_intraday_risk", time(17, 25)),
    ("into_close", time(21, 55)),
    ("closing_wrap", time(22, 15)),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_replay_date(raw: str, *, now_local: datetime) -> date:
    text = (raw or "today").strip().lower()
    if text == "today":
        return now_local.date()
    if text == "yesterday":
        return (now_local - timedelta(days=1)).date()
    return datetime.strptime(text, "%Y-%m-%d").date()


def _resolve_cutoff(
    *,
    replay_date: date,
    until: str,
    now_local: datetime,
    tz: ZoneInfo,
) -> datetime:
    until_mode = (until or "now").strip().lower()
    if until_mode in {"close", "full-day"}:
        return datetime.combine(replay_date, time(23, 59), tzinfo=tz)
    if replay_date < now_local.date():
        return datetime.combine(replay_date, time(23, 59), tzinfo=tz)
    if replay_date > now_local.date():
        return datetime.combine(replay_date, time(0, 0), tzinfo=tz)
    return now_local


def _comparable_session_keys(session_key: str) -> tuple[str, ...]:
    key = (session_key or "morning").strip().lower()
    if key == "morning":
        return ("closing_wrap", "morning")
    if key == "europe_midday":
        return ("morning", "europe_midday")
    if key == "us_pre_open":
        return ("europe_midday", "morning", "us_pre_open")
    if key == "us_intraday_risk":
        return ("us_pre_open", "europe_midday", "morning", "us_intraday_risk")
    if key == "into_close":
        return ("us_intraday_risk", "us_pre_open", "morning", "into_close")
    if key == "closing_wrap":
        return ("into_close", "us_intraday_risk", "us_pre_open", "morning", "closing_wrap")
    return ("morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap")


def _build_replay_plan(
    *,
    replay_date: date,
    cutoff_local: datetime,
    tz: ZoneInfo,
    force_all: bool,
) -> list[DayReplaySession]:
    plan: list[DayReplaySession] = []
    for key, anchor in _REPLAY_POINTS:
        window = session_window_for_key(key)
        anchor_local = datetime.combine(replay_date, anchor, tzinfo=tz)
        if key == "into_close" and replay_date == cutoff_local.date():
            if time(17, 30) <= cutoff_local.timetz().replace(tzinfo=None) < time(22, 0):
                anchor_local = cutoff_local.replace(second=0, microsecond=0)
        eligible = anchor_local <= cutoff_local
        future_reason = ""
        if not eligible:
            if key == "closing_wrap":
                future_reason = "not eligible until 22:00"
            else:
                future_reason = f"future checkpoint at {anchor_local.strftime('%H:%M')}"
        if force_all:
            eligible = True
            future_reason = ""
        plan.append(
            DayReplaySession(
                session_key=window.key,
                session_title=window.title,
                replay_time_local=anchor_local,
                eligible=eligible,
                future=not eligible,
                future_reason=future_reason,
                send_action="future" if not eligible else "skipped",
            )
        )
    return plan


def _find_previous_metrics(
    *,
    snapshots: list[tuple[str, datetime, dict[str, float]]],
    session_key: str,
    current_time: datetime,
) -> dict[str, float]:
    comparable = set(_comparable_session_keys(session_key))
    candidates = [
        metrics
        for key, ts, metrics in snapshots
        if key in comparable and ts < current_time
    ]
    if not candidates:
        return {}
    return candidates[-1]


def _cache_key(args: tuple, kwargs: dict) -> tuple:
    return (repr(args), repr(sorted((str(k), repr(v)) for k, v in kwargs.items())))


def _memoize_method(obj: object, method_name: str) -> dict[str, int]:
    store: dict[tuple, object] = {}
    stats = {"hits": 0, "misses": 0}
    original = getattr(obj, method_name, None)
    if original is None:
        return stats

    def _wrapped(*args, **kwargs):
        key = _cache_key(args, kwargs)
        if key in store:
            stats["hits"] += 1
            return deepcopy(store[key])
        stats["misses"] += 1
        result = original(*args, **kwargs)
        store[key] = deepcopy(result)
        return result

    setattr(obj, method_name, _wrapped)
    return stats


def _attach_replay_cache(
    *,
    market_svc: MarketDataService,
    news_svc: NewsDataService,
    macro_svc: MacroDataService,
) -> list[dict[str, int]]:
    stats: list[dict[str, int]] = []
    for method in ("get_quote", "get_quotes", "get_price_history"):
        stats.append(_memoize_method(market_svc, method))
    for method in ("fetch_all",):
        stats.append(_memoize_method(news_svc, method))
    for method in (
        "get_morning_macro",
        "get_ecb_snapshot",
        "get_eurostat_snapshot",
        "get_commodity_strip",
        "get_treasury_yields",
    ):
        stats.append(_memoize_method(macro_svc, method))
    return stats


def _provider_note(briefing) -> str:
    raw = str((briefing.data_freshness or {}).get("Provider Health", "")).strip()
    if not raw:
        return "Provider notes: core providers available."
    lower = raw.lower()
    items: list[str] = []
    if "gdelt:0" in lower:
        items.append("GDELT unavailable")
    if "fmp:0" in lower:
        items.append("FMP unavailable")
    if "marketaux:0" in lower:
        items.append("Marketaux unavailable")
    if not items:
        return "Provider notes: core providers available."
    return "Provider notes: " + "; ".join(items) + "; core providers available."


def _latest_data_timestamp_local(briefing, tz: ZoneInfo) -> str:
    timestamps: list[datetime] = []
    for quote in (
        list(briefing.market_setup.index_quotes)
        + list(briefing.market_setup.macro_quotes)
        + list(briefing.watchlist_quotes)
        + list(briefing.portfolio_quotes)
    ):
        if quote.timestamp:
            timestamps.append(quote.timestamp)
    if not timestamps:
        return "unavailable"
    latest = max(ts.astimezone(tz) if ts.tzinfo else ts.replace(tzinfo=tz) for ts in timestamps)
    return latest.strftime("%Y-%m-%d %H:%M %Z")


def _parse_channels(send_test: str, *, email_only: bool, telegram_only: bool) -> list[str]:
    channels: list[str] = []
    for part in (send_test or "").split(","):
        token = part.strip().lower()
        if token in {"telegram", "email"} and token not in channels:
            channels.append(token)
    if telegram_only:
        channels = [channel for channel in channels if channel == "telegram"]
    if email_only:
        channels = [channel for channel in channels if channel == "email"]
    return channels


def _with_test_banner_messages(
    *,
    messages: list[str],
    session_title: str,
    replay_time_local: datetime,
) -> list[str]:
    if not messages:
        return messages
    banner = (
        "<b>[TEST DAY REPLAY - NOT LIVE]</b>\n"
        f"<b>{session_title.upper()}</b> | replay_time {replay_time_local.strftime('%H:%M')}"
    )
    first = f"{banner}\n\n{messages[0]}"
    return [first] + messages[1:]


def _with_test_banner_email(email_content, *, session_title: str, replay_time_local: datetime):
    subject = f"[TEST Replay] {session_title} - {replay_time_local.strftime('%a %d %b %Y')}"
    plain_prefix = (
        "[TEST DAY REPLAY - NOT LIVE]\n"
        f"{session_title} | replay_time {replay_time_local.strftime('%H:%M')}\n\n"
    )
    html_prefix = (
        "<div style=\"font-family:Arial,sans-serif;background:#2a1010;color:#ffd7d7;"
        "padding:10px 14px;font-size:12px;font-weight:700;border-bottom:1px solid #6f2a2a;\">"
        "[TEST DAY REPLAY - NOT LIVE] "
        f"{session_title} | replay_time {replay_time_local.strftime('%H:%M')}"
        "</div>"
    )
    return email_content.model_copy(
        update={
            "subject": subject,
            "plain_text": plain_prefix + email_content.plain_text,
            "html_body": html_prefix + email_content.html_body,
        }
    )


def _with_replay_banner_messages(
    *,
    messages: list[str],
    session_title: str,
    replay_time_local: datetime,
    data_timestamp_local: str,
) -> list[str]:
    if not messages:
        return messages
    banner = (
        "<i>REPLAY MODE: session slot simulated at "
        f"{replay_time_local.strftime('%Y-%m-%d %H:%M %Z')}; data as of {data_timestamp_local}. "
        "Not a historical point-in-time replay.</i>"
    )
    return [f"{banner}\n\n{messages[0]}"] + messages[1:]


def _with_replay_banner_email(
    email_content,
    *,
    replay_time_local: datetime,
    data_timestamp_local: str,
):
    plain_prefix = (
        "REPLAY MODE: session slot simulated at "
        f"{replay_time_local.strftime('%Y-%m-%d %H:%M %Z')}; data as of {data_timestamp_local}. "
        "Not a historical point-in-time replay.\n\n"
    )
    html_prefix = (
        "<div style=\"font-family:Arial,sans-serif;background:#142235;color:#d7e7ff;"
        "padding:10px 14px;font-size:11px;line-height:1.35;border-bottom:1px solid #254260;\">"
        "REPLAY MODE: session slot simulated at "
        f"{replay_time_local.strftime('%Y-%m-%d %H:%M %Z')}; data as of {data_timestamp_local}. "
        "Not a historical point-in-time replay."
        "</div>"
    )
    return email_content.model_copy(
        update={
            "plain_text": plain_prefix + email_content.plain_text,
            "html_body": html_prefix + email_content.html_body,
        }
    )


def _deliver_test(
    *,
    settings: Settings,
    telegram_messages: list[str],
    email_content,
    channels: list[str],
) -> list[str]:
    sent: list[str] = []
    send_settings = settings.model_copy(deep=True)
    send_settings.dry_run = False
    if "telegram" in channels:
        messenger = TelegramMessenger(send_settings)
        if messenger.is_configured() and messenger.send_messages(telegram_messages):
            sent.append("telegram")
    if "email" in channels:
        messenger = EmailMessenger(send_settings)
        if messenger.is_configured() and messenger.send_rich(
            subject=email_content.subject,
            plain_text=email_content.plain_text,
            html_body=email_content.html_body,
            inline_assets=email_content.inline_assets,
        ):
            sent.append("email")
    return sent


def _persist_replay_snapshot(
    *,
    profile_name: str,
    session_key: str,
    generated_at: datetime,
    metrics: dict[str, float],
    replay_namespace: str,
) -> None:
    with get_session() as session:
        for key, value in metrics.items():
            session.add(
                MarketSnapshot(
                    symbol=f"__RP__{key[:10]}",
                    display_name=profile_name,
                    price=float(value or 0.0),
                    snapshot_type=f"replay:{replay_namespace}:{session_key}",
                    timestamp=generated_at,
                )
            )


def _print_replay_summary(result: DayReplayResult) -> None:
    print(
        f"DAY REPLAY | {result.replay_date.isoformat()} | {result.timezone_name} | "
        f"now {result.now_local.strftime('%H:%M')}"
    )
    print(f"Mode: {result.mode}")
    print(f"Replay source mode: {result.replay_data_mode}")
    print(f"Replay data: {result.data_note}")
    print(f"Provider cache hits: {result.provider_cache_hits}")
    print("Eligible sessions:")
    for row in result.sessions:
        marker = "✓" if row.eligible else "○"
        base = (
            f"{marker} {row.session_title:<24} "
            f"replay_time={row.replay_time_local.strftime('%H:%M')}"
        )
        if row.eligible:
            extra = f" action={row.send_action}"
            if row.materiality_score is not None:
                extra += f" score={row.materiality_score}"
            if row.selected_charts:
                extra += f" charts={len(row.selected_charts)}"
            if row.data_timestamp_local:
                extra += f" data_ts={row.data_timestamp_local}"
            print(base + extra)
        else:
            print(base + f" {row.future_reason}")
    counts = result.replay_summary_counts or {}
    print("Summary:")
    print(
        "  generated="
        f"{counts.get('generated_only', 0)} "
        f"test_sent={counts.get('test_sent', 0)} "
        f"suppressed={counts.get('suppressed', 0)} "
        f"held={counts.get('held', 0)} "
        f"future={counts.get('future', 0)}"
    )
    print(
        "  provider_calls="
        f"{result.provider_cache_misses} "
        f"provider_calls_avoided={result.provider_cache_hits}"
    )
    if result.chart_counts_by_session:
        rendered = ", ".join(f"{k}:{v}" for k, v in result.chart_counts_by_session.items())
        print(f"  chart_counts={rendered}")
    print(
        "  healthcare_items="
        f"included:{result.healthcare_items_included} suppressed:{result.healthcare_items_suppressed}"
    )


def run_day_replay(
    settings: Settings,
    *,
    replay_date: str = "today",
    until: str = "now",
    profile_override: str | None = None,
    send_test: str = "",
    force_all: bool = False,
    respect_materiality: bool = True,
    include_breaking: bool = False,
    persist_replay_snapshots: bool = False,
    healthcare_enabled: bool = False,
    vertical: str = "",
    max_sessions: int = 8,
    show_output: bool = False,
    email_only: bool = False,
    telegram_only: bool = False,
) -> DayReplayResult:
    """Run a manual day replay across eligible session checkpoints."""
    init_db()
    now_utc = _utcnow()
    tz_name = settings.timezone
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    target_date = _parse_replay_date(replay_date, now_local=now_local)
    cutoff_local = _resolve_cutoff(replay_date=target_date, until=until, now_local=now_local, tz=tz)
    replay_namespace = now_local.strftime("rp%Y%m%d%H%M%S")
    plan = _build_replay_plan(replay_date=target_date, cutoff_local=cutoff_local, tz=tz, force_all=force_all)
    if max_sessions > 0:
        plan = plan[:max_sessions]

    profile = load_user_profile(settings)
    if profile_override and profile_override != profile.name:
        profile.name = profile_override
    if healthcare_enabled or (vertical or "").strip().lower() == "healthcare":
        profile.healthcare["enabled"] = True

    universe = load_sector_universe(settings)
    market_svc = MarketDataService(settings)
    news_svc = NewsDataService(settings)
    macro_svc = MacroDataService(settings)
    cache_stats = _attach_replay_cache(market_svc=market_svc, news_svc=news_svc, macro_svc=macro_svc)
    generator = MorningBriefingGenerator(
        settings=settings,
        profile=profile,
        universe=universe,
        market_data=market_svc,
        news_data=news_svc,
        macro_data=macro_svc,
    )
    telegram_formatter = TelegramFormatter(profile.timezone)
    email_formatter = EmailFormatter(profile.timezone)
    replay_snapshots: list[tuple[str, datetime, dict[str, float]]] = []
    channels = _parse_channels(send_test, email_only=email_only, telegram_only=telegram_only)
    mode = "send-test" if channels else "dry-run"
    data_note = "latest available quotes/news unless stored intraday snapshots exist"
    summary_counts: dict[str, int] = {"generated_only": 0, "test_sent": 0, "suppressed": 0, "held": 0, "future": 0}
    chart_counts_by_session: dict[str, int] = {}
    healthcare_included = 0
    healthcare_suppressed = 0

    for row in plan:
        if not row.eligible:
            continue
        replay_time_utc = row.replay_time_local.astimezone(timezone.utc)
        briefing = generator.generate(session_key=row.session_key, session_title=row.session_title)
        briefing.generated_at = replay_time_utc
        briefing.session_mode = session_mode_for(replay_time_utc.astimezone(tz))
        data_ts_local = _latest_data_timestamp_local(briefing, tz)
        row.data_timestamp_local = data_ts_local
        row.replay_data_mode = "latest_available"
        # Make replay semantics explicit inside user-facing freshness fields.
        freshness = dict(briefing.data_freshness or {})
        freshness["Generated"] = (
            f"for replay slot: {row.replay_time_local.strftime('%Y-%m-%d %H:%M %Z')}"
        )
        freshness["Replay data snapshot"] = f"latest available, {data_ts_local}"
        briefing.data_freshness = freshness

        previous = _find_previous_metrics(
            snapshots=replay_snapshots,
            session_key=row.session_key,
            current_time=replay_time_utc,
        )
        current = snapshot_metrics(briefing)
        briefing.what_changed_lines = build_what_changed_lines(previous=previous, current=current)
        if not previous:
            briefing.what_changed_lines = ["No prior comparable replay snapshot available."]

        materiality = compute_materiality(briefing, previous=previous)
        row.materiality_score = materiality.score
        row.materiality_action = materiality.decision
        row.materiality_reasons = list(materiality.reasons)

        should_send = row.session_key == "morning" or not respect_materiality or materiality.decision in {
            "send_session",
            "breaking_alert",
        }
        if force_all:
            should_send = True
        if materiality.decision == "breaking_alert" and not include_breaking and row.session_key != "morning":
            row.warnings.append("Breaking threshold hit (not sent; rerun with --include-breaking).")
            should_send = False

        bundle_meta = dict((briefing.morning_chart_bundle or {}).get("meta") or {})
        row.selected_charts = list(bundle_meta.get("selected_charts") or [])
        row.suppressed_charts = list(bundle_meta.get("suppressed_charts") or [])
        if bundle_meta.get("required_charts_missing"):
            row.warnings.append(
                "required_charts_missing=" + ",".join(bundle_meta.get("required_charts_missing") or [])
            )

        telegram_messages = telegram_formatter.format_morning_briefing(briefing)
        email_content = email_formatter.format_morning_briefing(briefing)
        telegram_messages = _with_replay_banner_messages(
            messages=telegram_messages,
            session_title=row.session_title,
            replay_time_local=row.replay_time_local,
            data_timestamp_local=data_ts_local,
        )
        email_content = _with_replay_banner_email(
            email_content,
            replay_time_local=row.replay_time_local,
            data_timestamp_local=data_ts_local,
        )
        row.telegram_chars = sum(len(msg) for msg in telegram_messages)
        row.email_chars = len(email_content.plain_text or "")
        row.warnings.append(_provider_note(briefing))
        chart_counts_by_session[row.session_key] = len(row.selected_charts)
        if briefing.healthcare_intelligence:
            healthcare_included += len(briefing.healthcare_intelligence.items or [])
            healthcare_suppressed += int(briefing.healthcare_intelligence.suppressed_count or 0)

        if should_send:
            row.send_action = "generated_only"
            replay_snapshots.append((row.session_key, replay_time_utc, current))
            if persist_replay_snapshots:
                _persist_replay_snapshot(
                    profile_name=profile.name,
                    session_key=row.session_key,
                    generated_at=replay_time_utc,
                    metrics=current,
                    replay_namespace=replay_namespace,
                )
            if show_output:
                from app.main import _print_email_output, _print_terminal_output

                _print_terminal_output(telegram_messages, f"day_replay:{row.session_key}")
                _print_email_output(
                    email_content.subject,
                    email_content.plain_text,
                    email_content.inline_assets,
                    f"day_replay:{row.session_key}",
                )
            if channels:
                test_messages = _with_test_banner_messages(
                    messages=telegram_messages,
                    session_title=row.session_title,
                    replay_time_local=row.replay_time_local,
                )
                test_email = _with_test_banner_email(
                    email_content,
                    session_title=row.session_title,
                    replay_time_local=row.replay_time_local,
                )
                row.sent_channels = _deliver_test(
                    settings=settings,
                    telegram_messages=test_messages,
                    email_content=test_email,
                    channels=channels,
                )
                row.send_action = "test_sent" if row.sent_channels else "generated_only"
            summary_counts[row.send_action] = summary_counts.get(row.send_action, 0) + 1
        else:
            row.send_action = "suppressed" if materiality.decision == "suppress" else "held"
            summary_counts[row.send_action] = summary_counts.get(row.send_action, 0) + 1
            if show_output:
                from app.main import _print_terminal_output

                _print_terminal_output(
                    [
                        f"{row.session_title} suppressed by materiality "
                        f"(score={row.materiality_score}, decision={row.materiality_action})."
                    ],
                    f"day_replay:{row.session_key}",
                )

    for row in plan:
        if not row.eligible:
            summary_counts["future"] = summary_counts.get("future", 0) + 1

    result = DayReplayResult(
        replay_date=target_date,
        timezone_name=tz_name,
        now_local=now_local,
        mode=mode,
        data_note=data_note,
        sessions=plan,
        replay_namespace=replay_namespace,
        replay_data_mode="latest_available",
        provider_cache_hits=sum(int(stat.get("hits", 0)) for stat in cache_stats),
        provider_cache_misses=sum(int(stat.get("misses", 0)) for stat in cache_stats),
        replay_summary_counts=summary_counts,
        chart_counts_by_session=chart_counts_by_session,
        healthcare_items_included=healthcare_included,
        healthcare_items_suppressed=healthcare_suppressed,
    )
    _print_replay_summary(result)
    logger.info(
        "Day replay complete | replay_date=%s now=%s mode=%s sessions=%d replay_mode=true "
        "replay_persist_snapshots=%s replay_delivery_channels=%s replay_namespace=%s",
        target_date.isoformat(),
        now_local.isoformat(timespec="minutes"),
        mode,
        len(plan),
        persist_replay_snapshots,
        ",".join(channels) if channels else "none",
        replay_namespace,
    )
    return result
