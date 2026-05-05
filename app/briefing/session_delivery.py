"""Session-scoped delivery idempotency helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import os

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.db.models import SessionSendState
from app.db.session import get_session
from app.logger import get_logger

logger = get_logger("session_delivery")

_IN_PROGRESS_STALE_AFTER = timedelta(minutes=30)


def canonical_session_message_key(session_key: str) -> str:
    normalized = (session_key or "morning").strip().lower() or "morning"
    return f"session_brief:{normalized}"


def normalize_replay_namespace(replay_namespace: str | None) -> str:
    return (replay_namespace or "").strip()


def build_session_idempotency_key(
    *,
    profile_name: str,
    channel: str,
    session_key: str,
    local_date: date,
    replay_namespace: str | None = None,
) -> str:
    normalized_session = (session_key or "morning").strip().lower() or "morning"
    normalized_channel = (channel or "").strip().lower()
    normalized_profile = (profile_name or "default_user").strip() or "default_user"
    normalized_namespace = normalize_replay_namespace(replay_namespace) or "live"
    return (
        f"{normalized_profile}:{normalized_channel}:"
        f"{canonical_session_message_key(normalized_session)}:{local_date.isoformat()}:{normalized_namespace}"
    )


@dataclass(frozen=True)
class SessionDeliveryContext:
    profile_name: str
    session_key: str
    local_date: date
    command_source: str = "cli"
    replay_namespace: str | None = None
    force_send: bool = False

    @property
    def normalized_replay_namespace(self) -> str:
        return normalize_replay_namespace(self.replay_namespace)


@dataclass(frozen=True)
class SessionSendClaim:
    acquired: bool
    existing_success: bool
    idempotency_key: str
    process_id: int
    state_id: int | None = None


def _is_stale(state: SessionSendState, *, now_utc: datetime) -> bool:
    updated_at = state.updated_at or state.created_at
    if not updated_at:
        return False
    return updated_at <= now_utc - _IN_PROGRESS_STALE_AFTER


def claim_session_send(*, channel: str, context: SessionDeliveryContext) -> SessionSendClaim:
    normalized_channel = (channel or "").strip().lower()
    normalized_session = (context.session_key or "morning").strip().lower() or "morning"
    normalized_profile = (context.profile_name or "default_user").strip() or "default_user"
    replay_namespace = context.normalized_replay_namespace
    idempotency_key = build_session_idempotency_key(
        profile_name=normalized_profile,
        channel=normalized_channel,
        session_key=normalized_session,
        local_date=context.local_date,
        replay_namespace=replay_namespace,
    )
    process_id = os.getpid()
    now_utc = datetime.now(timezone.utc)
    scope = {
        "profile_name": normalized_profile,
        "channel": normalized_channel,
        "session_key": normalized_session,
        "local_date": context.local_date,
        "replay_namespace": replay_namespace,
    }

    with get_session() as session:
        existing = session.query(SessionSendState).filter_by(**scope).one_or_none()
        if existing and existing.success and context.force_send:
            session.delete(existing)
            session.flush()
            existing = None
            logger.info(
                "Session delivery force-resend: cleared prior success | idempotency_key=%s process_id=%s",
                idempotency_key,
                process_id,
            )
        if existing and existing.success:
            logger.info(
                "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=true "
                "process_id=%s command_source=%s",
                idempotency_key,
                process_id,
                context.command_source,
            )
            return SessionSendClaim(
                acquired=False,
                existing_success=True,
                idempotency_key=idempotency_key,
                process_id=process_id,
                state_id=existing.id,
            )

        if existing is None:
            row = SessionSendState(
                profile_name=normalized_profile,
                channel=normalized_channel,
                session_key=normalized_session,
                local_date=context.local_date,
                replay_namespace=replay_namespace,
                message_type=canonical_session_message_key(normalized_session),
                idempotency_key=idempotency_key,
                in_progress=True,
                success=False,
                command_source=context.command_source,
                process_id=process_id,
                created_at=now_utc,
                updated_at=now_utc,
                error_message=None,
            )
            session.add(row)
            try:
                session.flush()
                logger.info(
                    "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=false "
                    "process_id=%s command_source=%s",
                    idempotency_key,
                    process_id,
                    context.command_source,
                )
                return SessionSendClaim(
                    acquired=True,
                    existing_success=False,
                    idempotency_key=idempotency_key,
                    process_id=process_id,
                    state_id=row.id,
                )
            except IntegrityError:
                session.rollback()

        existing = session.query(SessionSendState).filter_by(**scope).one_or_none()
        if existing and existing.success:
            logger.info(
                "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=true "
                "process_id=%s command_source=%s",
                idempotency_key,
                process_id,
                context.command_source,
            )
            return SessionSendClaim(
                acquired=False,
                existing_success=True,
                idempotency_key=idempotency_key,
                process_id=process_id,
                state_id=existing.id if existing else None,
            )

        if existing and existing.in_progress and not _is_stale(existing, now_utc=now_utc):
            logger.info(
                "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=false "
                "process_id=%s command_source=%s",
                idempotency_key,
                process_id,
                context.command_source,
            )
            return SessionSendClaim(
                acquired=False,
                existing_success=False,
                idempotency_key=idempotency_key,
                process_id=process_id,
                state_id=existing.id,
            )

        if existing is None:
            logger.info(
                "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=false "
                "process_id=%s command_source=%s",
                idempotency_key,
                process_id,
                context.command_source,
            )
            return SessionSendClaim(
                acquired=False,
                existing_success=False,
                idempotency_key=idempotency_key,
                process_id=process_id,
                state_id=None,
            )

        updated = (
            session.query(SessionSendState)
            .filter(
                SessionSendState.id == existing.id,
                SessionSendState.success.is_(False),
                or_(
                    SessionSendState.in_progress.is_(False),
                    SessionSendState.updated_at <= now_utc - _IN_PROGRESS_STALE_AFTER,
                ),
            )
            .update(
                {
                    SessionSendState.in_progress: True,
                    SessionSendState.command_source: context.command_source,
                    SessionSendState.process_id: process_id,
                    SessionSendState.updated_at: now_utc,
                    SessionSendState.error_message: None,
                },
                synchronize_session=False,
            )
        )
        session.flush()
        logger.info(
            "Session delivery idempotency check | idempotency_key=%s existing_sent_message_found=false "
            "process_id=%s command_source=%s",
            idempotency_key,
            process_id,
            context.command_source,
        )
        return SessionSendClaim(
            acquired=bool(updated),
            existing_success=False,
            idempotency_key=idempotency_key,
            process_id=process_id,
            state_id=existing.id,
        )


def finalize_session_send_claim(
    *,
    claim: SessionSendClaim,
    success: bool,
    error_message: str | None = None,
) -> None:
    if claim.state_id is None:
        return
    now_utc = datetime.now(timezone.utc)
    with get_session() as session:
        state = session.get(SessionSendState, claim.state_id)
        if state is None:
            return
        state.success = bool(success)
        state.in_progress = False
        state.updated_at = now_utc
        state.sent_at = now_utc if success else None
        state.process_id = claim.process_id
        state.error_message = (error_message or "")[:500] or None
