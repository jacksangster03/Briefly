"""Personal relevance scoring: match events against user preferences.

Computes a relevance score based on:
- Sector match against user's sector weights
- Ticker match against watchlists (primary > secondary > monitor)
- Region match against coverage weights
- Theme/keyword match against interest weights
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent

logger = get_logger("personal_relevance")


def _normalize_region_key(region: str) -> str:
    return str(region or "").strip().lower().replace(" ", "_").replace("-", "_")


def compute_personal_relevance(
    events: list[NormalisedEvent],
    profile: UserProfile,
    interest_weights_path: Path | None = None,
) -> list[NormalisedEvent]:
    """Set personal_relevance_score on each event based on user profile."""
    theme_keywords = {}
    if interest_weights_path and interest_weights_path.exists():
        with open(interest_weights_path) as f:
            config = yaml.safe_load(f) or {}
        theme_keywords = config.get("theme_keywords", {})

    primary_set = set(t.upper() for t in profile.watchlist_primary)
    secondary_set = set(t.upper() for t in profile.watchlist_secondary)
    monitor_set = set(t.upper() for t in profile.watchlist_monitor)
    portfolio_set = set(t.upper() for t in profile.portfolio_symbols)
    portfolio_weights = profile.portfolio_weight_by_ticker
    bucket_by_ticker = {
        holding.symbol: (holding.bucket or "")
        for holding in profile.portfolio_holdings
    }

    for evt in events:
        scores = []

        # Sector relevance
        if evt.sectors:
            sector_score = max(
                profile.sector_weights.get(s, 0.2) for s in evt.sectors
            )
            scores.append(sector_score)

        # Watchlist relevance
        watchlist_score = 0.0
        for ticker in evt.tickers:
            t = ticker.upper()
            if t in primary_set:
                watchlist_score = max(watchlist_score, 1.0)
            elif t in secondary_set:
                watchlist_score = max(watchlist_score, 0.75)
            elif t in monitor_set:
                watchlist_score = max(watchlist_score, 0.50)
        if watchlist_score > 0:
            scores.append(watchlist_score)

        # Portfolio relevance: direct position hits outrank watchlist-only
        # relevance; sector read-through is a secondary signal.
        portfolio_score = 0.0
        for ticker in evt.tickers:
            t = ticker.upper()
            if t not in portfolio_set:
                continue
            weight = portfolio_weights.get(t, 0.0)
            direct = 0.82
            if weight >= 6.0:
                direct = 1.0
            elif weight >= 3.0:
                direct = 0.92
            elif weight > 0:
                direct = 0.86
            bucket = bucket_by_ticker.get(t, "")
            if bucket == "core":
                direct = min(1.0, direct + 0.04)
            elif bucket == "satellite":
                direct = max(0.0, direct - 0.06)
            portfolio_score = max(portfolio_score, direct)

        if portfolio_score == 0 and evt.sectors and profile.portfolio_sector_weights:
            concentration = max(profile.portfolio_sector_weights.get(sector, 0.0) for sector in evt.sectors)
            if concentration > 0:
                portfolio_score = min(0.75, 0.40 + (0.50 * concentration))

        if portfolio_score > 0:
            scores.append(portfolio_score)

        # Region relevance
        if evt.regions:
            region_score = 0.3
            for region in evt.regions:
                key = _normalize_region_key(region)
                region_score = max(region_score, profile.coverage_weights.get(key, 0.3))
                if key == "global_macro":
                    region_score = max(region_score, profile.coverage_weights.get("global", 0.3))
            scores.append(region_score)

        # Theme/keyword matching
        if theme_keywords:
            theme_score = _match_themes(evt, theme_keywords)
            if theme_score > 0:
                scores.append(theme_score)

        # Final: max of all signals (not average, so a single strong signal dominates)
        evt.personal_relevance_score = max(scores) if scores else 0.2

    logger.debug("Computed personal relevance for %d events", len(events))
    return events


def _match_themes(evt: NormalisedEvent, theme_keywords: dict[str, list[str]]) -> float:
    """Check if event text matches any theme keywords."""
    text = f"{evt.title} {evt.summary}".lower()
    best_score = 0.0

    for _theme, keywords in theme_keywords.items():
        for kw in keywords:
            if kw.lower() in text:
                best_score = max(best_score, 0.85)
                break  # one match per theme is enough

    return best_score
