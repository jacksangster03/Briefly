"""Phase 9.8 — Exact snapshot replay.

Re-delivers a stored session snapshot through the live messenger channels
without making any provider or LLM calls. The content sent is exactly what
was stored at capture time, prefixed with a SNAPSHOT REPLAY banner so the
recipient can distinguish replays from live sends.

Only call this from the CLI or tests. The scheduler never uses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.briefing.session_snapshot_service import get_session_snapshot
from app.db.session import init_db
from app.logger import get_logger
from app.messaging.email import EmailMessenger
from app.messaging.telegram import TelegramMessenger

logger = get_logger("snapshot_replay")

# Channels accepted by the --channel CLI flag
VALID_CHANNELS = frozenset({"all", "telegram", "email"})


@dataclass
class ReplayResult:
    profile_name: str
    session_key: str
    local_date: date
    dry_run: bool
    channel: str                      # "all" | "telegram" | "email"
    telegram_attempted: bool = False
    telegram_ok: bool = False
    telegram_reason: str = ""
    email_attempted: bool = False
    email_ok: bool = False
    email_reason: str = ""
    errors: list[str] = field(default_factory=list)


def _telegram_banner(snap: dict) -> str:
    return (
        "<b>SNAPSHOT REPLAY — NOT LIVE</b>\n"
        f"Session: {snap.get('session_title') or snap.get('session_key', '')}\n"
        f"Original date: {snap.get('local_date', '')}\n"
        f"Originally generated: {snap.get('generated_at_local') or snap.get('generated_at_utc', '')}\n"
        "Content: exactly as originally delivered — no provider data refreshed\n"
        "================================\n\n"
    )


def _email_banner_plain(snap: dict) -> str:
    return (
        "SNAPSHOT REPLAY — NOT LIVE\n"
        f"Session: {snap.get('session_title') or snap.get('session_key', '')}\n"
        f"Original date: {snap.get('local_date', '')}\n"
        f"Originally generated: {snap.get('generated_at_local') or snap.get('generated_at_utc', '')}\n"
        "Content: exactly as originally delivered — no provider data refreshed.\n"
        f"{'=' * 64}\n\n"
    )


def _email_banner_html(snap: dict) -> str:
    return (
        '<div style="background:#e8f4f8;border-left:4px solid #0d6efd;'
        'padding:12px 16px;margin-bottom:16px;font-family:Arial,sans-serif;font-size:13px;">'
        "<strong>SNAPSHOT REPLAY — NOT LIVE</strong><br>"
        f"Session: {snap.get('session_title') or snap.get('session_key', '')}<br>"
        f"Original date: {snap.get('local_date', '')}<br>"
        f"Originally generated: {snap.get('generated_at_local') or snap.get('generated_at_utc', '')}<br>"
        "Content: exactly as originally delivered — no provider data refreshed."
        "</div>"
    )


def replay_snapshot(
    *,
    profile_name: str,
    session_key: str,
    local_date: date,
    channel: str = "all",
    no_banner: bool = False,
    settings,
) -> ReplayResult:
    """Load a snapshot from SQLite and re-deliver it via configured messengers.

    No provider calls are made. No idempotency keys are written. No new
    snapshot is created. The send is recorded in ``sent_messages`` for
    audit purposes.

    Args:
        profile_name: Profile whose snapshot archive is queried.
        session_key: Session key, e.g. ``"morning"``, ``"us_pre_open"``.
        local_date: The original session date.
        channel: ``"all"``, ``"telegram"``, or ``"email"``. Overrides
            the profile's delivery plan.
        no_banner: When True, suppress the SNAPSHOT REPLAY prefix.
        settings: A ``Settings`` instance (dry_run is respected).

    Returns:
        A ``ReplayResult`` describing what was attempted and whether it succeeded.
    """
    init_db()
    result = ReplayResult(
        profile_name=profile_name,
        session_key=session_key,
        local_date=local_date,
        dry_run=settings.dry_run,
        channel=channel,
    )

    snap = get_session_snapshot(profile_name, local_date, session_key)
    if snap is None:
        msg = (
            f"No snapshot found for profile={profile_name!r} "
            f"session={session_key!r} date={local_date.isoformat()!r}. "
            "Only scheduler-generated live sessions are stored (not dry runs or backfills)."
        )
        result.errors.append(msg)
        logger.error(msg)
        return result

    want_telegram = channel in {"all", "telegram"}
    want_email = channel in {"all", "email"}

    # ── Telegram ─────────────────────────────────────────────────────────────
    if want_telegram:
        result.telegram_attempted = True
        telegram_text: str = snap.get("telegram_text") or ""
        if not telegram_text:
            result.telegram_reason = "no Telegram text stored in snapshot"
            logger.warning("Snapshot replay: no Telegram text stored for %s/%s/%s", profile_name, local_date, session_key)
        else:
            if not no_banner:
                telegram_text = _telegram_banner(snap) + telegram_text
            messenger = TelegramMessenger(settings)
            if not messenger.is_configured() and not settings.dry_run:
                result.telegram_reason = "Telegram not configured"
                logger.warning("Snapshot replay: Telegram not configured; skipping")
            else:
                ok = messenger.send_messages([telegram_text])
                result.telegram_ok = bool(ok)
                result.telegram_reason = "" if ok else (str(getattr(messenger, "last_error", "")) or "send returned False")
                if ok:
                    logger.info(
                        "Snapshot replay: Telegram sent | profile=%s session=%s date=%s dry_run=%s",
                        profile_name, session_key, local_date, settings.dry_run,
                    )
                else:
                    logger.error(
                        "Snapshot replay: Telegram failed | profile=%s session=%s date=%s reason=%s",
                        profile_name, session_key, local_date, result.telegram_reason,
                    )
                    result.errors.append(f"telegram: {result.telegram_reason}")

    # ── Email ─────────────────────────────────────────────────────────────────
    if want_email:
        result.email_attempted = True
        email_subject: str = snap.get("email_subject") or ""
        email_plain: str = snap.get("email_plain_text") or ""
        email_html: str = snap.get("email_html") or ""

        if not email_plain and not email_html:
            result.email_reason = "no email content stored in snapshot"
            logger.warning("Snapshot replay: no email content stored for %s/%s/%s", profile_name, local_date, session_key)
        else:
            if not no_banner:
                email_subject = f"[REPLAY] {email_subject}" if email_subject else "[REPLAY] Snapshot Replay"
                email_plain = _email_banner_plain(snap) + email_plain
                email_html = _email_banner_html(snap) + email_html

            messenger = EmailMessenger(settings)
            if not messenger.is_configured() and not settings.dry_run:
                result.email_reason = "email not configured"
                logger.warning("Snapshot replay: email not configured; skipping")
            else:
                ok = messenger.send_rich(
                    subject=email_subject,
                    plain_text=email_plain,
                    html_body=email_html or f"<html><body>{email_plain}</body></html>",
                    inline_assets=[],  # inline assets are encoded in stored HTML; no re-attachment needed
                )
                result.email_ok = bool(ok)
                result.email_reason = "" if ok else (str(getattr(messenger, "last_error", "")) or "send returned False")
                if ok:
                    logger.info(
                        "Snapshot replay: email sent | profile=%s session=%s date=%s dry_run=%s",
                        profile_name, session_key, local_date, settings.dry_run,
                    )
                else:
                    logger.error(
                        "Snapshot replay: email failed | profile=%s session=%s date=%s reason=%s",
                        profile_name, session_key, local_date, result.email_reason,
                    )
                    result.errors.append(f"email: {result.email_reason}")

    return result
