"""Shared event-processing pipeline used by morning, intraday, and breaking."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.personalization.delivery_rules import BreakingRules, IntradayRules
from app.personalization.user_profile import UserProfile
from app.processing.cleaners import strip_title_suffix
from app.processing.dedupe import classify_against_sent_history, deduplicate_events
from app.processing.event_clustering import cluster_events
from app.processing.personal_relevance import compute_personal_relevance
from app.processing.relevance_scoring import score_events
from app.processing.source_credibility import apply_credibility_scores
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.universe.ticker_metadata import (
    TICKER_DISPLAY_NAMES,
    extract_tickers_from_text,
)

import re

SectorLookup = Callable[[str], list[str]]

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
    "buying opportunity",
    "beaten-down stock",
    "beaten down stock",
    "bottom-fish",
    "bottom fish",
    "bargain stock",
    "hot stock",
    "next big thing",
    "majority of americans say",
    "from an investment standpoint",
    "for passive income",
    "for retirement",
    "no-brainer stock",
    "before placing a bet",
    "higher stock price",
    "don't buy either stock",
    "stock is a buy before",
    "don't buy either stock until you read this",
    "earnings preview",
    "ahead of earnings",
    "how to play",
    "could surge",
    "spacex isn't even public yet",
    "what's going on with",
    "what is going on with",
    "wall street thinks",
    "hidden gem",
    "must-buy",
    "must buy stock",
    "can't-miss",
    "can't miss stock",
    "sleeper stock",
    "under the radar",
    "flying under the radar",
    "overlooked stock",
    "safe dividend",
    "secret to",
    "underrated stock",
    "stampeded into",
    "stampede into",
    "charts reveal",
    "the chart says",
    "one chart shows",
    "countdown to",
    "stark message",
    "stark warning",
    "going public",
    "is going public",
    "soon to go public",
    "investors turn bearish",
    "investors turn bullish",
    "turning bearish",
    "turning bullish",
    "growing more bullish",
    "growing more bearish",
    "should investors buy",
    "should investors sell",
    "what to do if",
    "is it time to buy",
    "is it time to sell",
    "is now the time",
    "is now a good time",
    "time to buy",
    "what investors need",
    "why investors should",
    "could be a bargain",
    "could double",
    "could triple",
    "set to soar",
    "set to surge",
    "best bet",
    "worst performing",
    "the smart money",
    "smart money is buying",
    "smart money is selling",
    "dividend machine",
    "cash cow",
    "cash machine",
    "penny stock",
    "meme stock",
    "stock to watch",
    "stocks to watch",
    "something just changed with",
    "screaming buy",
    "screaming sell",
    "don't panic over",
    "stock for retirees",
    "stocks for retirees",
    "real opportunity",
    "the real opportunity",
    "bye-bye to buy-buy",
    "rating upgrade",
    "rating downgrade",
    "growth stocks to buy",
    "value stocks to buy",
    "dividend stocks to buy",
    "stock keeps going down",
    "stock keeps going up",
    "wall street sees",
    "wall street's take",
    "zacks analyst blog",
    "analyst blog highlights",
    "here's why wall street",
    "here's why the street",
    "best stocks to buy",
    "top stocks to buy",
)

# Regex patterns: if any match lower(title), suppress the event.
# These catch structural clickbait/listicle formats.
# Match both digits and spelled-out numbers at the start of titles
_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"

_LOW_SIGNAL_REGEXES = [
    re.compile(p) for p in (
        rf"^{_NUM} (?:reasons?|stocks?|things?|ways?) to ",
        rf"^{_NUM} (?:best|top|worst|smarter|monster|magnificent|high[- ]yield)",
        rf"^{_NUM} (?:dividend|growth|value|no[- ]brainer|under[- ]the[- ]radar) stocks?",
        r"^is .{3,60} (?:a buy|worth|a cheap|undervalued|overvalued|a good investment)",
        r"^is .{3,60}\?$",            # generic question-headline clickbait
        r"^why .{3,80} (?:moved|dropped|surged|fell|rose|tanked|soared|jumped|plunged|crashed|skyrocketed)\b",
        r"^(?:here'?s? (?:why|what|how)|what (?:you need|investors? need|to know))",
        rf"{_NUM} (?:risks?|things?) to watch",
        r"if you invested \$",
        r"^better .{3,40}:",              # "Better Buy:", "Better Hold:", comparison headlines
        r"buy the dip",
        r"prediction: .* stock is a buy before",
        r"^what(?:'s| is) (?:going on|happening) with",
        r"\bdown \d+(?:\.\d+)?%.*analysts?\b",   # "down 12% - analysts say..."
        r"\bup \d+(?:\.\d+)?%.*(?:analysts?|here'?s why|this is why)\b",
        r"^is this the next ",
        r"\bhigh[- ]yield(?:ing)? .{0,20} stock",
        r"\banalysts? (?:predict|expect|project) .{0,40} (?:surge|soar|jump|rally)",
    )
]

MACRO_SIGNAL_KEYWORDS = {
    "boe", "boj", "cpi", "earnings", "ecb", "fda", "fed", "fomc", "gaza",
    "guidance", "inflation", "iran", "israel", "jobs", "lebanon", "merger",
    "opec", "oil", "powell", "rates", "regulatory", "sanction", "tariff",
    "treasury", "yield",
}

REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "US": (
        "u.s.",
        "united states",
        "white house",
        "washington",
        "federal reserve",
        "treasury",
        "wall street",
        "nasdaq",
        "s&p 500",
        "dow jones",
        "russell 2000",
    ),
    "Europe": (
        "europe",
        "eurozone",
        "european union",
        "ecb",
        "boe",
        "bank of england",
        "stoxx",
        "ftse",
        "dax",
        "cac 40",
        "britain",
        "u.k.",
        "germany",
        "france",
        "italy",
        "spain",
        "brussels",
    ),
    "Middle East": (
        "middle east",
        "iran",
        "israel",
        "gaza",
        "lebanon",
        "hormuz",
        "opec",
        "saudi",
        "riyadh",
        "uae",
        "qatar",
        "yemen",
        "tehran",
    ),
    "Asia": (
        "asia",
        "china",
        "beijing",
        "japan",
        "boj",
        "nikkei",
        "taiwan",
        "south korea",
        "korea",
        "hong kong",
        "india",
        "singapore",
        "shanghai",
        "shenzhen",
        "kospi",
    ),
    "LATAM": (
        "latam",
        "latin america",
        "brazil",
        "mexico",
        "argentina",
        "chile",
        "peru",
        "colombia",
        "ibovespa",
        "bovespa",
    ),
    "Global Macro": (
        "global",
        "worldwide",
        "cross-asset",
        "risk sentiment",
        "g7",
        "g20",
        "imf",
        "world bank",
        "oecd",
        "geopolitical",
    ),
}


def _normalise_filter_text(value: str) -> str:
    """Normalise punctuation so low-signal rules catch typographic variants."""
    return (
        value.lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("—", "-")
        .replace("–", "-")
        .replace("\xa0", " ")
    )


def process_event_stream(
    events: list[NormalisedEvent],
    profile: UserProfile,
    settings: Settings,
    sector_lookup: SectorLookup | None = None,
) -> list[NormalisedEvent]:
    """Normalize, cluster, classify, and score an incoming event stream.

    When ``sector_lookup`` is provided, sector enrichment runs inside the
    pipeline after ticker resolution so sectors are always derived from the
    cleaned ticker set (never from spurious provider-supplied tickers).
    """
    _clean_event_titles(events)
    _resolve_event_tickers(events)
    if sector_lookup is not None:
        _enrich_event_sectors(events, sector_lookup)
    _enrich_event_regions(events)

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
        # Cross-run suppression policy: one story should surface once across
        # morning/intraday/breaking rather than repeating via continuations.
        if evt.update_status != "new":
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
        # Breaking should be strict one-shot, not repeat continuations.
        if evt.update_status != "new":
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


def _clean_event_titles(events: list[NormalisedEvent]) -> None:
    """Strip source attribution suffixes from event titles."""
    for evt in events:
        evt.title = strip_title_suffix(evt.title)


_NON_NEWS_SOURCES = {"sec_edgar", "fred"}


def _resolve_event_tickers(events: list[NormalisedEvent]) -> None:
    """Resolve event tickers with title priority.

    Provider ticker data is unreliable: Finnhub's ``related`` field lists
    tangential tickers (TSMC story tagged NVDA because a Nvidia quote appears
    three paragraphs in) and NewsAPI headlines carry no tickers at all. This
    function runs three steps:

      1. Bucket each provider-supplied ticker by whether its symbol appears
         in the title or only in the summary; drop it if it appears nowhere.
      2. Extract additional tickers from the title and summary via the
         company-name lookup in ``ticker_metadata``.
      3. If any ticker resolves from the title, the event is labeled using
         only those tickers — summary-only tickers are discarded to stop
         incidental mentions from taking over the event label.

    Regulatory filings and macro data rows are left untouched: they already
    carry authoritative tickers.
    """
    for evt in events:
        if evt.source in _NON_NEWS_SOURCES:
            continue

        title = evt.title or ""
        summary = evt.summary or ""

        title_tickers: list[str] = []
        summary_tickers: list[str] = []

        for symbol in evt.tickers or []:
            upper = symbol.upper()
            if _symbol_in_text(upper, title):
                _append_unique(title_tickers, upper)
            elif _symbol_in_text(upper, summary):
                _append_unique(summary_tickers, upper)

        for extracted in extract_tickers_from_text(title):
            _append_unique(title_tickers, extracted)
        for extracted in extract_tickers_from_text(summary):
            if extracted not in title_tickers:
                _append_unique(summary_tickers, extracted)

        evt.tickers = title_tickers if title_tickers else summary_tickers


def _symbol_in_text(symbol: str, text: str) -> bool:
    """Return True if ``symbol`` appears in ``text`` with acceptable context.

    Short symbols (1–2 chars like T, F, V) must appear in explicit notation
    (``$T`` or ``(T)``) to avoid matching common English words. Longer symbols
    need only word-boundary matching.
    """
    if not text:
        return False
    upper = text.upper()
    if len(symbol) <= 2:
        return f"${symbol}" in upper or f"({symbol})" in upper
    return re.search(rf"\b{re.escape(symbol)}\b", upper) is not None


def _append_unique(bucket: list[str], value: str) -> None:
    if value and value not in bucket:
        bucket.append(value)


def _enrich_event_sectors(
    events: list[NormalisedEvent],
    sector_lookup: SectorLookup,
) -> None:
    """Populate sector tags from resolved tickers."""
    for evt in events:
        for ticker in evt.tickers:
            for sector in sector_lookup(ticker):
                if sector not in evt.sectors:
                    evt.sectors.append(sector)


def _enrich_event_regions(events: list[NormalisedEvent]) -> None:
    """Populate conservative region tags from event text and event type."""
    for evt in events:
        raw_text = f"{evt.title} {evt.summary}"
        text = _normalise_filter_text(f"{evt.title} {evt.summary}")

        for region, tokens in REGION_KEYWORDS.items():
            if any(token in text for token in tokens):
                if region not in evt.regions:
                    evt.regions.append(region)

        # Capture uppercase acronym references without confusing lowercase
        # pronouns like "tell us".
        if re.search(r"\bUS\b", raw_text) and "US" not in evt.regions:
            evt.regions.append("US")
        if re.search(r"\bEU\b", raw_text) and "Europe" not in evt.regions:
            evt.regions.append("Europe")

        if (
            evt.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory"}
            and "Global Macro" not in evt.regions
        ):
            evt.regions.append("Global Macro")


def is_actionable_event(evt: NormalisedEvent) -> bool:
    """Filter out low-signal feature content before surfacing it to users."""
    if evt.source == "sec_edgar":
        return True

    text = _normalise_filter_text(f"{evt.title} {evt.summary}")
    title_lower = _normalise_filter_text(evt.title)

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
