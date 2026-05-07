"""Deterministic session-to-session delta helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.briefing.session_metadata import label_for
from app.briefing.session_snapshot_service import get_session_snapshot
from app.db.models import SessionSendState
from app.db.session import get_session
from app.schemas.events import NormalisedEvent


@dataclass(frozen=True)
class SessionDelta:
    previous_session_key: str | None
    previous_session_label: str
    previous_sent_at: str
    new_news_items: list[NormalisedEvent]
    repeated_news_items: list[NormalisedEvent]
    carried_forward_items: list[NormalisedEvent]
    stale_numbers: list[str]
    what_changed_header: str
    inclusion_reason: str = ""


def previous_session_context(
    *,
    profile_name: str,
    session_key: str,
    local_date,
    timezone_name: str,
) -> tuple[str | None, str, str]:
    prev_key = _previous_session_key(session_key)
    if not prev_key:
        return None, "", ""
    with get_session() as db:
        row = (
            db.query(SessionSendState)
            .filter(
                SessionSendState.profile_name == profile_name,
                SessionSendState.session_key == prev_key,
                SessionSendState.local_date == local_date,
                SessionSendState.replay_namespace == "",
                SessionSendState.success.is_(True),
            )
            .order_by(SessionSendState.sent_at.desc(), SessionSendState.id.desc())
            .first()
        )
    if row is None:
        return prev_key, label_for(prev_key), ""
    local_ts = _as_local(row.sent_at, timezone_name).strftime("%Y-%m-%d %H:%M %Z") if row.sent_at else ""
    return prev_key, label_for(prev_key), local_ts


def split_news_since_previous(
    *,
    profile_name: str,
    session_key: str,
    local_date,
    timezone_name: str,
    events: list[NormalisedEvent],
) -> SessionDelta:
    prev_key, prev_label, prev_sent = previous_session_context(
        profile_name=profile_name,
        session_key=session_key,
        local_date=local_date,
        timezone_name=timezone_name,
    )
    if not prev_key:
        return SessionDelta(
            previous_session_key=None,
            previous_session_label="",
            previous_sent_at="",
            new_news_items=list(events),
            repeated_news_items=[],
            carried_forward_items=[],
            stale_numbers=[],
            what_changed_header="WHAT CHANGED",
            inclusion_reason="no_previous_session",
        )

    snapshot = get_session_snapshot(profile_name, local_date, prev_key)
    seen_ids = _seen_title_keys_from_snapshot(snapshot)

    new_items: list[NormalisedEvent] = []
    repeated_items: list[NormalisedEvent] = []
    carried: list[NormalisedEvent] = []
    for evt in events:
        tkey = f"title:{(evt.title or '').strip().lower()}"
        if tkey in seen_ids:
            repeated_items.append(evt)
            # Keep a small carried-forward context set if story is large.
            if int(evt.cluster_size or 1) >= 8:
                carried.append(evt)
            continue
        new_items.append(evt)

    header = f"WHAT CHANGED SINCE {prev_label.upper()}" if prev_label else "WHAT CHANGED"
    return SessionDelta(
        previous_session_key=prev_key,
        previous_session_label=prev_label,
        previous_sent_at=prev_sent,
        new_news_items=new_items,
        repeated_news_items=repeated_items,
        carried_forward_items=carried[:2],
        stale_numbers=[],
        what_changed_header=header,
        inclusion_reason="incremental_split",
    )


def split_events_against_previous_snapshot(
    *,
    profile_name: str,
    session_key: str,
    local_date,
    timezone_name: str,
    events: list[NormalisedEvent],
) -> tuple[list[NormalisedEvent], list[NormalisedEvent], list[NormalisedEvent], str]:
    """Split arbitrary event sections (themes/watchlist/etc.) against prior session snapshot."""
    prev_key, prev_label, _prev_sent = previous_session_context(
        profile_name=profile_name,
        session_key=session_key,
        local_date=local_date,
        timezone_name=timezone_name,
    )
    if not prev_key:
        return list(events), [], [], ""

    snapshot = get_session_snapshot(profile_name, local_date, prev_key)
    seen_ids = _seen_title_keys_from_snapshot(snapshot)
    new_items: list[NormalisedEvent] = []
    repeated: list[NormalisedEvent] = []
    carried: list[NormalisedEvent] = []
    for evt in events:
        tkey = f"title:{(evt.title or '').strip().lower()}"
        if tkey in seen_ids:
            repeated.append(evt)
            if int(evt.cluster_size or 1) >= 8:
                carried.append(evt)
            continue
        new_items.append(evt)
    return new_items, repeated, carried[:2], prev_label


def _previous_session_key(session_key: str) -> str | None:
    key = (session_key or "morning").strip().lower()
    mapping = {
        "europe_midday": "morning",
        "us_pre_open": "europe_midday",
        "us_intraday_risk": "us_pre_open",
        "into_close": "us_intraday_risk",
        "closing_wrap": "into_close",
    }
    return mapping.get(key)


def _seen_title_keys_from_snapshot(snapshot: dict | None) -> set[str]:
    seen_ids: set[str] = set()
    if not snapshot:
        return seen_ids
    for row in snapshot.get("portfolio_summary", []) or []:
        title = str((row or {}).get("title") or "").strip().lower()
        if title:
            seen_ids.add(f"title:{title}")
    text = str(snapshot.get("telegram_text") or "")
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        raw = raw.lstrip("-•").strip()
        if len(raw) < 16:
            continue
        seen_ids.add(f"title:{raw.lower()}")
    return seen_ids


def _as_local(dt: datetime, timezone_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return dt.astimezone(timezone.utc)
