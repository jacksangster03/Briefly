"""Persist LLM API call usage to SQLite for cost tracking."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from app.db.session import get_session
from app.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("llm.usage")


def log_llm_usage(
    *,
    profile_name: str,
    session_key: str | None,
    local_date: date | None,
    model: str,
    call_type: str,
    mode: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float | None,
) -> None:
    """Write one LLMUsageLog row. Never raises: failures are logged and swallowed."""
    from app.db.models import LLMUsageLog
    try:
        row = LLMUsageLog(
            profile_name=profile_name,
            called_at_utc=datetime.now(timezone.utc),
            local_date=local_date,
            session_key=session_key,
            model=model,
            call_type=call_type,
            mode=mode,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            total_tokens=max(0, prompt_tokens + completion_tokens),
            estimated_cost_usd=estimated_cost_usd,
        )
        with get_session() as db:
            db.add(row)
        logger.debug(
            "LLM usage logged: profile=%s session=%s model=%s tokens=%d cost=%s",
            profile_name,
            session_key,
            model,
            prompt_tokens + completion_tokens,
            f"${estimated_cost_usd:.6f}" if estimated_cost_usd is not None else "n/a",
        )
    except Exception as exc:
        logger.warning("Failed to persist LLM usage log: %s", exc)


def query_usage_summary(
    profile_name: str,
    *,
    days: int = 30,
) -> list[dict]:
    """Return per-day, per-model aggregates for the last `days` calendar days.

    Each dict has: date, model, calls, prompt_tokens, completion_tokens,
    total_tokens, estimated_cost_usd (sum, None if no rates were set).
    Ordered newest-first by date.
    """
    from datetime import timedelta
    from sqlalchemy import func
    from app.db.models import LLMUsageLog

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as db:
        rows = (
            db.query(
                LLMUsageLog.local_date,
                LLMUsageLog.model,
                func.count(LLMUsageLog.id).label("calls"),
                func.sum(LLMUsageLog.prompt_tokens).label("prompt_tokens"),
                func.sum(LLMUsageLog.completion_tokens).label("completion_tokens"),
                func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
                func.sum(LLMUsageLog.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .filter(
                LLMUsageLog.profile_name == profile_name,
                LLMUsageLog.called_at_utc >= cutoff_dt,
            )
            .group_by(LLMUsageLog.local_date, LLMUsageLog.model)
            .order_by(LLMUsageLog.local_date.desc())
            .all()
        )
    return [
        {
            "date": r.local_date.isoformat() if r.local_date else "",
            "model": r.model or "",
            "calls": int(r.calls or 0),
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
            "estimated_cost_usd": float(r.estimated_cost_usd) if r.estimated_cost_usd is not None else None,
        }
        for r in rows
    ]


def query_monthly_spend(
    profile_name: str,
    year: int,
    month: int,
) -> float | None:
    """Return total estimated USD spend for a profile in the given calendar month.

    Returns None when no rows have cost data (rates not configured), so callers
    can distinguish "zero spend" from "cost estimation disabled".
    """
    from sqlalchemy import func, extract
    from app.db.models import LLMUsageLog

    with get_session() as db:
        result = (
            db.query(func.sum(LLMUsageLog.estimated_cost_usd))
            .filter(
                LLMUsageLog.profile_name == profile_name,
                extract("year", LLMUsageLog.local_date) == year,
                extract("month", LLMUsageLog.local_date) == month,
                LLMUsageLog.estimated_cost_usd.isnot(None),
            )
            .scalar()
        )
    return float(result) if result is not None else None


def query_usage_rows(
    profile_name: str,
    *,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """Return individual LLM call rows, newest first."""
    from datetime import timedelta
    from app.db.models import LLMUsageLog

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as db:
        rows = (
            db.query(LLMUsageLog)
            .filter(
                LLMUsageLog.profile_name == profile_name,
                LLMUsageLog.called_at_utc >= cutoff_dt,
            )
            .order_by(LLMUsageLog.called_at_utc.desc())
            .limit(limit)
            .all()
        )
    return [
        {
            "id": r.id,
            "called_at_utc": r.called_at_utc.isoformat() if r.called_at_utc else "",
            "local_date": r.local_date.isoformat() if r.local_date else "",
            "session_key": r.session_key or "",
            "model": r.model or "",
            "call_type": r.call_type or "",
            "mode": r.mode or "",
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "estimated_cost_usd": r.estimated_cost_usd,
        }
        for r in rows
    ]
