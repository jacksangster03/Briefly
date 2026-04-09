"""Event clustering: group related events into narrative clusters.

Groups events by shared tickers, keywords, or themes within a time window.
Each cluster gets a single representative event for the briefing, with
a note about how many related items exist.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta

from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("clustering")


def cluster_events(
    events: list[NormalisedEvent],
    time_window_hours: int = 12,
) -> list[NormalisedEvent]:
    """Assign cluster IDs to related events and return representatives.

    Two events are in the same cluster if they share at least one ticker
    and their event types are compatible, within the time window.
    """
    if not events:
        return []

    # Build clusters
    clusters: dict[str, list[NormalisedEvent]] = {}
    assigned: set[str] = set()

    # Sort by published_at (newest first) so the best event is likely first
    sorted_events = sorted(
        events,
        key=lambda e: e.published_at or e.generated_at if hasattr(e, "generated_at") else e.published_at,
        reverse=True,
    )

    for evt in sorted_events:
        if evt.event_id in assigned:
            continue

        # Try to find an existing cluster this event belongs to
        matched_cluster = _find_matching_cluster(evt, clusters, time_window_hours)

        if matched_cluster:
            clusters[matched_cluster].append(evt)
            assigned.add(evt.event_id)
        else:
            # Start a new cluster
            cluster_id = str(uuid.uuid4())[:8]
            evt.cluster_id = cluster_id
            clusters[cluster_id] = [evt]
            assigned.add(evt.event_id)

    # Select representative from each cluster (highest final_score)
    representatives: list[NormalisedEvent] = []
    for cluster_id, cluster_events_list in clusters.items():
        cluster_events_list.sort(key=lambda e: e.final_score, reverse=True)
        rep = cluster_events_list[0]
        rep.cluster_id = cluster_id

        if len(cluster_events_list) > 1:
            rep.summary = (
                f"{rep.summary} "
                f"[+{len(cluster_events_list) - 1} related]"
            ).strip()

        representatives.append(rep)

    logger.info(
        "Clustered %d events into %d clusters",
        len(events), len(representatives),
    )
    return representatives


def _find_matching_cluster(
    evt: NormalisedEvent,
    clusters: dict[str, list[NormalisedEvent]],
    window_hours: int,
) -> str | None:
    """Find a cluster this event belongs to, or return None."""
    window = timedelta(hours=window_hours)
    evt_tickers = set(evt.tickers)

    if not evt_tickers:
        return None

    for cluster_id, members in clusters.items():
        rep = members[0]
        rep_tickers = set(rep.tickers)

        # Must share at least one ticker
        if not evt_tickers & rep_tickers:
            continue

        # Must be within time window
        if evt.published_at and rep.published_at:
            if abs(evt.published_at - rep.published_at) > window:
                continue

        # Compatible event types (don't cluster earnings with general news)
        if _compatible_types(evt.event_type, rep.event_type):
            return cluster_id

    return None


def _compatible_types(type_a: str, type_b: str) -> bool:
    """Check if two event types are similar enough to cluster."""
    if type_a == type_b:
        return True

    # Group compatible types
    news_types = {"market_news", "company_news", "headline", "news_search"}
    filing_types = {"filing", "current_report", "annual_report", "quarterly_report"}
    earnings_types = {"earnings", "guidance"}

    for group in [news_types, filing_types, earnings_types]:
        if type_a in group and type_b in group:
            return True

    return False
