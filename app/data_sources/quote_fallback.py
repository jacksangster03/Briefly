"""Snapshot-based fallback quote loader.

When live providers fail, this module reads the most recent prior-session
SessionArchiveSnapshot for the same profile/date and reconstructs QuoteData
objects tagged with source="stale_snapshot:<session_key>".

Fallback hierarchy used by morning_generator:
  1. live/near-real-time provider quote (handled by MarketDataService)
  2. alternate provider quote (handled by MarketDataService chain)
  3. current-day in-memory/DB quote cache (not yet implemented)
  4. latest prior session snapshot for same profile/date  <- THIS MODULE
  5. prior close (not yet implemented separately)
  6. unavailable
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from app.schemas.events import QuoteData

if TYPE_CHECKING:
    pass

logger = logging.getLogger("briefing.quote_fallback")

# Source basis constants
SOURCE_BASIS_LIVE = "live"
SOURCE_BASIS_NEAR_REAL_TIME = "near_real_time"
SOURCE_BASIS_DELAYED = "delayed"
SOURCE_BASIS_STALE_SNAPSHOT = "stale_snapshot"
SOURCE_BASIS_PRIOR_CLOSE = "prior_close"
SOURCE_BASIS_UNAVAILABLE = "unavailable"

# Fallback session order for each session key.
# The current session itself is excluded — we only look at prior sessions.
_FALLBACK_ORDER: dict[str, list[str]] = {
    "us_intraday_risk": ["us_pre_open", "europe_midday", "morning"],
    "into_close": ["us_intraday_risk", "us_pre_open", "morning"],
    "closing_wrap": ["into_close", "us_intraday_risk", "us_pre_open", "morning"],
    "europe_midday": ["morning"],
    "us_pre_open": ["europe_midday", "morning"],
    "morning": ["closing_wrap"],  # prior day closing_wrap; same date won't match normally
}


def load_snapshot_fallback_quotes(
    *,
    profile_name: str,
    session_key: str,
    local_date: date,
) -> tuple[list[QuoteData], str | None, str | None]:
    """Load fallback quotes from the most recent prior-session archive snapshot.

    Returns:
        (quotes, snapshot_session_key, snapshot_time_str)
        quotes: list of QuoteData with source="stale_snapshot:<session_key>"
        snapshot_session_key: which session the snapshot came from, or None
        snapshot_time_str: human-readable time like "13:34" or None
    """
    try:
        from app.db.models import SessionArchiveSnapshot
        from app.db.session import get_session
    except Exception as exc:
        logger.debug("quote_fallback: DB import failed: %s", exc)
        return [], None, None

    fallback_sessions = _FALLBACK_ORDER.get((session_key or "morning").lower(), [])
    if not fallback_sessions:
        return [], None, None

    try:
        with get_session() as db_session:
            for snap_session_key in fallback_sessions:
                snap = (
                    db_session.query(SessionArchiveSnapshot)
                    .filter(
                        SessionArchiveSnapshot.profile_name == profile_name,
                        SessionArchiveSnapshot.local_date == local_date,
                        SessionArchiveSnapshot.session_key == snap_session_key,
                    )
                    .order_by(SessionArchiveSnapshot.id.desc())
                    .first()
                )
                if snap is None:
                    continue
                market_summary = snap.market_summary_json
                if not market_summary:
                    continue

                # Determine human-readable time string from snapshot
                generated_at = snap.generated_at_utc
                snapshot_time_str: str | None = None
                if generated_at is not None:
                    # Use local time string if available, otherwise derive from UTC
                    local_str = getattr(snap, "generated_at_local_str", None)
                    if local_str:
                        # "2026-05-06 10:29" -> "10:29"
                        parts = str(local_str).strip().split()
                        if len(parts) >= 2:
                            snapshot_time_str = parts[1][:5]
                    if not snapshot_time_str:
                        tz_name = getattr(snap, "timezone_name", None) or "Europe/Madrid"
                        try:
                            from zoneinfo import ZoneInfo
                            local_dt = generated_at.astimezone(ZoneInfo(tz_name))
                        except Exception:
                            local_dt = generated_at
                        snapshot_time_str = local_dt.strftime("%H:%M")

                # Convert market_summary_json rows to QuoteData objects
                quotes: list[QuoteData] = []
                source_tag = f"stale_snapshot:{snap_session_key}"
                for row in market_summary:
                    try:
                        symbol = str(row.get("symbol") or "").strip()
                        display_name = str(row.get("display_name") or symbol).strip()
                        price = row.get("price")
                        change_pct = row.get("change_pct")
                        if not symbol or price is None:
                            continue
                        q = QuoteData(
                            symbol=symbol,
                            display_name=display_name,
                            current_price=float(price),
                            change_percent=float(change_pct) if change_pct is not None else None,
                            source=source_tag,
                            timestamp=generated_at if generated_at is not None else datetime.now(timezone.utc),
                        )
                        quotes.append(q)
                    except Exception as row_exc:
                        logger.debug("quote_fallback: skipping malformed row %s: %s", row, row_exc)
                        continue

                if quotes:
                    logger.info(
                        "quote_fallback: loaded %d quotes from %s snapshot (time=%s)",
                        len(quotes),
                        snap_session_key,
                        snapshot_time_str,
                    )
                    return quotes, snap_session_key, snapshot_time_str

    except Exception as exc:
        logger.exception("quote_fallback: unexpected error loading fallback quotes: %s", exc)

    return [], None, None


def source_basis_for_quote(quote: QuoteData) -> str:
    """Return the source_basis string for a quote."""
    src = (quote.source or "").lower()
    if src.startswith("stale_snapshot"):
        return SOURCE_BASIS_STALE_SNAPSHOT
    if src in {"finnhub", "alpaca"}:
        return SOURCE_BASIS_LIVE
    if src in {"yfinance", "yfinance_live"}:
        return SOURCE_BASIS_NEAR_REAL_TIME
    if src == "yfinance_history_fallback":
        return SOURCE_BASIS_PRIOR_CLOSE
    return SOURCE_BASIS_LIVE
