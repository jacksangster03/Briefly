"""Event clustering: group related events into narrative clusters.

Clusters are deterministic so the same catalyst can be recognised across
multiple runs. That lets us suppress noisy repeats while still allowing
material follow-ups to flow through as updates.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import timedelta

from app.logger import get_logger
from app.processing.cleaners import normalise_for_comparison
from app.schemas.events import NormalisedEvent

logger = get_logger("clustering")

STOPWORDS = {
    "after", "analyst", "announces", "another", "becomes", "could", "from",
    "heres", "just", "market", "markets", "more", "says", "say", "stock",
    "stocks", "their", "these", "this", "today", "update", "what", "why",
    "with", "would", "worth", "reuters", "sources", "report", "according",
}

# Macro threads: events sharing one of these keyword groups cluster together
# even without shared tickers. Each group defines a single macro narrative.
# Keep groups tight: a keyword should only ever belong to one thread, or
# clustering will pull unrelated stories into the same bucket.
MACRO_THREAD_GROUPS = [
    {"iran", "tehran", "hormuz"},
    {"israel", "gaza", "hamas", "hezbollah", "netanyahu", "lebanon", "beirut"},
    {"tariff", "trade war", "trade deal", "trade deficit"},
    # Bare "fed" is too noisy (matches "fed up", "fed into", etc.) — rely on
    # unambiguous Fed terms instead.
    {"fomc", "powell", "federal reserve", "rate cut", "rate hike", "interest rate"},
    {"ecb", "lagarde", "european central bank"},
    {"boj", "ueda", "bank of japan"},
    {"bank of england"},
    {"opec", "oil output", "oil production", "crude oil", "oil price"},
    {"ukraine", "kyiv", "moscow", "kremlin", "putin", "zelensky"},
    {"china", "beijing", "xi jinping"},
    {"nonfarm", "payrolls", "jobless claims", "unemployment rate"},
    {"cpi", "ppi", "pce", "core inflation", "inflation report"},
]


def cluster_events(
    events: list[NormalisedEvent],
    time_window_hours: int = 12,
) -> list[NormalisedEvent]:
    """Assign deterministic cluster IDs and return cluster representatives."""
    if not events:
        return []

    clusters: dict[str, list[NormalisedEvent]] = defaultdict(list)
    sorted_events = sorted(
        events,
        key=lambda e: (
            e.final_score,
            e.published_at.isoformat() if e.published_at else "",
        ),
        reverse=True,
    )

    for evt in sorted_events:
        matched_cluster = _find_matching_cluster(evt, clusters, time_window_hours)
        if matched_cluster:
            evt.cluster_id = matched_cluster
            clusters[matched_cluster].append(evt)
            continue

        cluster_id = build_story_key(evt)
        evt.cluster_id = cluster_id
        clusters[cluster_id].append(evt)

    representatives: list[NormalisedEvent] = []
    for cluster_id, cluster_members in clusters.items():
        cluster_members.sort(
            key=lambda e: (
                e.final_score,
                e.published_at.isoformat() if e.published_at else "",
            ),
            reverse=True,
        )
        rep = cluster_members[0]
        rep.cluster_id = cluster_id
        rep.cluster_size = len(cluster_members)
        # Preserve original summary before we append cluster metadata
        rep.raw_data["summary_original"] = rep.summary
        rep.raw_data["cluster_titles"] = [member.title for member in cluster_members[:5]]
        rep.raw_data["cluster_sources"] = sorted({member.source for member in cluster_members})
        rep.raw_data["cluster_member_hashes"] = [
            member.content_hash for member in cluster_members if member.content_hash
        ]

        if len(cluster_members) > 1:
            related_count = len(cluster_members) - 1
            rep.summary = (
                f"{rep.summary} [+{related_count} related]"
            ).strip()

        representatives.append(rep)

    representatives.sort(key=lambda e: e.final_score, reverse=True)
    logger.info("Clustered %d events into %d story clusters", len(events), len(representatives))
    return representatives


def build_story_key(evt: NormalisedEvent) -> str:
    """Build a stable story key from tickers, event family, and topic terms.

    For tickerless events that match a macro thread group, the group index
    becomes the anchor so all Iran-thread events share a key root.
    """
    family = _event_family(evt.event_type)
    tickers = ",".join(sorted(evt.tickers)[:3])

    if not tickers:
        text = f"{evt.title} {evt.summary}".lower()
        macro_groups = _matching_macro_groups(text)
        if macro_groups:
            tickers = f"macro_thread_{min(macro_groups)}"
        else:
            tickers = "macro"

    topic = " ".join(_topic_terms(evt.title, evt.summary))
    raw = f"{family}|{tickers}|{topic}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _find_matching_cluster(
    evt: NormalisedEvent,
    clusters: dict[str, list[NormalisedEvent]],
    window_hours: int,
) -> str | None:
    window = timedelta(hours=window_hours)
    evt_tickers = set(evt.tickers)
    evt_terms = set(_topic_terms(evt.title, evt.summary))
    evt_text = f"{evt.title} {evt.summary}".lower()
    evt_macro_groups = _matching_macro_groups(evt_text)

    for cluster_id, members in clusters.items():
        rep = members[0]
        rep_tickers = set(rep.tickers)
        rep_terms = set(_topic_terms(rep.title, rep.summary))

        if evt.published_at and rep.published_at:
            if abs(evt.published_at - rep.published_at) > window:
                continue

        if not _compatible_types(evt.event_type, rep.event_type):
            continue

        # Shared tickers: strong signal
        if evt_tickers and rep_tickers and evt_tickers & rep_tickers:
            return cluster_id

        # Macro thread grouping: for tickerless events, this is the primary
        # clustering signal (e.g. all Iran-related, all tariff-related).
        # Checked before topic terms to prevent coincidental word overlap
        # from pulling macro events into the wrong cluster.
        if not evt_tickers and evt_macro_groups:
            rep_text = f"{rep.title} {rep.summary}".lower()
            rep_macro_groups = _matching_macro_groups(rep_text)
            if evt_macro_groups & rep_macro_groups:
                return cluster_id

        # Shared topic terms: moderate signal
        if evt_terms and rep_terms and len(evt_terms & rep_terms) >= 2:
            return cluster_id

    return None


def _matching_macro_groups(text: str) -> set[int]:
    """Return indices of MACRO_THREAD_GROUPS that match the given text.

    Uses word-boundary matching so "fed up" doesn't trigger the Fed thread,
    "putin" requires its own word, and "opec" doesn't match inside
    "nopecorn" etc.
    """
    matches = set()
    for i, group in enumerate(MACRO_THREAD_GROUPS):
        for keyword in group:
            if _keyword_hit(keyword, text):
                matches.add(i)
                break
    return matches


def _keyword_hit(keyword: str, text: str) -> bool:
    """Match keyword against text with whole-word semantics."""
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _event_family(event_type: str) -> str:
    event_type = (event_type or "headline").lower()
    news_types = {"market_news", "company_news", "headline", "news_search"}
    filing_types = {"filing", "current_report", "annual_report", "quarterly_report"}
    earnings_types = {"earnings", "guidance"}
    macro_types = {"macro_release", "fed_decision", "geopolitical", "regulatory"}

    if event_type in news_types:
        return "news"
    if event_type in filing_types:
        return "filing"
    if event_type in earnings_types:
        return "earnings"
    if event_type in macro_types:
        return "macro"
    return event_type


def _topic_terms(title: str, summary: str = "", limit: int = 5) -> list[str]:
    text = normalise_for_comparison(f"{title} {summary}")
    words = [word for word in text.split() if len(word) > 2 and word not in STOPWORDS]
    deduped: list[str] = []
    seen = set()
    for word in words:
        if word not in seen:
            deduped.append(word)
            seen.add(word)
        if len(deduped) >= limit:
            break
    return deduped or ["headline"]


def _compatible_types(type_a: str, type_b: str) -> bool:
    if type_a == type_b:
        return True

    news_types = {"market_news", "company_news", "headline", "news_search"}
    filing_types = {"filing", "current_report", "annual_report", "quarterly_report"}
    earnings_types = {"earnings", "guidance"}
    macro_types = {"macro_release", "fed_decision", "geopolitical", "regulatory"}

    for group in [news_types, filing_types, earnings_types, macro_types]:
        if type_a in group and type_b in group:
            return True

    return False
