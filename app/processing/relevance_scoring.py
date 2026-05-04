"""Multi-dimensional event scoring engine.

Computes five scoring dimensions per event, then a weighted composite:
1. source_credibility   - how trustworthy is the source?
2. event_type_weight    - how market-moving is this type of event?
3. personal_relevance   - does it match the user's sectors/watchlist/regions?
4. novelty              - has the user already seen this?
5. factual_confidence   - is this verified or speculative?

The final_score is a weighted combination used for inclusion/ranking.
"""

from __future__ import annotations

from app.logger import get_logger
from app.processing.sentiment import compute_sentiment
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent

logger = get_logger("scoring")

# -- Source credibility by provider -------------------------------------------
SOURCE_CREDIBILITY: dict[str, float] = {
    "sec_edgar": 0.95,
    "fred": 0.90,
    "finnhub": 0.75,
    "polygon": 0.75,
    "newsapi": 0.55,
    "yfinance": 0.50,
    "x_twitter": 0.25,
}

# -- Event type importance weights -------------------------------------------
EVENT_TYPE_WEIGHTS: dict[str, float] = {
    "earnings": 0.90,
    "guidance": 0.90,
    "fda_decision": 0.95,
    "m_and_a": 0.90,
    "insider_transaction": 0.70,
    "current_report": 0.80,     # 8-K
    "annual_report": 0.75,      # 10-K
    "quarterly_report": 0.70,   # 10-Q
    "ownership_disclosure": 0.65,
    "macro_release": 0.85,
    "fed_decision": 0.95,
    "geopolitical": 0.80,
    "regulatory": 0.80,
    "analyst_action": 0.65,
    "market_news": 0.50,
    "company_news": 0.55,
    "headline": 0.45,
    "news_search": 0.40,
    "filing": 0.60,
}

# -- Composite weights -------------------------------------------------------
COMPOSITE_WEIGHTS = {
    "source_credibility": 0.15,
    "event_type_weight": 0.25,
    "personal_relevance": 0.25,
    "novelty": 0.20,
    "factual_confidence": 0.15,
}

HIGH_SIGNAL_KEYWORDS = {
    "earnings": 0.15,
    "guidance": 0.18,
    "fed": 0.18,
    "inflation": 0.15,
    "fda": 0.20,
    "merger": 0.18,
    "acquisition": 0.18,
    "oil": 0.12,
    "tariff": 0.12,
    "yield": 0.10,
    "sanction": 0.12,
}

MACRO_TAG_KEYWORDS = (
    "yield",
    "rates",
    "inflation",
    "fed",
    "ecb",
    "boj",
    "dollar",
    "usd",
    "fx",
    "oil",
    "crude",
    "gold",
    "opec",
    "tariff",
    "sanction",
    "shipping",
    "geopolitic",
)

LOW_SIGNAL_PATTERNS = (
    "3 reasons to buy",
    "bull and bear of the day",
    "worth owning",
    "millionaire maker",
    "if i had",
    "buy now",
    "path to $",
    "what analyst projections",
    "some facts to note",
)

SENTIMENT_ADJUSTMENT_CAP = 0.05


def _normalize_region_key(region: str) -> str:
    return str(region or "").strip().lower().replace(" ", "_").replace("-", "_")


def score_event(
    event: NormalisedEvent,
    profile: UserProfile,
    sent_hashes: set[str] | None = None,
) -> NormalisedEvent:
    """Score a single event across all dimensions and set final_score."""
    scores: dict[str, float] = {}

    # 1. Source credibility
    scores["source_credibility"] = SOURCE_CREDIBILITY.get(event.source, 0.3)

    # 2. Event type weight
    scores["event_type_weight"] = max(
        EVENT_TYPE_WEIGHTS.get(event.event_type, 0.35),
        _keyword_impact(event),
    )

    # 3. Personal relevance (max of sector, watchlist, region, portfolio signals)
    sector_score = _sector_relevance(event, profile)
    watchlist_score = _watchlist_relevance(event, profile)
    region_score = _region_relevance(event, profile)
    portfolio_score = _portfolio_relevance(event, profile)
    scores["personal_relevance"] = max(
        sector_score,
        watchlist_score,
        region_score,
        portfolio_score,
        event.personal_relevance_score or 0.0,
    )
    event.raw_data["portfolio_relevance"] = round(portfolio_score, 4)
    event.portfolio_tag = _portfolio_tag(event, profile)
    event.raw_data["portfolio_tag"] = event.portfolio_tag

    # 4. Novelty
    if sent_hashes and event.content_hash in sent_hashes:
        scores["novelty"] = 0.1
        event.already_sent = True
    else:
        scores["novelty"] = event.novelty_score if event.novelty_score > 0 else 1.0

    # 5. Factual confidence (set by provider, adjusted here)
    scores["factual_confidence"] = event.factual_confidence_score

    # Apply a small penalty to generic feature/listicle content.
    quality_penalty = _quality_penalty(event)
    sentiment_adjustment = _sentiment_adjustment(event)

    # Composite
    final = sum(
        COMPOSITE_WEIGHTS[dim] * scores[dim]
        for dim in COMPOSITE_WEIGHTS
    )
    final = max(0.0, min(1.0, final - quality_penalty + sentiment_adjustment))

    # Apply the scores back to the event
    event.importance_score = scores["event_type_weight"]
    event.personal_relevance_score = scores["personal_relevance"]
    event.novelty_score = scores["novelty"]
    event.attention_score = scores["source_credibility"]
    event.sentiment = round(event.sentiment, 4)
    event.final_score = round(final, 4)

    # Build explanation
    event.score_explanation = _build_explanation(scores, event, profile)

    return event


def score_events(
    events: list[NormalisedEvent],
    profile: UserProfile,
    sent_hashes: set[str] | None = None,
) -> list[NormalisedEvent]:
    """Score and sort events by final_score descending."""
    scored = [score_event(e, profile, sent_hashes) for e in events]
    scored.sort(key=lambda e: e.final_score, reverse=True)
    logger.info(
        "Scored %d events. Top score: %.3f, Bottom: %.3f",
        len(scored),
        scored[0].final_score if scored else 0,
        scored[-1].final_score if scored else 0,
    )
    return scored


# -- Relevance sub-scorers ---------------------------------------------------

def _sector_relevance(event: NormalisedEvent, profile: UserProfile) -> float:
    if not event.sectors:
        return 0.2
    return max(
        profile.sector_weights.get(s, 0.2) for s in event.sectors
    )


def _watchlist_relevance(event: NormalisedEvent, profile: UserProfile) -> float:
    if not event.tickers:
        return 0.0
    primary = set(t.upper() for t in profile.watchlist_primary)
    secondary = set(t.upper() for t in profile.watchlist_secondary)
    for ticker in event.tickers:
        upper = ticker.upper()
        if upper in primary:
            return 1.0
        if upper in secondary:
            return 0.75
    return 0.0


def _region_relevance(event: NormalisedEvent, profile: UserProfile) -> float:
    if not event.regions:
        return 0.3  # default for events with no region tag
    score = 0.3
    for region in event.regions:
        key = _normalize_region_key(region)
        score = max(score, profile.coverage_weights.get(key, 0.3))
        if key == "global_macro":
            score = max(score, profile.coverage_weights.get("global", 0.3))
    return score


def _portfolio_relevance(event: NormalisedEvent, profile: UserProfile) -> float:
    """Estimate event relevance against actual held positions/exposure."""
    if not profile.portfolio_holdings:
        return 0.0

    holdings = set(profile.portfolio_symbols)
    weight_by_ticker = profile.portfolio_weight_by_ticker
    bucket_by_ticker = {
        p.symbol: (p.bucket or "")
        for p in profile.portfolio_holdings
    }

    direct_score = 0.0
    for ticker in event.tickers:
        upper = ticker.upper()
        if upper not in holdings:
            continue
        weight = weight_by_ticker.get(upper, 0.0)
        score = 0.82
        if weight >= 6.0:
            score = 1.0
        elif weight >= 3.0:
            score = 0.93
        elif weight > 0:
            score = 0.86
        bucket = bucket_by_ticker.get(upper, "")
        if bucket == "core":
            score = min(1.0, score + 0.04)
        elif bucket == "satellite":
            score = max(0.0, score - 0.06)
        direct_score = max(direct_score, score)

    sector_readthrough = 0.0
    if event.sectors and profile.portfolio_sector_weights:
        concentration = max(profile.portfolio_sector_weights.get(sector, 0.0) for sector in event.sectors)
        if concentration > 0:
            sector_readthrough = min(0.75, 0.40 + (0.50 * concentration))

    return max(direct_score, sector_readthrough)


def _portfolio_tag(event: NormalisedEvent, profile: UserProfile) -> str:
    """Deterministic portfolio priority tag used for newsroom ordering."""
    holdings = set(profile.portfolio_symbols)
    watchlist = {t.upper() for t in profile.all_watchlist_tickers}
    tickers = {t.upper() for t in event.tickers}
    if holdings and tickers & holdings:
        return "DIRECT"

    if event.sectors and profile.portfolio_sector_weights:
        if any(profile.portfolio_sector_weights.get(sector, 0.0) >= 0.10 for sector in event.sectors):
            return "SECTOR"

    text = f"{event.title} {event.summary}".lower()
    if any(keyword in text for keyword in MACRO_TAG_KEYWORDS):
        return "MACRO"

    if tickers & watchlist:
        return "TANGENTIAL"
    return ""


def _sentiment_adjustment(event: NormalisedEvent) -> float:
    text = " ".join(part for part in [event.title, event.summary] if part).strip()
    if not text:
        return 0.0
    result = compute_sentiment(text)
    event.sentiment = result.score
    event.raw_data["sentiment_label"] = result.label
    event.raw_data["sentiment_score"] = round(result.score, 4)
    return max(-SENTIMENT_ADJUSTMENT_CAP, min(SENTIMENT_ADJUSTMENT_CAP, result.score * SENTIMENT_ADJUSTMENT_CAP))


def _build_explanation(
    scores: dict[str, float],
    event: NormalisedEvent,
    profile: UserProfile,
) -> str:
    """Human-readable explanation of why an event scored as it did."""
    parts = []
    parts.append(f"source={event.source} ({scores['source_credibility']:.2f})")
    parts.append(f"type={event.event_type} ({scores['event_type_weight']:.2f})")
    parts.append(f"relevance={scores['personal_relevance']:.2f}")
    portfolio_relevance = float(event.raw_data.get("portfolio_relevance", 0.0))
    if portfolio_relevance > 0:
        parts.append(f"portfolio={portfolio_relevance:.2f}")

    if event.tickers:
        in_watchlist = [
            t for t in event.tickers
            if t.upper() in set(
                item.upper() for item in (profile.watchlist_primary + profile.watchlist_secondary)
            )
        ]
        if in_watchlist:
            parts.append(f"watchlist=[{','.join(in_watchlist)}]")
        in_portfolio = [
            t for t in event.tickers if t.upper() in set(profile.portfolio_symbols)
        ]
        if in_portfolio:
            parts.append(f"portfolio_holdings=[{','.join(in_portfolio)}]")

    parts.append(f"novelty={scores['novelty']:.2f}")
    parts.append(f"confidence={scores['factual_confidence']:.2f}")
    if event.reason_code:
        parts.append(f"reason={event.reason_code}")
    parts.append(f"FINAL={event.final_score:.4f}")

    return " | ".join(parts)


def _keyword_impact(event: NormalisedEvent) -> float:
    text = f"{event.title} {event.summary}".lower()
    impact = 0.0
    for keyword, boost in HIGH_SIGNAL_KEYWORDS.items():
        if keyword in text:
            impact = max(impact, min(0.95, EVENT_TYPE_WEIGHTS.get(event.event_type, 0.35) + boost))
    return impact


def _quality_penalty(event: NormalisedEvent) -> float:
    text = f"{event.title} {event.summary}".lower()
    if any(pattern in text for pattern in LOW_SIGNAL_PATTERNS):
        return 0.08
    return 0.0
