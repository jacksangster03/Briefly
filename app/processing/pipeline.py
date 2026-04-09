"""Shared event-processing pipeline used by morning, intraday, and breaking."""

from __future__ import annotations

from pathlib import Path

from app.personalization.delivery_rules import BreakingRules, IntradayRules
from app.personalization.user_profile import UserProfile
from app.processing.dedupe import classify_against_sent_history, deduplicate_events
from app.processing.event_clustering import cluster_events
from app.processing.personal_relevance import compute_personal_relevance
from app.processing.relevance_scoring import score_events
from app.processing.source_credibility import apply_credibility_scores
from app.schemas.events import NormalisedEvent
from app.settings import Settings

import re

# Substring patterns: if any of these appear in lower(title + summary), suppress the event.
LOW_SIGNAL_PATTERNS = (
    "3 reasons to buy",
    "bull and bear of the day",
    "market today",
    "millionaire maker",
    "path to $",
    "axios reported",
    "space x isnt even public yet",
    "worth owning",
    "what analyst projections",
    "if i had",
    "buy now",
    "some facts to note",
    "out of favor",
    "best stock",
    "top stock",
    "retirement planning book",
    "jim cramer",
    "suze orman",
    "warren buffett had to say",
    "cathie wood",
    "exploring the top movers",
    "here are 2 smarter",
    "here are 3 smarter",
    "here's what history says",
    "everyone's buying",
    "top picks for",
    "top-rated stocks",
    "stocks to consider",
    "should you sell",
    "should you buy",
    "could make you a millionaire",
    "is it too late to buy",
    "my top pick",
    "beaten-down stock",
    "beaten down stock",
    "bargain stock",
    "hot stock",
    "next big thing",
    "majority of americans say",
    "from an investment standpoint",
    "for passive income",
    "for retirement",
    "no-brainer stock",
)

# Regex patterns: if any match lower(title), suppress the event.
# These catch structural clickbait/listicle formats.
# Match both digits and spelled-out numbers at the start of titles
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"

_LOW_SIGNAL_REGEXES = [
    re.compile(p) for p in (
        rf"^{_NUM} (?:reasons?|stocks?|things?|ways?) to ",
        rf"^{_NUM} (?:best|top|worst|smarter|monster|magnificent)",
        r"^is .{3,60} (?:a buy|worth|a cheap|undervalued|overvalued|a good investment)",
        r"^is .{3,60}\?$",            # generic question-headline clickbait
        r"^why .{3,80} (?:moved|dropped|surged|fell|rose|tanked|soared)\b",
        r"^(?:here'?s? (?:why|what|how)|what (?:you need|investors? need|to know))",
        rf"{_NUM} (?:risks?|things?) to watch",
        r"if you invested \$",
        r"better buy:",
        r"buy the dip",
    )
]

MACRO_SIGNAL_KEYWORDS = {
    "boe", "boj", "cpi", "earnings", "ecb", "fda", "fed", "fomc", "gaza",
    "guidance", "inflation", "iran", "israel", "jobs", "lebanon", "merger",
    "opec", "oil", "powell", "rates", "regulatory", "sanction", "tariff",
    "treasury", "yield",
}


def process_event_stream(
    events: list[NormalisedEvent],
    profile: UserProfile,
    settings: Settings,
) -> list[NormalisedEvent]:
    """Normalize, cluster, classify, and score an incoming event stream."""
    deduped = deduplicate_events(
        events,
        apply_ticker_clustering=False,
        mark_sent_history=False,
    )
    apply_credibility_scores(deduped, sources_config_path=Path(settings.configs_dir) / "sources.yaml")
    compute_personal_relevance(
        deduped,
        profile,
        interest_weights_path=Path(settings.configs_dir) / "interest_weights.yaml",
    )

    prelim_scored = score_events(deduped, profile)
    clustered = cluster_events(prelim_scored)
    classified = classify_against_sent_history(clustered)
    rescored = score_events(classified, profile)
    return rescored


def select_intraday_events(
    events: list[NormalisedEvent],
    rules: IntradayRules,
) -> list[NormalisedEvent]:
    selected: list[NormalisedEvent] = []
    seen_clusters: set[str] = set()

    for evt in events:
        if not is_actionable_event(evt):
            continue
        if evt.cluster_id and evt.cluster_id in seen_clusters:
            continue

        required_score = (
            rules.continuation_min_final_score
            if evt.update_status == "material_update"
            else rules.min_final_score
        )
        required_novelty = (
            rules.continuation_min_novelty_score
            if evt.update_status == "material_update"
            else rules.min_novelty_score
        )
        if evt.final_score < required_score or evt.novelty_score < required_novelty:
            continue

        selected.append(evt)
        if evt.cluster_id:
            seen_clusters.add(evt.cluster_id)
        if len(selected) >= rules.max_events_per_update:
            break

    return selected


def select_breaking_events(
    events: list[NormalisedEvent],
    rules: BreakingRules,
) -> list[NormalisedEvent]:
    selected: list[NormalisedEvent] = []
    for evt in events:
        if not is_actionable_event(evt):
            continue
        if evt.already_sent:
            continue
        if rules.require_ticker and not evt.tickers:
            continue
        if evt.factual_confidence_score < rules.min_factual_confidence:
            continue

        threshold = rules.min_final_score
        if evt.update_status == "material_update":
            threshold = max(threshold, rules.material_update_min_final_score)
        if evt.event_type in rules.elevated_threshold_types:
            threshold = max(threshold, rules.elevated_threshold_types[evt.event_type])
        if evt.event_type in rules.high_priority_types:
            threshold -= 0.03

        if evt.final_score >= threshold:
            selected.append(evt)
        if len(selected) >= rules.max_per_hour:
            break

    return selected


def is_actionable_event(evt: NormalisedEvent) -> bool:
    """Filter out low-signal feature content before surfacing it to users."""
    if evt.source == "sec_edgar":
        return True

    text = f"{evt.title} {evt.summary}".lower()
    title_lower = evt.title.lower()

    # Substring blocklist
    if any(pattern in text for pattern in LOW_SIGNAL_PATTERNS):
        return False

    # Regex blocklist (applied to title only for precision)
    if any(rx.search(title_lower) for rx in _LOW_SIGNAL_REGEXES):
        return False

    if evt.tickers:
        return True
    if evt.sectors:
        return True
    if evt.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory"}:
        return True
    if evt.source == "newsapi" and evt.personal_relevance_score < 0.55:
        return False
    return any(keyword in text for keyword in MACRO_SIGNAL_KEYWORDS)
