"""Dataset and label utilities for local-first ML news classifier development."""

from __future__ import annotations

from datetime import date, datetime, timezone
import csv
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db.models import NewsClassifierLabel
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("ml.news_dataset")


def build_news_label_row(
    event: NormalisedEvent,
    *,
    session_key: str | None = None,
    local_date: date | None = None,
    included_in_briefing: bool | None = None,
    sent_as_breaking: bool | None = None,
) -> dict:
    raw = event.raw_data or {}
    published = _to_utc(event.published_at)
    first_seen = _to_utc(_parse_dt(raw.get("first_seen_at")))
    return {
        "event_id": str(event.event_id or ""),
        "headline": str(event.title or ""),
        "summary": str(event.summary or ""),
        "source": str(event.source or ""),
        "domain": _domain_for(event.url),
        "published_at": published,
        "first_seen_at": first_seen,
        "tickers_json": list(event.tickers or []),
        "sectors_json": list(event.sectors or []),
        "deterministic_story_type": _s(raw.get("news_story_type")),
        "deterministic_suppression_reason": _s(raw.get("news_suppress_reason")),
        "deterministic_breaking_eligible": _b(raw.get("news_breaking_eligible")),
        "deterministic_freshness_state": _s(raw.get("news_freshness_state")),
        "deterministic_update_status": _s(event.update_status),
        "deterministic_score": _f(event.final_score),
        "included_in_briefing": included_in_briefing,
        "sent_as_breaking": sent_as_breaking,
        "session_key": session_key or "",
        "local_date": local_date,
        "label_source": "deterministic",
    }


def upsert_news_label_rows(db: Session, rows: list[dict]) -> int:
    """Upsert by (event_id, session_key, local_date). Returns number upserted."""
    if not rows:
        return 0
    count = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        event_id = str(row.get("event_id") or "")
        session_key = str(row.get("session_key") or "")
        local_date = row.get("local_date")
        existing = (
            db.query(NewsClassifierLabel)
            .filter(
                NewsClassifierLabel.event_id == event_id,
                NewsClassifierLabel.session_key == session_key,
                NewsClassifierLabel.local_date == local_date,
            )
            .order_by(NewsClassifierLabel.id.desc())
            .first()
        )
        if existing is None:
            existing = NewsClassifierLabel(**row)
            db.add(existing)
        else:
            for key, value in row.items():
                if value is not None:
                    setattr(existing, key, value)
        existing.updated_at = now
        count += 1
    return count


def export_news_labels(
    db: Session,
    output_path: str | Path,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
    format: str = "csv",
    dedupe_headlines: bool = False,
) -> Path:
    fmt = (format or "csv").strip().lower()
    if fmt != "csv":
        raise ValueError("Only csv export is supported in Phase 1.")
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    q = db.query(NewsClassifierLabel)
    if from_date is not None:
        q = q.filter(NewsClassifierLabel.local_date >= from_date)
    if to_date is not None:
        q = q.filter(NewsClassifierLabel.local_date <= to_date)
    rows = q.order_by(NewsClassifierLabel.local_date.desc(), NewsClassifierLabel.id.desc()).all()
    if dedupe_headlines:
        rows = dedupe_label_rows(rows)
    fields = [
        "id",
        "event_id",
        "stable_story_key",
        "headline",
        "summary",
        "source",
        "domain",
        "published_at",
        "first_seen_at",
        "tickers_json",
        "sectors_json",
        "deterministic_story_type",
        "deterministic_suppression_reason",
        "deterministic_breaking_eligible",
        "deterministic_freshness_state",
        "deterministic_update_status",
        "deterministic_score",
        "included_in_briefing",
        "sent_as_breaking",
        "session_key",
        "local_date",
        "sessions_seen",
        "session_keys_agg",
        "local_dates_agg",
        "manual_story_type",
        "manual_suppression_reason",
        "manual_breaking_eligible",
        "manual_ticker_mismatch_risk",
        "manual_stale_reprint_risk",
        "label_source",
        "notes",
        "created_at",
        "updated_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            base = {field: getattr(r, field, None) for field in fields}
            base["stable_story_key"] = stable_story_key_for_row(r)
            base.setdefault("sessions_seen", 1)
            base.setdefault("session_keys_agg", getattr(r, "session_key", "") or "")
            ld = getattr(r, "local_date", None)
            base.setdefault("local_dates_agg", ld.isoformat() if ld else "")
            writer.writerow(base)
    return path


def load_labeled_examples(
    db: Session,
    *,
    label_source: str | None = None,
    limit: int | None = None,
) -> list[NewsClassifierLabel]:
    q = db.query(NewsClassifierLabel)
    if label_source:
        q = q.filter(NewsClassifierLabel.label_source == label_source)
    q = q.order_by(NewsClassifierLabel.updated_at.desc(), NewsClassifierLabel.id.desc())
    if limit:
        q = q.limit(max(1, int(limit)))
    return list(q.all())


def stable_story_key_for_row(row: NewsClassifierLabel) -> str:
    domain = _s(getattr(row, "domain", "")).lower()
    headline = _normalize_headline(_s(getattr(row, "headline", "")))
    if not headline:
        event_id = _s(getattr(row, "event_id", ""))
        if event_id:
            return f"event:{event_id}"
    digest = hashlib.sha256(f"{domain}|{headline}".encode("utf-8")).hexdigest()[:16]
    return f"headline:{digest}"


def dedupe_label_rows(rows: list[NewsClassifierLabel]) -> list[NewsClassifierLabel]:
    """Collapse repeated story/headline rows into one most-recent representative row."""
    if not rows:
        return []
    grouped: dict[str, list[NewsClassifierLabel]] = {}
    for row in rows:
        key = stable_story_key_for_row(row)
        grouped.setdefault(key, []).append(row)
    collapsed: list[NewsClassifierLabel] = []
    for key, bucket in grouped.items():
        bucket_sorted = sorted(
            bucket,
            key=lambda r: (
                getattr(r, "updated_at", None) or getattr(r, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
                getattr(r, "id", 0),
            ),
            reverse=True,
        )
        winner = bucket_sorted[0]
        sessions = sorted({str(getattr(r, "session_key", "") or "") for r in bucket if getattr(r, "session_key", None)})
        dates = sorted({(getattr(r, "local_date", None).isoformat() if getattr(r, "local_date", None) else "") for r in bucket if getattr(r, "local_date", None)})
        setattr(winner, "stable_story_key", key)
        setattr(winner, "sessions_seen", len(bucket))
        setattr(winner, "session_keys_agg", ",".join(sessions))
        setattr(winner, "local_dates_agg", ",".join(dates))
        collapsed.append(winner)
    collapsed.sort(
        key=lambda r: (
            getattr(r, "updated_at", None) or getattr(r, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            getattr(r, "id", 0),
        ),
        reverse=True,
    )
    return collapsed


def _domain_for(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _s(value) -> str:
    return str(value or "").strip()


def _f(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _b(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _normalize_headline(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s
