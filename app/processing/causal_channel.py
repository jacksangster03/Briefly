"""Deterministic classification of a NormalisedEvent's causal channel.

Per docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.3, ResearchEvent.causal_channel
replaces the ad-hoc bucket keys used today in
app/briefing/morning_generator.py::_build_applied_news_stack() (macro_rates,
regional, sector, watchlist, portfolio, event_risk, geopolitical) with a
small fixed taxonomy that interpretation text and ranking can both key off
of. This module is the single place that maps an event to a channel: event
types map directly where the mapping is unambiguous (earnings, fed_decision,
...); generic catch-all types (market_news, company_news, headline) fall
back to keyword matching on the title/summary text.
"""

from __future__ import annotations

from app.schemas.events import NormalisedEvent
from app.schemas.research_event import CausalChannel

# Event types with an unambiguous channel. Anything not listed here falls
# through to keyword matching (for generic news_search/market_news/headline
# types) or "other".
_EVENT_TYPE_CHANNEL: dict[str, CausalChannel] = {
    "earnings": "earnings",
    "guidance": "earnings",
    "quarterly_report": "earnings",
    "fed_decision": "rates",
    "macro_release": "rates",
    "fda_decision": "regulatory",
    "regulatory": "regulatory",
    "current_report": "other",
    "annual_report": "other",
    "insider_transaction": "other",
    "ownership_disclosure": "other",
    "m_and_a": "other",
    "geopolitical": "geopolitical",
    "analyst_action": "sentiment",
}

# Keyword fallback for generic event types (market_news, company_news,
# headline, news_search) where event_type alone doesn't disambiguate.
# Checked in this order; first match wins.
_KEYWORD_CHANNELS: tuple[tuple[CausalChannel, tuple[str, ...]], ...] = (
    ("rates", ("fed ", "federal reserve", "rate hike", "rate cut", "treasury yield",
               "interest rate", "inflation", "cpi ", "fomc", "central bank")),
    ("earnings", ("earnings", "quarterly results", "guidance", "revenue beat",
                  "revenue miss", "q1 ", "q2 ", "q3 ", "q4 ")),
    ("regulatory", ("lawsuit", "antitrust", "investigation", "sec charges",
                     "regulatory approval", "fda ", "ban on", "fined", "settlement")),
    ("geopolitical", ("sanctions", "war", "military", "conflict", "ceasefire",
                       "missile", "invasion", "geopolitical")),
    ("supply_chain", ("supply chain", "chip shortage", "export control", "tariff",
                        "shipping disruption", "factory shutdown", "production halt")),
    ("sentiment", ("analyst", "price target", "upgrade", "downgrade", "buy rating",
                    "sell rating", "outperform")),
)


def classify_causal_channel(event: NormalisedEvent) -> CausalChannel:
    """Return the deterministic causal channel for ``event``.

    Resolution order: explicit event_type mapping, then keyword match on
    title+summary, then "other" as the fallback.
    """
    channel = _EVENT_TYPE_CHANNEL.get(event.event_type)
    if channel:
        return channel

    text = f"{event.title} {event.summary}".lower()
    for channel, keywords in _KEYWORD_CHANNELS:
        if any(keyword in text for keyword in keywords):
            return channel

    return "other"
