"""Live session archive snapshot service (Phase 8.9 Lite).

Captures lightweight local snapshots of scheduler-generated briefings so you
can later read exactly what was sent. Only live scheduler runs are stored.
Backfills, dry runs, replays, and manual sends are excluded.

No extra provider calls, no re-rendering. Only already-generated text, HTML,
and compact JSON summaries are persisted. Snapshot saving is non-blocking:
any failure is logged and delivery continues unaffected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import SessionArchiveSnapshot
from app.db.session import get_session

logger = logging.getLogger("briefing.session_snapshot_service")

_CANONICAL_SESSION_KEYS: frozenset[str] = frozenset({
    "morning",
    "europe_midday",
    "us_pre_open",
    "us_intraday_risk",
    "into_close",
    "closing_wrap",
})


@dataclass
class SnapshotCaptureRequest:
    profile_name: str
    session_key: str
    session_title: str
    local_date: date
    generated_at_utc: datetime
    timezone_name: str
    source_type: str
    delivery_attempted: bool
    delivery_success: bool
    delivery_channels: dict[str, str]
    delivery_reasons: dict[str, str]
    telegram_messages: list[str]
    email_subject: str
    email_plain_text: str
    email_html: str
    market_summary: list[dict]
    macro_summary: list[dict]
    portfolio_summary: list[dict]
    chart_selection: list[dict]
    events_count: int
    store_email_html: bool = True


def source_type_from_command_source(
    command_source: str,
    *,
    is_backfill: bool,
    is_dry_run: bool,
) -> str:
    if is_dry_run:
        return "dry_run"
    if is_backfill:
        return "backfill"
    if command_source == "scheduler":
        return "live_scheduler"
    if "replay" in command_source:
        return "replay"
    return "manual"


def should_store_snapshot(
    *,
    command_source: str,
    dry_run: bool,
    is_backfill: bool,
    session_key: str,
    delivery_attempted: bool,
    snapshots_enabled: bool,
) -> bool:
    """Return True only when this run should produce an archive snapshot."""
    if not snapshots_enabled:
        return False
    if dry_run:
        return False
    if is_backfill:
        return False
    if command_source != "scheduler":
        return False
    if session_key not in _CANONICAL_SESSION_KEYS:
        return False
    if not delivery_attempted:
        return False
    return True


# ---------------------------------------------------------------------------
# Compact summary builders (no provider calls, pure data extraction)
# ---------------------------------------------------------------------------

def _compact_market_summary(briefing) -> list[dict]:
    result = []
    try:
        for q in (briefing.market_setup.index_quotes or [])[:8]:
            result.append({
                "symbol": q.symbol,
                "display_name": q.display_name or q.symbol,
                "price": round(float(q.current_price or 0), 4),
                "change_pct": round(float(q.change_percent or 0), 4),
            })
    except Exception:
        pass
    return result


def _compact_macro_summary(briefing) -> list[dict]:
    result = []
    try:
        for m in (briefing.macro_context or [])[:8]:
            result.append({
                "name": m.name,
                "value": round(float(m.value or 0), 4),
                "change": round(float(m.change or 0), 6),
            })
    except Exception:
        pass
    return result


def _compact_portfolio_summary(briefing) -> list[dict]:
    result = []
    try:
        for evt in (briefing.portfolio_focus or [])[:10]:
            result.append({
                "title": (evt.title or "")[:120],
                "tickers": (evt.tickers or [])[:5],
            })
    except Exception:
        pass
    return result


def _compact_chart_selection(briefing) -> list[dict]:
    result = []
    try:
        selection = getattr(briefing, "morning_chart_selection", None) or []
        for chart in selection[:12]:
            result.append({
                "chart_key": getattr(chart, "chart_key", ""),
                "variant": getattr(chart, "variant", ""),
                "available": getattr(chart, "available", True),
            })
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------

def create_session_snapshot(req: SnapshotCaptureRequest) -> bool:
    """Persist a snapshot. Returns True on success. Never raises."""
    try:
        telegram_text = "\n\n---\n\n".join(req.telegram_messages) if req.telegram_messages else ""
        email_html = req.email_html if req.store_email_html else ""
        try:
            generated_at_local_str = req.generated_at_utc.astimezone(
                ZoneInfo(req.timezone_name)
            ).strftime("%Y-%m-%d %H:%M")
        except Exception:
            generated_at_local_str = req.generated_at_utc.strftime("%Y-%m-%d %H:%M")

        with get_session() as db_sess:
            existing = (
                db_sess.query(SessionArchiveSnapshot)
                .filter(
                    SessionArchiveSnapshot.profile_name == req.profile_name,
                    SessionArchiveSnapshot.local_date == req.local_date,
                    SessionArchiveSnapshot.session_key == req.session_key,
                )
                .first()
            )
            if existing:
                existing.delivery_success = req.delivery_success
                existing.delivery_channels_json = req.delivery_channels
                existing.delivery_status_json = req.delivery_reasons
                existing.telegram_text = telegram_text
                existing.email_subject = req.email_subject
                existing.email_plain_text = req.email_plain_text
                existing.email_html = email_html
                existing.market_summary_json = req.market_summary
                existing.macro_summary_json = req.macro_summary
                existing.portfolio_summary_json = req.portfolio_summary
                existing.chart_selection_json = req.chart_selection
                existing.events_count = req.events_count
            else:
                db_sess.add(SessionArchiveSnapshot(
                    profile_name=req.profile_name,
                    session_key=req.session_key,
                    session_title=req.session_title,
                    local_date=req.local_date,
                    generated_at_utc=req.generated_at_utc,
                    generated_at_local_str=generated_at_local_str,
                    timezone_name=req.timezone_name,
                    source_type=req.source_type,
                    delivery_attempted=req.delivery_attempted,
                    delivery_success=req.delivery_success,
                    delivery_channels_json=req.delivery_channels,
                    delivery_status_json=req.delivery_reasons,
                    telegram_text=telegram_text,
                    email_subject=req.email_subject,
                    email_plain_text=req.email_plain_text,
                    email_html=email_html,
                    market_summary_json=req.market_summary,
                    macro_summary_json=req.macro_summary,
                    portfolio_summary_json=req.portfolio_summary,
                    chart_selection_json=req.chart_selection,
                    events_count=req.events_count,
                ))
        return True
    except Exception:
        logger.exception(
            "Failed to save session archive snapshot | profile=%s date=%s session=%s",
            req.profile_name, req.local_date, req.session_key,
        )
        return False


def list_session_snapshots(profile_name: str, target_date: date) -> list[dict]:
    """Return summary rows for all sessions on a given date."""
    _SESSION_ORDER = {k: i for i, k in enumerate([
        "morning", "europe_midday", "us_pre_open",
        "us_intraday_risk", "into_close", "closing_wrap",
    ])}
    with get_session() as db_sess:
        rows = (
            db_sess.query(SessionArchiveSnapshot)
            .filter(
                SessionArchiveSnapshot.profile_name == profile_name,
                SessionArchiveSnapshot.local_date == target_date,
            )
            .all()
        )
    rows = sorted(rows, key=lambda r: _SESSION_ORDER.get(r.session_key, 99))
    return [
        {
            "session_key": r.session_key,
            "session_title": r.session_title or r.session_key,
            "local_date": r.local_date.isoformat() if r.local_date else "",
            "generated_at_local": r.generated_at_local_str or "",
            "source_type": r.source_type,
            "delivery_success": r.delivery_success,
            "delivery_channels": r.delivery_channels_json or {},
            "events_count": r.events_count or 0,
            "has_telegram": bool(r.telegram_text),
            "has_email": bool(r.email_subject),
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


def get_session_snapshot(profile_name: str, target_date: date, session_key: str) -> dict | None:
    """Return the full snapshot for one session. Reads from SQLite only."""
    with get_session() as db_sess:
        row = (
            db_sess.query(SessionArchiveSnapshot)
            .filter(
                SessionArchiveSnapshot.profile_name == profile_name,
                SessionArchiveSnapshot.local_date == target_date,
                SessionArchiveSnapshot.session_key == session_key,
            )
            .first()
        )
    if row is None:
        return None
    return {
        "profile_name": row.profile_name,
        "session_key": row.session_key,
        "session_title": row.session_title or row.session_key,
        "local_date": row.local_date.isoformat() if row.local_date else "",
        "generated_at_utc": row.generated_at_utc.isoformat() if row.generated_at_utc else "",
        "generated_at_local": row.generated_at_local_str or "",
        "timezone_name": row.timezone_name or "",
        "source_type": row.source_type,
        "delivery_attempted": row.delivery_attempted,
        "delivery_success": row.delivery_success,
        "delivery_channels": row.delivery_channels_json or {},
        "delivery_status": row.delivery_status_json or {},
        "telegram_text": row.telegram_text or "",
        "email_subject": row.email_subject or "",
        "email_plain_text": row.email_plain_text or "",
        "email_html": row.email_html or "",
        "market_summary": row.market_summary_json or [],
        "macro_summary": row.macro_summary_json or [],
        "portfolio_summary": row.portfolio_summary_json or [],
        "chart_selection": row.chart_selection_json or [],
        "events_count": row.events_count or 0,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def prune_old_snapshots(profile_name: str, retention_days: int = 30) -> int:
    """Delete snapshots older than retention_days. Returns count deleted."""
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).date()
    with get_session() as db_sess:
        rows = (
            db_sess.query(SessionArchiveSnapshot)
            .filter(
                SessionArchiveSnapshot.profile_name == profile_name,
                SessionArchiveSnapshot.local_date < cutoff_date,
            )
            .all()
        )
        count = len(rows)
        for row in rows:
            db_sess.delete(row)
    return count
