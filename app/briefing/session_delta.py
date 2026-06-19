"""Deterministic session-to-session delta helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.briefing.session_metadata import label_for
from app.briefing.session_snapshot_service import get_session_snapshot
from app.db.models import SessionSendState
from app.db.session import get_session
from app.processing.cleaners import title_similarity
from app.schemas.events import NormalisedEvent

# Content-similarity thresholds for classifying an event against the previous
# session's already-sent titles. Pure title-string equality used to be the
# only signal (see docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.2): a
# reworded headline about the same story registered as fully "new", and a
# materially different update with an unchanged headline registered as fully
# "repeated". These thresholds let a reworded-but-same story be classified as
# an update (still surfaced, lower novelty) rather than either extreme.
_REPEATED_SIMILARITY_THRESHOLD = 0.82  # near-identical token overlap: same headline, reworded
_UPDATED_SIMILARITY_THRESHOLD = 0.35   # meaningful overlap: same story, materially different


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
    updated_news_items: list[NormalisedEvent] = field(default_factory=list)


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
    seen_titles = _seen_titles_from_snapshot(snapshot)
    new_items, updated_items, repeated_items, carried = _classify_against_seen(events, seen_titles)

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
        updated_news_items=updated_items,
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
    seen_titles = _seen_titles_from_snapshot(snapshot)
    new_items, updated_items, repeated, carried = _classify_against_seen(events, seen_titles)
    # This function's external contract is a 4-tuple (new/repeated/carried/label);
    # material updates are surfaced as "new" here rather than getting their own
    # bucket, since callers (theme/watchlist sections) only distinguish fresh
    # vs. repeated. Each event's novelty_score/update_status is still set
    # accurately by _classify_against_seen for any downstream ResearchEvent use.
    return new_items + updated_items, repeated, carried[:2], prev_label


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


def _seen_titles_from_snapshot(snapshot: dict | None) -> list[str]:
    """Extract candidate title strings from the previous session's snapshot
    for content-similarity comparison (see _classify_against_seen).
    """
    seen: list[str] = []
    if not snapshot:
        return seen
    for row in snapshot.get("portfolio_summary", []) or []:
        title = str((row or {}).get("title") or "").strip()
        if title:
            seen.append(title)
    text = str(snapshot.get("telegram_text") or "")
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        raw = raw.lstrip("-•").strip()
        if len(raw) < 16:
            continue
        seen.append(raw)
    return seen


_EventLists = tuple[
    list[NormalisedEvent], list[NormalisedEvent], list[NormalisedEvent], list[NormalisedEvent]
]


def _classify_against_seen(
    events: list[NormalisedEvent],
    seen_titles: list[str],
) -> _EventLists:
    """Classify events against the previous session's titles using content
    similarity rather than exact string equality, and set each event's
    novelty_score/update_status accordingly (mutates events in place).

    A reworded headline about the same story (high but non-exact overlap)
    is classified as a material update rather than either "fully new" or
    "fully repeated" -- the gap named in
    docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.2/17.4.

    Returns (new_items, material_update_items, repeated_items, carried_forward_items).
    """
    new_items: list[NormalisedEvent] = []
    updated_items: list[NormalisedEvent] = []
    repeated_items: list[NormalisedEvent] = []
    carried: list[NormalisedEvent] = []

    for evt in events:
        best_similarity = max(
            (title_similarity(evt.title or "", seen) for seen in seen_titles),
            default=0.0,
        )
        if best_similarity >= _REPEATED_SIMILARITY_THRESHOLD:
            evt.novelty_score = round(max(0.0, 1.0 - best_similarity), 3)
            evt.update_status = "repeated"
            repeated_items.append(evt)
            # Keep a small carried-forward context set if story is large.
            if int(evt.cluster_size or 1) >= 8:
                carried.append(evt)
            continue
        if best_similarity >= _UPDATED_SIMILARITY_THRESHOLD:
            evt.novelty_score = round(max(0.0, 1.0 - best_similarity), 3)
            evt.update_status = "material_update"
            updated_items.append(evt)
            continue
        evt.novelty_score = 1.0
        new_items.append(evt)

    return new_items, updated_items, repeated_items, carried


def _as_local(dt: datetime, timezone_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return dt.astimezone(timezone.utc)
