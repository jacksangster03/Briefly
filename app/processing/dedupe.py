"""Event deduplication and sent-history classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import NormalisedEvent as StoredEvent
from app.db.models import SentMessage
from app.db.session import get_session
from app.logger import get_logger
from app.processing.cleaners import normalise_for_comparison
from app.schemas.events import NormalisedEvent

logger = get_logger("dedupe")


def deduplicate_events(
    events: list[NormalisedEvent],
    time_window_hours: int = 12,
    similarity_threshold: float = 0.85,
    *,
    apply_ticker_clustering: bool = True,
    mark_sent_history: bool = True,
) -> list[NormalisedEvent]:
    """Remove duplicate and near-duplicate events."""
    if not events:
        return []

    original_count = len(events)

    for evt in events:
        if not evt.content_hash:
            evt.compute_hash()

    seen_hashes: set[str] = set()
    stage1: list[NormalisedEvent] = []
    for evt in events:
        if evt.content_hash not in seen_hashes:
            seen_hashes.add(evt.content_hash)
            stage1.append(evt)

    seen_normalised: set[str] = set()
    stage2: list[NormalisedEvent] = []
    for evt in stage1:
        norm = normalise_for_comparison(evt.title)
        if norm and norm not in seen_normalised:
            seen_normalised.add(norm)
            stage2.append(evt)
        elif not norm:
            stage2.append(evt)

    stage3 = (
        _cluster_by_ticker_time(stage2, time_window_hours, similarity_threshold)
        if apply_ticker_clustering
        else stage2
    )

    if mark_sent_history:
        stage4 = classify_against_sent_history(stage3)
        final = [e for e in stage4 if not e.already_sent]
    else:
        final = stage3

    logger.info(
        "Dedup: %d -> hash:%d -> headline:%d -> story:%d -> unsent:%d",
        original_count, len(stage1), len(stage2), len(stage3), len(final),
    )
    return final


def classify_against_sent_history(
    events: list[NormalisedEvent],
    lookback_hours: int = 24,
) -> list[NormalisedEvent]:
    """Classify events as new, material_update, or duplicate."""
    if not events:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    try:
        with get_session() as session:
            recent_sent_events = (
                session.query(StoredEvent)
                .filter(StoredEvent.already_sent.is_(True), StoredEvent.created_at >= cutoff)
                .all()
            )
            recent_sent_messages = (
                session.query(SentMessage)
                .filter(SentMessage.sent_at >= cutoff, SentMessage.success.is_(True))
                .all()
            )
    except Exception:
        logger.debug("Could not read sent history; treating all events as new", exc_info=True)
        return events

    sent_hashes = {
        msg.content_hash for msg in recent_sent_messages if msg.content_hash
    } | {
        row.content_hash for row in recent_sent_events if row.content_hash
    }

    recent_by_cluster: dict[str, list[StoredEvent]] = {}
    for row in recent_sent_events:
        if row.cluster_id:
            recent_by_cluster.setdefault(row.cluster_id, []).append(row)

    for evt in events:
        evt.update_status = "new"
        evt.reason_code = evt.reason_code or "new_catalyst"

        if evt.content_hash in sent_hashes:
            evt.already_sent = True
            evt.update_status = "duplicate"
            evt.reason_code = "already_sent_exact"
            evt.novelty_score = 0.05
            continue

        matches = recent_by_cluster.get(evt.cluster_id or "", [])
        if not matches:
            matches = [
                row for row in recent_sent_events
                if _same_story(evt, row)
            ]

        if not matches:
            evt.novelty_score = max(evt.novelty_score, 1.0)
            continue

        newest = max(matches, key=lambda row: row.created_at or cutoff)
        if _is_material_update(evt, newest):
            evt.update_status = "material_update"
            evt.reason_code = "material_update"
            evt.novelty_score = max(evt.novelty_score, 0.55)
            evt.already_sent = False
        else:
            evt.already_sent = True
            evt.update_status = "duplicate"
            evt.reason_code = "continuation_suppressed"
            evt.novelty_score = 0.15

    return events


def _cluster_by_ticker_time(
    events: list[NormalisedEvent],
    window_hours: int,
    similarity_threshold: float = 0.85,
) -> list[NormalisedEvent]:
    """Cluster events about the same ticker within a time window."""
    window = timedelta(hours=window_hours)
    clusters: dict[str, NormalisedEvent] = {}
    no_ticker: list[NormalisedEvent] = []

    for evt in events:
        if not evt.tickers:
            no_ticker.append(evt)
            continue

        for ticker in evt.tickers:
            key = f"{ticker}|{evt.event_type}"
            existing = clusters.get(key)

            if existing is None:
                clusters[key] = evt
                continue

            if evt.published_at and existing.published_at:
                delta = abs(evt.published_at - existing.published_at)
                same_story = (
                    _title_similarity(evt.title, existing.title) >= similarity_threshold
                    or evt.event_type == existing.event_type
                )
                if delta <= window and same_story:
                    if evt.final_score > existing.final_score:
                        clusters[key] = evt
                    continue

            alt_key = f"{key}|{evt.event_id[:8]}"
            clusters[alt_key] = evt

    return list(clusters.values()) + no_ticker


def _same_story(evt: NormalisedEvent, row: StoredEvent) -> bool:
    row_tickers = set(row.tickers or [])
    evt_tickers = set(evt.tickers or [])
    if evt.cluster_id and row.cluster_id and evt.cluster_id == row.cluster_id:
        return True
    if evt_tickers and row_tickers and not (evt_tickers & row_tickers):
        return False
    if evt.event_type and row.event_type and evt.event_type != row.event_type:
        return False
    return _title_similarity(evt.title, row.title or "") >= 0.55


def _is_material_update(evt: NormalisedEvent, row: StoredEvent) -> bool:
    similarity = _title_similarity(evt.title, row.title or "")
    return similarity < 0.82


def _title_similarity(a: str, b: str) -> float:
    tokens_a = set(normalise_for_comparison(a).split())
    tokens_b = set(normalise_for_comparison(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
