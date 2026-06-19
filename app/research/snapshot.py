"""Persist and load compact ResearchEvent session snapshots for diff-based novelty."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from app.schemas.research_event import ResearchEvent

logger = logging.getLogger("research.snapshot")

_DEFAULT_CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "research_snapshots"

_PREVIOUS_SESSION_MAP: dict[str, str] = {
    "europe_midday": "morning",
    "us_pre_open": "europe_midday",
    "us_intraday_risk": "us_pre_open",
    "into_close": "us_intraday_risk",
    "closing_wrap": "into_close",
}


def _snapshot_path(root: Path, profile_name: str, local_date: date, session_key: str) -> Path:
    return root / profile_name / str(local_date) / f"{session_key}.json"


def persist_research_snapshot(
    events: list[ResearchEvent],
    *,
    profile_name: str,
    local_date: date,
    session_key: str,
    cache_root: Path | None = None,
) -> None:
    """Persist a compact snapshot of surfaced research events. Never raises."""
    root = cache_root or _DEFAULT_CACHE_ROOT
    path = _snapshot_path(root, profile_name, local_date, session_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        compact = [
            {
                "source_event_id": ev.source_event_id,
                "event_hash": ev.compute_hash(),
                "title": ev.title[:120],
                "material_update": ev.material_update,
                "price_confirmation_status": ev.price_confirmation_status,
                "source_tier": ev.source_tier,
                "portfolio_relevance_score": round(float(ev.portfolio_relevance_score or 0.0), 4),
                "confidence_label": ev.confidence_label,
                "causal_channel": ev.causal_channel,
            }
            for ev in events
        ]
        path.write_text(
            json.dumps({"events": compact, "saved_at": datetime.utcnow().isoformat()}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Research snapshot persist failed profile=%s session=%s", profile_name, session_key, exc_info=True)


def load_research_snapshot(
    *,
    profile_name: str,
    local_date: date,
    session_key: str,
    cache_root: Path | None = None,
) -> list[dict]:
    """Load compact research event records from a previous session. Returns [] on miss."""
    root = cache_root or _DEFAULT_CACHE_ROOT
    path = _snapshot_path(root, profile_name, local_date, session_key)
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("events") or [])
    except Exception:
        logger.debug("Research snapshot load failed profile=%s session=%s", profile_name, session_key, exc_info=True)
        return []


def load_previous_research_snapshot(
    *,
    profile_name: str,
    local_date: date,
    session_key: str,
    cache_root: Path | None = None,
) -> list[dict]:
    """Load the previous comparable session's research snapshot, or [] if none exists."""
    prev_key = _PREVIOUS_SESSION_MAP.get((session_key or "morning").lower())
    if not prev_key:
        return []
    return load_research_snapshot(
        profile_name=profile_name,
        local_date=local_date,
        session_key=prev_key,
        cache_root=cache_root,
    )


def compact_dicts_to_stub_events(records: list[dict]) -> list[ResearchEvent]:
    """Convert compact snapshot dicts back to minimal ResearchEvent stubs for diff comparison.

    Only the fields used by diff_research_events() are populated.
    """
    from app.schemas.research_event import ResearchEvent
    stubs: list[ResearchEvent] = []
    for rec in records:
        ev = ResearchEvent(
            source_event_id=str(rec.get("source_event_id") or ""),
            title=str(rec.get("title") or ""),
            material_update=bool(rec.get("material_update")),
            price_confirmation_status=rec.get("price_confirmation_status") or "unavailable",
            source_tier=str(rec.get("source_tier") or ""),
            portfolio_relevance_score=float(rec.get("portfolio_relevance_score") or 0.0),
            confidence_label=rec.get("confidence_label") or "none",
            causal_channel=rec.get("causal_channel") or "other",
        )
        stubs.append(ev)
    return stubs
