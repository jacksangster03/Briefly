"""Event deduplication engine.

Implements multiple dedup strategies:
1. Exact hash match (content_hash)
2. Normalised headline match
3. Ticker + time window clustering
4. Already-sent check against DB history
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
) -> list[NormalisedEvent]:
    """Remove duplicate and near-duplicate events.

    Returns a deduplicated list, preserving the first occurrence.
    """
    if not events:
        return []

    original_count = len(events)

    # Ensure all events have content hashes
    for evt in events:
        if not evt.content_hash:
            evt.compute_hash()

    # Stage 1: exact hash dedup
    seen_hashes: set[str] = set()
    stage1: list[NormalisedEvent] = []
    for evt in events:
        if evt.content_hash not in seen_hashes:
            seen_hashes.add(evt.content_hash)
            stage1.append(evt)

    # Stage 2: normalised headline dedup
    seen_normalised: set[str] = set()
    stage2: list[NormalisedEvent] = []
    for evt in stage1:
        norm = normalise_for_comparison(evt.title)
        if norm and norm not in seen_normalised:
            seen_normalised.add(norm)
            stage2.append(evt)
        elif not norm:
            stage2.append(evt)

    # Stage 3: ticker + time window clustering
    stage3 = _cluster_by_ticker_time(stage2, time_window_hours)

    # Stage 4: mark already-sent events
    stage4 = _mark_already_sent(stage3)

    final = [e for e in stage4 if not e.already_sent]

    logger.info(
        "Dedup: %d -> hash:%d -> headline:%d -> cluster:%d -> unsent:%d",
        original_count, len(stage1), len(stage2), len(stage3), len(final),
    )
    return final


def _cluster_by_ticker_time(
    events: list[NormalisedEvent],
    window_hours: int,
) -> list[NormalisedEvent]:
    """Cluster events about the same ticker within a time window.

    Keeps only the highest-scoring event per (ticker, event_type) cluster.
    Events without tickers pass through unfiltered.
    """
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

            # Check time proximity
            if evt.published_at and existing.published_at:
                delta = abs(evt.published_at - existing.published_at)
                if delta <= window:
                    # Keep the one with higher final_score, or the newer one
                    if evt.final_score > existing.final_score:
                        clusters[key] = evt
                    continue

            # Outside time window: this is a separate event, keep both
            # Use a modified key to avoid overwriting
            alt_key = f"{key}|{evt.event_id[:8]}"
            clusters[alt_key] = evt

    return list(clusters.values()) + no_ticker


def _mark_already_sent(events: list[NormalisedEvent]) -> list[NormalisedEvent]:
    """Check which events have already been sent to the user (last 24h)."""
    if not events:
        return events

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    try:
        with get_session() as session:
            recent_sent = (
                session.query(SentMessage)
                .filter(SentMessage.sent_at >= cutoff, SentMessage.success.is_(True))
                .all()
            )

            sent_hashes: set[str] = set()
            for msg in recent_sent:
                if msg.content_hash:
                    sent_hashes.add(msg.content_hash)

            for evt in events:
                if evt.content_hash in sent_hashes:
                    evt.already_sent = True
                    evt.novelty_score = 0.1

    except Exception:
        logger.debug("Could not check sent history; treating all as new", exc_info=True)

    return events
