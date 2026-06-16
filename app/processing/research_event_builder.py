"""Builds a ResearchEvent from an already-scored NormalisedEvent.

Per docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.7 (Phase 5), this is the
consolidation step: source quality, corroboration, and portfolio/watchlist
ranking bonuses currently live as three independent computations
(processing/source_credibility.py, briefing/global_news_selector.py,
briefing/theme_builder.py). build_research_event() is the one place that
pulls all of them onto a single ResearchEvent instead of leaving each
caller (themes, global news selection, watchlist sections) compute its own
view of "how good is this event" separately.

This module makes no provider/DB calls. Price confirmation requires
already-fetched quotes and is only applied if the caller supplies them; it
is optional precisely so this builder stays usable in contexts (like tests
or a future shadow run) where quotes aren't available yet.
"""

from __future__ import annotations

from app.briefing.global_news_selector import corroboration_score
from app.briefing.theme_builder import portfolio_tag_bonus, watchlist_overlap_bonus
from app.processing.causal_channel import classify_causal_channel
from app.processing.price_confirmation import apply_price_confirmation
from app.processing.source_credibility import DEFAULT_TRUST_TIERS, PROVIDER_TIERS
from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import CausalChannel, ResearchEvent

INTERPRETATION_TEMPLATES: dict[CausalChannel, str] = {
    "rates": (
        "Rates/macro context can reset valuation-sensitive growth and duration risk."
    ),
    "earnings": (
        "Direct earnings signal for the affected name(s); "
        "watch for sector read-through if guidance moves."
    ),
    "regulatory": (
        "Regulatory/legal outcomes can change the addressable market or cost base "
        "for affected names."
    ),
    "geopolitical": (
        "Headline risk matters only if cross-asset confirmation (oil, VIX, FX) follows."
    ),
    "supply_chain": (
        "Supply-chain disruption can compress margins or delay revenue recognition "
        "across the value chain."
    ),
    "sentiment": (
        "Analyst/positioning commentary; weigh against price confirmation rather "
        "than treating as fundamental news."
    ),
    "other": (
        "Context for portfolio/watchlist exposure; materiality depends on "
        "downstream confirmation."
    ),
}


def resolve_source_tier_and_quality(event: NormalisedEvent) -> tuple[str, float]:
    """Resolve (source_tier, source_quality_score) for an event.

    Starts from the provider-tier baseline (processing/source_credibility.py),
    then takes the max against factual_confidence_score, since that field
    already reflects apply_credibility_scores()'s source-name/filing/FRED
    adjustments on top of the provider-tier baseline.
    """
    tier = PROVIDER_TIERS.get(event.source, "medium")
    quality = DEFAULT_TRUST_TIERS.get(tier, 0.50)
    if event.factual_confidence_score:
        quality = max(quality, event.factual_confidence_score)
    return tier, quality


def build_research_event(
    event: NormalisedEvent,
    *,
    quotes_by_symbol: dict[str, QuoteData] | None = None,
    preferred_symbols: set[str] | None = None,
) -> ResearchEvent:
    """Build a fully-populated ResearchEvent from a deduplicated, scored
    NormalisedEvent.

    Consolidates source quality (source_credibility.py), corroboration
    (global_news_selector.py), and portfolio/watchlist ranking bonuses
    (theme_builder.py) onto one object. Sets causal_channel and a templated
    interpretation. Applies price confirmation only if quotes are supplied.
    """
    research_event = ResearchEvent.from_normalised_event(event)

    tier, quality = resolve_source_tier_and_quality(event)
    research_event.source_tier = tier
    research_event.source_quality_score = quality

    research_event.corroboration_score = corroboration_score(event)
    research_event.causal_channel = classify_causal_channel(event)
    research_event.interpretation = INTERPRETATION_TEMPLATES[research_event.causal_channel]

    tag_bonus = portfolio_tag_bonus(event)
    watch_bonus = watchlist_overlap_bonus(event, preferred_symbols)
    research_event.watchlist_relevance_score = min(1.0, watch_bonus / 0.35) if watch_bonus else 0.0
    # portfolio_tag_bonus folds into the existing portfolio_relevance_score
    # (already seeded from personal_relevance_score by from_normalised_event)
    # rather than overwriting it, since both signals matter for ranking.
    research_event.portfolio_relevance_score = min(
        1.0, research_event.portfolio_relevance_score + tag_bonus
    )

    if quotes_by_symbol:
        apply_price_confirmation(research_event, event, quotes_by_symbol)

    return research_event
