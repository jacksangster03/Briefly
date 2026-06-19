"""Price confirmation: does actual price/volume action support a story's
implied direction?

Per docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.7 (Phase 4), this is the
highest-leverage fix for "is it confirmed?" and does not exist anywhere
upstream today: no module checks whether a ticker's price actually moved in
the direction a news event implies.

This module is deliberately pure: it takes already-fetched quotes (the same
QuoteData objects assembled for charts/watchlist sections elsewhere) and a
direction signal, and returns a PriceConfirmationStatus plus a human-readable
detail string. It makes no provider calls, no DB calls, and is not wired into
the pipeline, generators, or formatters yet (see Part 17.7 for the phased
rollout). It never decides inclusion or trust: a ResearchEvent with
price_confirmation_status="contradicted" is still rendered if its other
scores warrant it; this is one input among several in the ranking formula
(Part 17.4), not a suppression gate by itself.
"""

from __future__ import annotations

from typing import Literal

from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import PriceConfirmationStatus, ResearchEvent

Direction = Literal["up", "down", "neutral"]

# Below this absolute sentiment magnitude, treat the event as having no clear
# directional implication (matches the kind of neutral commentary/context
# stories that shouldn't be scored against price action at all).
_NEUTRAL_SENTIMENT_BAND = 0.15

# Minimum absolute price move to count as a real confirming/contradicting
# signal rather than noise. Matches the existing
# settings.breaking_followup_min_asset_move_pct default (0.9) so "is this
# move material" means the same thing across breaking-alert follow-ups and
# price confirmation. Not imported from Settings directly so this module
# stays dependency-free and unit-testable in isolation (same rationale as
# app/risk/advanced_metrics.py).
DEFAULT_MOVE_THRESHOLD_PCT = 0.9


def expected_direction_from_sentiment(
    sentiment: float,
    *,
    neutral_band: float = _NEUTRAL_SENTIMENT_BAND,
) -> Direction:
    """Map a NormalisedEvent.sentiment score (-1.0 to 1.0) to an expected
    price direction. Scores within ``neutral_band`` of zero are treated as
    having no clear directional implication.
    """
    if sentiment > neutral_band:
        return "up"
    if sentiment < -neutral_band:
        return "down"
    return "neutral"


def _confirm_single_quote(
    quote: QuoteData,
    direction: Direction,
    move_threshold_pct: float,
) -> tuple[PriceConfirmationStatus, str]:
    move = quote.change_percent
    symbol = quote.symbol
    if direction == "neutral":
        return "unavailable", f"{symbol}: no clear expected direction"
    if abs(move) < move_threshold_pct:
        return "pending", f"{symbol}: move {move:+.2f}% below {move_threshold_pct:.1f}% threshold"
    moved_up = move > 0
    expected_up = direction == "up"
    if moved_up == expected_up:
        return "confirmed", f"{symbol}: moved {move:+.2f}%, consistent with expected direction"
    return "contradicted", f"{symbol}: moved {move:+.2f}%, opposite of expected direction"


# Precedence when multiple tickers disagree: a confirming move from any one
# ticker is taken as confirmation (the story is about at least one real,
# moving asset); only when nothing confirms does a contradiction count;
# pending/unavailable are the fallback when no ticker shows a material move.
_STATUS_PRIORITY: dict[PriceConfirmationStatus, int] = {
    "confirmed": 3,
    "contradicted": 2,
    "pending": 1,
    "unavailable": 0,
}


def confirm_price_action(
    *,
    tickers: list[str],
    direction: Direction,
    quotes_by_symbol: dict[str, QuoteData],
    move_threshold_pct: float = DEFAULT_MOVE_THRESHOLD_PCT,
) -> tuple[PriceConfirmationStatus, str]:
    """Check whether quoted price action for ``tickers`` confirms ``direction``.

    Returns (status, detail). With multiple tickers, the highest-priority
    status wins (confirmed > contradicted > pending > unavailable); see
    _STATUS_PRIORITY for the rationale.
    """
    if not tickers:
        return "unavailable", "no tickers to check"
    if direction == "neutral":
        return "unavailable", "no clear expected direction from event sentiment"

    results: list[tuple[PriceConfirmationStatus, str]] = []
    for ticker in tickers:
        quote = quotes_by_symbol.get(ticker.upper())
        if quote is None:
            results.append(("unavailable", f"{ticker}: no quote available"))
            continue
        results.append(_confirm_single_quote(quote, direction, move_threshold_pct))

    best = max(results, key=lambda r: _STATUS_PRIORITY[r[0]])
    return best


def apply_price_confirmation(
    research_event: ResearchEvent,
    source_event: NormalisedEvent,
    quotes_by_symbol: dict[str, QuoteData],
    *,
    move_threshold_pct: float = DEFAULT_MOVE_THRESHOLD_PCT,
) -> ResearchEvent:
    """Populate price_confirmation_status/detail on ``research_event`` using
    ``source_event.sentiment`` for direction and ``research_event.tickers``
    (falling back to ``source_event.tickers``) for which quotes to check.

    Mutates and returns ``research_event``. Does not touch any other field:
    callers remain responsible for inclusion/suppression decisions.
    """
    direction = expected_direction_from_sentiment(source_event.sentiment)
    tickers = research_event.tickers or list(source_event.tickers)
    status, detail = confirm_price_action(
        tickers=tickers,
        direction=direction,
        quotes_by_symbol=quotes_by_symbol,
        move_threshold_pct=move_threshold_pct,
    )
    research_event.price_confirmation_status = status
    research_event.price_confirmation_detail = detail
    return research_event

