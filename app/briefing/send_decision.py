"""Freshness-aware send-decision layer.

Determines whether a session briefing should be sent, and in what mode,
based on the live/stale/unavailable status of market data and news.

Send-decision matrix:
  1. market live/partial + material market/news signal -> normal briefing
  2. market live/partial + no fresh news -> market_only (if price threshold met)
  3. market stale_snapshot/unavailable + fresh material news -> news_only or degraded_context
  4. market stale_snapshot/unavailable + no fresh material news -> suppressed
  5. dry-run/preview -> always render degraded_context (never suppress delivery)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.schemas.briefings import MorningBriefing

logger = logging.getLogger("briefing.send_decision")

# Thresholds
_NEWS_FRESH_MATERIALITY_THRESHOLD = 2.0   # fresh_news_materiality_score to justify a send
_NEWS_FRESH_COUNT_MIN = 1                  # minimum fresh news items for news-led send

# Market data status constants (match briefing field values)
MARKET_STATUS_LIVE = "live"
MARKET_STATUS_PARTIAL = "partial"
MARKET_STATUS_STALE_SNAPSHOT = "stale_snapshot"
MARKET_STATUS_UNAVAILABLE = "unavailable"

NEWS_STATUS_FRESH = "fresh"
NEWS_STATUS_STALE = "stale"
NEWS_STATUS_EMPTY = "empty"
NEWS_STATUS_PROVIDER_OUTAGE = "provider_outage"

BRIEFING_MODE_NORMAL = "normal"
BRIEFING_MODE_MARKET_ONLY = "market_only"
BRIEFING_MODE_NEWS_ONLY = "news_only"
BRIEFING_MODE_DEGRADED_CONTEXT = "degraded_context"
BRIEFING_MODE_SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class SendDecision:
    mode: str           # one of the BRIEFING_MODE_* constants
    should_send: bool
    suppress_reason: str  # empty string when should_send=True
    log_label: str      # for delivery log (e.g. "sent_normal", "suppressed_degraded_no_fresh_data")


def classify_briefing_statuses(briefing: MorningBriefing) -> MorningBriefing:
    """Populate market_data_status, news_status, fresh_news_count, fresh_news_materiality_score
    on the briefing object. Returns the same object (mutated in place).

    If the briefing has already been pre-classified (fresh_news_count > 0, or
    market_data_status/news_status set to non-default values), re-classification is
    skipped to preserve explicit test overrides and caller-set values.
    """
    _already_set = (
        briefing.fresh_news_count > 0
        or briefing.market_data_status not in ("live", "")
        or briefing.news_status not in ("fresh", "")
    )
    if _already_set:
        return briefing

    # --- Market data status ---
    if briefing.market_data_outage:
        market_status = MARKET_STATUS_UNAVAILABLE
    elif getattr(briefing, "stale_snapshot_used", False):
        market_status = MARKET_STATUS_STALE_SNAPSHOT
    else:
        # Determine live vs partial: if all index+macro quotes have live sources
        all_quotes = (
            list(briefing.market_setup.index_quotes)
            + list(briefing.market_setup.macro_quotes)
        )
        stale_sources = {"yfinance_history_fallback", "prior_close"}
        live_sources = {"finnhub", "alpaca", "yfinance", "yfinance_live"}
        stale_count = sum(
            1 for q in all_quotes
            if (q.source or "").lower().startswith("stale_snapshot")
            or (q.source or "").lower() in stale_sources
        )
        live_count = sum(
            1 for q in all_quotes
            if (q.source or "").lower() in live_sources
        )
        if all_quotes and live_count == 0 and stale_count > 0:
            market_status = MARKET_STATUS_STALE_SNAPSHOT
        elif all_quotes and stale_count > 0:
            market_status = MARKET_STATUS_PARTIAL
        elif not all_quotes:
            market_status = MARKET_STATUS_UNAVAILABLE
        else:
            market_status = MARKET_STATUS_LIVE

    # --- News status ---
    fresh_count, fresh_mat = _count_fresh_news(briefing)
    if briefing.news_data_outage:
        news_status = NEWS_STATUS_PROVIDER_OUTAGE
    elif fresh_count == 0 and briefing.news_raw_fetched == 0:
        news_status = NEWS_STATUS_EMPTY
    elif fresh_count == 0:
        news_status = NEWS_STATUS_STALE
    else:
        news_status = NEWS_STATUS_FRESH

    briefing.market_data_status = market_status
    briefing.news_status = news_status
    briefing.fresh_news_count = fresh_count
    briefing.fresh_news_materiality_score = fresh_mat
    if getattr(briefing, "stale_snapshot_used", False):
        briefing.last_valid_market_snapshot_session = getattr(briefing, "stale_snapshot_session", "") or ""
        briefing.last_valid_market_snapshot_time = getattr(briefing, "stale_snapshot_time", "") or ""
    return briefing


def make_send_decision(
    briefing: MorningBriefing,
    *,
    is_scheduled: bool = True,
    is_dry_run: bool = False,
) -> SendDecision:
    """Compute the send decision for a briefing.

    Parameters
    ----------
    briefing:
        The fully built MorningBriefing. classify_briefing_statuses() will be
        called here to ensure statuses are populated.
    is_scheduled:
        True when triggered by the scheduler (live delivery path).
        False for manual/preview runs.
    is_dry_run:
        If True, always render degraded_context; never suppress delivery.
    """
    # Classify statuses if not already done (market_data_status default is "live").
    # If the caller pre-classified (e.g. via a prior classify_briefing_statuses call or
    # by directly setting fields in tests), this call will recompute from quotes/news.
    # To avoid overwriting pre-set values in tests, we skip re-classification when
    # fresh_news_count has been explicitly set to a non-zero value or when
    # market_data_status has been set to a non-default non-live value.
    _preclassified = (
        briefing.fresh_news_count > 0
        or briefing.market_data_status not in ("live", "")
        or briefing.news_status not in ("fresh", "")
    )
    if not _preclassified:
        classify_briefing_statuses(briefing)
    market_status = briefing.market_data_status
    news_status = briefing.news_status
    fresh_count = briefing.fresh_news_count
    fresh_mat = briefing.fresh_news_materiality_score

    # Dry-run/preview: always render, never suppress
    if is_dry_run:
        if market_status in (MARKET_STATUS_STALE_SNAPSHOT, MARKET_STATUS_UNAVAILABLE):
            mode = BRIEFING_MODE_DEGRADED_CONTEXT
        else:
            mode = BRIEFING_MODE_NORMAL
        return SendDecision(mode=mode, should_send=True, suppress_reason="", log_label=f"dry_run_{mode}")

    # Rule 1: live/partial market + normal briefing (default path)
    if market_status in (MARKET_STATUS_LIVE, MARKET_STATUS_PARTIAL):
        if news_status == NEWS_STATUS_FRESH and fresh_count >= _NEWS_FRESH_COUNT_MIN:
            return SendDecision(mode=BRIEFING_MODE_NORMAL, should_send=True, suppress_reason="", log_label="sent_normal")
        # Rule 2: live market + no fresh news -> market_only if scheduled, else normal
        if is_scheduled:
            return SendDecision(mode=BRIEFING_MODE_MARKET_ONLY, should_send=True, suppress_reason="", log_label="sent_market_only")
        return SendDecision(mode=BRIEFING_MODE_NORMAL, should_send=True, suppress_reason="", log_label="sent_normal_no_fresh_news")

    # Rules 3 & 4: stale_snapshot or unavailable market
    has_material_news = (
        news_status == NEWS_STATUS_FRESH
        and fresh_count >= _NEWS_FRESH_COUNT_MIN
        and fresh_mat >= _NEWS_FRESH_MATERIALITY_THRESHOLD
    )

    if has_material_news:
        # Rule 3: stale market + material news -> news_only or degraded_context
        if market_status == MARKET_STATUS_STALE_SNAPSHOT:
            mode = BRIEFING_MODE_DEGRADED_CONTEXT
        else:
            mode = BRIEFING_MODE_NEWS_ONLY
        if mode == BRIEFING_MODE_NEWS_ONLY:
            # Suppress stale market analysis to avoid misleading with stale data
            briefing.market_setup_analysis = ""
        return SendDecision(mode=mode, should_send=True, suppress_reason="", log_label=f"sent_{mode}")

    # Rule 4: stale/unavailable market + no material news -> suppress
    if is_scheduled:
        reason = f"market_status={market_status}, news_status={news_status}, fresh_news_count={fresh_count}"
        briefing.briefing_mode = BRIEFING_MODE_SUPPRESSED
        briefing.suppress_reason = reason
        return SendDecision(
            mode=BRIEFING_MODE_SUPPRESSED,
            should_send=False,
            suppress_reason=reason,
            log_label="skipped_degraded_no_fresh_data",
        )
    # Non-scheduled: render degraded_context for inspection
    return SendDecision(mode=BRIEFING_MODE_DEGRADED_CONTEXT, should_send=True, suppress_reason="", log_label="preview_degraded_context")


def _count_fresh_news(briefing: MorningBriefing) -> tuple[int, float]:
    """Return (fresh_count, materiality_score) for news that is not duplicate/stale."""
    all_events = (
        list(briefing.global_news)
        + list(briefing.top_themes)
        + list(briefing.watchlist_events)
        + list(briefing.portfolio_focus)
    )
    seen: set[str] = set()
    fresh_count = 0
    mat_score = 0.0
    for evt in all_events:
        key = evt.cluster_id or evt.content_hash or evt.event_id
        if key in seen:
            continue
        seen.add(key)
        if evt.already_sent:
            continue
        if evt.update_status not in {"new", "updated", ""}:
            continue
        fresh_count += 1
        mat_score += float(evt.final_score or 0.0)
    return fresh_count, round(mat_score, 3)
