"""IPO calendar provider.

Sources (in confidence order):
  1. Nasdaq IPO calendar (public, no key required) — estimated dates
  2. FMP IPO calendar (if FMP_API_KEY configured) — estimated dates

All dates from these sources are estimates unless confirmed by an EDGAR
424B4 or EFFECT filing. Label accordingly in IpoTerms.terms_confidence.
"""

from __future__ import annotations

import time
from datetime import datetime, date, timezone
from typing import Any

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.schemas.ipo_event import IpoEvent, IpoTerms

logger = get_logger("primary.ipo_calendar")

_NASDAQ_CAL_URL = "https://api.nasdaq.com/api/ipo/calendar"
_FMP_CAL_URL = "https://financialmodelingprep.com/api/v3/ipo_calendar"

_RATE_LIMIT_S = 1.5
_MAX_AGE_DAYS = 45  # ignore calendar entries older than this


class IpoCalendarProvider(BaseProvider):
    """Fetches IPO calendar entries from Nasdaq and/or FMP."""

    name = "ipo_calendar"

    def __init__(self, fmp_api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self._fmp_key = fmp_api_key
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "User-Agent": kwargs.get("user_agent", "Briefly jacksangster.033@gmail.com"),
        })
        self._last_req: float = 0.0

    def is_configured(self) -> bool:
        return True  # Nasdaq calendar requires no key

    def fetch_calendar(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[NormalisedEvent]:
        """Return IPO calendar NormalisedEvents. Dates from these sources
        are estimates and are labelled as such.
        """
        today = date.today()
        if date_from is None:
            date_from = today
        if date_to is None:
            from datetime import timedelta
            date_to = today + timedelta(days=30)

        events: list[NormalisedEvent] = []
        events.extend(self._fetch_nasdaq(date_from, date_to))
        if self._fmp_key:
            events.extend(self._fetch_fmp(date_from, date_to))
        logger.info("ipo_calendar: %d calendar events", len(events))
        return events

    # -- Nasdaq ---------------------------------------------------------------

    def _fetch_nasdaq(self, date_from: date, date_to: date) -> list[NormalisedEvent]:
        params = {
            "date": date_from.strftime("%Y-%m"),  # Nasdaq uses YYYY-MM
        }
        self._throttle()
        try:
            resp = self._get(_NASDAQ_CAL_URL, params=params)
        except Exception as exc:
            logger.debug("ipo_calendar: nasdaq fetch failed: %s", exc)
            return []

        entries = _extract_nasdaq_entries(resp)
        events = []
        for entry in entries:
            evt = _nasdaq_entry_to_event(entry)
            if evt:
                events.append(evt)
        return events

    # -- FMP ------------------------------------------------------------------

    def _fetch_fmp(self, date_from: date, date_to: date) -> list[NormalisedEvent]:
        params = {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "apikey": self._fmp_key,
        }
        self._throttle()
        try:
            resp = self._get(_FMP_CAL_URL, params=params)
        except Exception as exc:
            logger.debug("ipo_calendar: fmp fetch failed: %s", exc)
            return []

        if not isinstance(resp, list):
            return []
        events = []
        for entry in resp:
            evt = _fmp_entry_to_event(entry)
            if evt:
                events.append(evt)
        return events

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure helpers -------------------------------------------------------------

def _extract_nasdaq_entries(resp: Any) -> list[dict]:
    """Extract IPO entries from Nasdaq API response."""
    if not isinstance(resp, dict):
        return []
    data = resp.get("data", {})
    if not isinstance(data, dict):
        return []
    rows = data.get("upcomingTable", {}).get("rows", [])
    if not rows:
        rows = data.get("priceTable", {}).get("rows", [])
    return rows if isinstance(rows, list) else []


def _nasdaq_entry_to_event(entry: dict) -> NormalisedEvent | None:
    """Convert a Nasdaq IPO calendar row to a NormalisedEvent."""
    company_name = entry.get("companyName", "") or entry.get("name", "")
    ticker = (entry.get("proposedTickerSymbol", "") or entry.get("symbol", "")).upper().strip()
    exchange = entry.get("proposedExchange", "") or entry.get("exchange", "")
    expected_date_str = entry.get("expectedPriceDate", "") or entry.get("priceDate", "")
    price_range_str = entry.get("proposedSharePrice", "") or ""
    shares_str = entry.get("sharesOffered", "") or ""

    if not company_name:
        return None

    terms = _parse_terms(price_range_str, shares_str, ticker, exchange)

    expected_date = _parse_calendar_date(expected_date_str)
    if expected_date and _is_stale(expected_date):
        return None

    ipo_meta = IpoEvent(
        canonical_company_name=company_name,
        ipo_status="priced" if entry.get("dealStatus", "").lower() == "priced" else "public_filing",
        terms=terms,
        expected_listing_date=expected_date,
        status_confidence="low",
        terms_confidence="estimated",
        official_source_urls=[_NASDAQ_CAL_URL],
    )
    tickers = [ticker] if ticker else []

    evt = NormalisedEvent(
        source="nasdaq_ipo_calendar",
        source_type="calendar",
        published_at=datetime.now(timezone.utc),
        title=f"IPO Calendar: {company_name}" + (f" ({ticker})" if ticker else ""),
        summary=_calendar_summary(company_name, ticker, exchange, expected_date, terms),
        url=_NASDAQ_CAL_URL,
        tickers=tickers,
        event_type="ipo_calendar",
        factual_confidence_score=0.65,
        importance_score=0.55,
        raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
    )
    evt.compute_hash()
    return evt


def _fmp_entry_to_event(entry: dict) -> NormalisedEvent | None:
    """Convert an FMP IPO calendar entry to a NormalisedEvent."""
    company_name = entry.get("company", "")
    ticker = (entry.get("symbol", "") or "").upper().strip()
    exchange = entry.get("exchange", "")
    expected_date_str = entry.get("date", "")
    price_low = _safe_float(entry.get("priceRangeLow"))
    price_high = _safe_float(entry.get("priceRangeHigh"))
    shares_str = str(entry.get("shares", "") or "")

    if not company_name:
        return None

    expected_date = _parse_calendar_date(expected_date_str)
    if expected_date and _is_stale(expected_date):
        return None

    terms = IpoTerms(
        price_range_low=price_low,
        price_range_high=price_high,
        proposed_exchange=exchange or None,
        proposed_ticker=ticker or None,
        terms_confidence="estimated",
    )

    ipo_meta = IpoEvent(
        canonical_company_name=company_name,
        ipo_status="public_filing",
        terms=terms,
        expected_listing_date=expected_date,
        status_confidence="low",
        terms_confidence="estimated",
        official_source_urls=[_FMP_CAL_URL],
    )
    tickers = [ticker] if ticker else []

    evt = NormalisedEvent(
        source="fmp_ipo_calendar",
        source_type="calendar",
        published_at=datetime.now(timezone.utc),
        title=f"IPO Calendar: {company_name}" + (f" ({ticker})" if ticker else ""),
        summary=_calendar_summary(company_name, ticker, exchange, expected_date, terms),
        url=_FMP_CAL_URL,
        tickers=tickers,
        event_type="ipo_calendar",
        factual_confidence_score=0.65,
        importance_score=0.55,
        raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
    )
    evt.compute_hash()
    return evt


def _parse_terms(price_range_str: str, shares_str: str, ticker: str, exchange: str) -> IpoTerms:
    import re
    terms = IpoTerms(
        proposed_ticker=ticker or None,
        proposed_exchange=exchange or None,
        terms_confidence="estimated",
    )
    if price_range_str:
        m = re.search(r"\$([\d.]+)\s*[-–]\s*\$([\d.]+)", price_range_str)
        if m:
            terms.price_range_low = float(m.group(1))
            terms.price_range_high = float(m.group(2))
        else:
            m2 = re.search(r"\$([\d.]+)", price_range_str)
            if m2:
                terms.price_range_low = float(m2.group(1))
                terms.price_range_high = float(m2.group(1))
    return terms


def _parse_calendar_date(s: str) -> date | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _is_stale(d: date) -> bool:
    """Return True if the calendar date is more than _MAX_AGE_DAYS in the past."""
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=_MAX_AGE_DAYS)
    return d < cutoff


def _calendar_summary(
    company_name: str,
    ticker: str,
    exchange: str,
    expected_date: date | None,
    terms: IpoTerms,
) -> str:
    parts = [f"{company_name}"]
    if ticker:
        parts.append(f"({ticker})")
    if exchange:
        parts.append(f"on {exchange}")
    if expected_date:
        parts.append(f"expected {expected_date.strftime('%b %d, %Y')} (estimate)")
    if terms.price_range_low and terms.price_range_high:
        parts.append(f"at ${terms.price_range_low}–${terms.price_range_high}")
    return " ".join(parts) + ". Calendar estimate only; confirm against EDGAR filings."


def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None
