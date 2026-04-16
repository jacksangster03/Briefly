"""Persistence helpers for cadence markers and breaking storyline state."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_

from app.db.models import BreakingStoryState, CadenceMarker
from app.db.session import get_session


def has_cadence_marker(profile_name: str, marker_key: str) -> bool:
    with get_session() as session:
        row = (
            session.query(CadenceMarker)
            .filter(
                CadenceMarker.profile_name == profile_name,
                CadenceMarker.marker_key == marker_key,
            )
            .first()
        )
    return row is not None


def record_cadence_marker(
    *,
    profile_name: str,
    marker_key: str,
    action_type: str,
    local_date: date,
    local_timezone: str,
    sent_at_local: datetime,
) -> None:
    with get_session() as session:
        row = (
            session.query(CadenceMarker)
            .filter(
                CadenceMarker.profile_name == profile_name,
                CadenceMarker.marker_key == marker_key,
            )
            .first()
        )
        if row is None:
            row = CadenceMarker(
                profile_name=profile_name,
                marker_key=marker_key,
                action_type=action_type,
                local_date=local_date,
                local_timezone=local_timezone,
                sent_at_local=sent_at_local,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
        row.updated_at = datetime.now(timezone.utc)


def get_breaking_story_state(
    *,
    profile_name: str,
    storyline_key: str,
    local_date: date,
) -> BreakingStoryState | None:
    with get_session() as session:
        row = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.storyline_key == storyline_key,
                BreakingStoryState.local_date == local_date,
            )
            .first()
        )
    return row


def upsert_breaking_story_initial(
    *,
    profile_name: str,
    storyline_key: str,
    local_date: date,
    category: str,
    event_id: str,
    event_title: str,
    why_markets_care: str,
    watch_symbols: list[str],
    first_sent_at: datetime,
    followup_due_at: datetime,
    reason: str,
) -> BreakingStoryState:
    with get_session() as session:
        row = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.storyline_key == storyline_key,
                BreakingStoryState.local_date == local_date,
            )
            .first()
        )
        if row is None:
            row = BreakingStoryState(
                profile_name=profile_name,
                storyline_key=storyline_key,
                local_date=local_date,
                state="initial_sent",
                phase="initial",
                category=category,
                event_id=event_id,
                event_title=event_title,
                why_markets_care=why_markets_care,
                watch_symbols=watch_symbols,
                first_sent_at=first_sent_at.astimezone(timezone.utc),
                followup_due_at=followup_due_at.astimezone(timezone.utc),
                followup_attempts=0,
                last_reason=reason,
                closed=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
        else:
            row.state = "initial_sent"
            row.phase = "initial"
            row.category = category
            row.event_id = event_id
            row.event_title = event_title
            row.why_markets_care = why_markets_care
            row.watch_symbols = watch_symbols
            row.first_sent_at = first_sent_at.astimezone(timezone.utc)
            row.followup_due_at = followup_due_at.astimezone(timezone.utc)
            row.followup_attempts = 0
            row.last_reason = reason
            row.closed = False
            row.updated_at = datetime.now(timezone.utc)
    return row


def due_breaking_followups(
    *,
    profile_name: str,
    now_utc: datetime,
) -> list[BreakingStoryState]:
    with get_session() as session:
        rows = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.closed.is_(False),
                BreakingStoryState.state.in_(["initial_sent", "followup_due"]),
                BreakingStoryState.followup_due_at.isnot(None),
                BreakingStoryState.followup_due_at <= now_utc.astimezone(timezone.utc),
            )
            .all()
        )
    return rows


def mark_followup_sent(
    *,
    profile_name: str,
    storyline_key: str,
    local_date: date,
    sent_at: datetime,
    reason: str,
) -> None:
    with get_session() as session:
        row = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.storyline_key == storyline_key,
                BreakingStoryState.local_date == local_date,
            )
            .first()
        )
        if row is None:
            return
        row.state = "followup_sent"
        row.phase = "followup"
        row.followup_sent_at = sent_at.astimezone(timezone.utc)
        row.followup_attempts = int(row.followup_attempts or 0) + 1
        row.last_reason = reason
        row.closed = True
        row.updated_at = datetime.now(timezone.utc)


def close_breaking_story(
    *,
    profile_name: str,
    storyline_key: str,
    local_date: date,
    reason: str,
) -> None:
    with get_session() as session:
        row = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.storyline_key == storyline_key,
                BreakingStoryState.local_date == local_date,
            )
            .first()
        )
        if row is None:
            return
        row.state = "closed"
        row.closed = True
        row.followup_attempts = int(row.followup_attempts or 0) + 1
        row.last_reason = reason
        row.updated_at = datetime.now(timezone.utc)


def breaking_sends_last_hour(*, profile_name: str, now_utc: datetime) -> int:
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(hours=1)
    with get_session() as session:
        rows = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                or_(
                    and_(BreakingStoryState.first_sent_at.isnot(None), BreakingStoryState.first_sent_at >= cutoff),
                    and_(BreakingStoryState.followup_sent_at.isnot(None), BreakingStoryState.followup_sent_at >= cutoff),
                ),
            )
            .all()
        )
    count = 0
    for row in rows:
        if row.first_sent_at and row.first_sent_at >= cutoff:
            count += 1
        if row.followup_sent_at and row.followup_sent_at >= cutoff:
            count += 1
    return int(count)


def recent_storyline_send_exists(
    *,
    profile_name: str,
    storyline_key: str,
    now_utc: datetime,
    cooldown_minutes: int,
) -> bool:
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(minutes=cooldown_minutes)
    with get_session() as session:
        row = (
            session.query(BreakingStoryState)
            .filter(
                BreakingStoryState.profile_name == profile_name,
                BreakingStoryState.storyline_key == storyline_key,
                or_(
                    and_(BreakingStoryState.first_sent_at.isnot(None), BreakingStoryState.first_sent_at >= cutoff),
                    and_(BreakingStoryState.followup_sent_at.isnot(None), BreakingStoryState.followup_sent_at >= cutoff),
                ),
            )
            .first()
        )
    return row is not None
