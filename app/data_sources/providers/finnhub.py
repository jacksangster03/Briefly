"""Finnhub provider: quotes, company news, market news, earnings calendar.

Free tier: 60 API calls/minute.  Docs: https://finnhub.io/docs/api
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import EarningsEvent, NormalisedEvent, QuoteData

logger = get_logger("finnhub")

BASE_URL = "https://finnhub.io/api/v1"


class FinnhubProvider(BaseProvider):
    name = "finnhub"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _params(self, extra: dict | None = None) -> dict:
        p = {"token": self.api_key}
        if extra:
            p.update(extra)
        return p

    # -- Quotes ---------------------------------------------------------------

    def get_quote(self, symbol: str) -> QuoteData | None:
        """Fetch a real-time quote for a single symbol."""
        try:
            data = self._get(f"{BASE_URL}/quote", params=self._params({"symbol": symbol}))
        except ProviderError:
            logger.warning("Failed to fetch quote for %s", symbol)
            return None

        if not data or data.get("c", 0) == 0:
            return None

        return QuoteData(
            symbol=symbol,
            current_price=data["c"],
            change=data["d"] or 0.0,
            change_percent=data["dp"] or 0.0,
            high=data.get("h", 0.0),
            low=data.get("l", 0.0),
            open=data.get("o", 0.0),
            previous_close=data.get("pc", 0.0),
            timestamp=datetime.fromtimestamp(data.get("t", 0), tz=timezone.utc),
            source="finnhub",
        )

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
        """Fetch quotes for a list of symbols. Skips failures."""
        results = []
        for sym in symbols:
            quote = self.get_quote(sym)
            if quote:
                results.append(quote)
        return results

    # -- Market news ----------------------------------------------------------

    def get_market_news(self, category: str = "general", min_id: int = 0) -> list[NormalisedEvent]:
        """Fetch general market news headlines."""
        try:
            data = self._get(
                f"{BASE_URL}/news",
                params=self._params({"category": category, "minId": min_id}),
            )
        except ProviderError:
            logger.warning("Failed to fetch market news")
            return []

        if not isinstance(data, list):
            return []

        events = []
        for item in data:
            evt = NormalisedEvent(
                source="finnhub",
                source_type="news",
                published_at=datetime.fromtimestamp(item.get("datetime", 0), tz=timezone.utc),
                title=item.get("headline", ""),
                summary=item.get("summary", ""),
                url=item.get("url", ""),
                tickers=self._extract_tickers(item),
                event_type="market_news",
                raw_data=item,
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d market news items from Finnhub", len(events))
        return events

    # -- Company news ---------------------------------------------------------

    def get_company_news(
        self, symbol: str, days_back: int = 1
    ) -> list[NormalisedEvent]:
        """Fetch news for a specific company."""
        today = datetime.now(timezone.utc).date()
        from_date = today - timedelta(days=days_back)

        try:
            data = self._get(
                f"{BASE_URL}/company-news",
                params=self._params({
                    "symbol": symbol,
                    "from": from_date.isoformat(),
                    "to": today.isoformat(),
                }),
            )
        except ProviderError:
            logger.warning("Failed to fetch company news for %s", symbol)
            return []

        if not isinstance(data, list):
            return []

        events = []
        for item in data:
            evt = NormalisedEvent(
                source="finnhub",
                source_type="news",
                published_at=datetime.fromtimestamp(item.get("datetime", 0), tz=timezone.utc),
                title=item.get("headline", ""),
                summary=item.get("summary", ""),
                url=item.get("url", ""),
                tickers=[symbol],
                event_type="company_news",
                raw_data=item,
            )
            evt.compute_hash()
            events.append(evt)

        return events

    # -- Earnings calendar ----------------------------------------------------

    def get_earnings_calendar(self, days_ahead: int = 7) -> list[EarningsEvent]:
        """Fetch upcoming earnings for the next N days."""
        today = datetime.now(timezone.utc).date()
        to_date = today + timedelta(days=days_ahead)

        try:
            data = self._get(
                f"{BASE_URL}/calendar/earnings",
                params=self._params({
                    "from": today.isoformat(),
                    "to": to_date.isoformat(),
                }),
            )
        except ProviderError:
            logger.warning("Failed to fetch earnings calendar")
            return []

        calendar = data.get("earningsCalendar", []) if isinstance(data, dict) else []
        events = []
        for item in calendar:
            events.append(EarningsEvent(
                symbol=item.get("symbol", ""),
                report_date=item.get("date", ""),
                fiscal_quarter=f"Q{item.get('quarter', '?')} {item.get('year', '')}",
                eps_estimate=item.get("epsEstimate"),
                eps_actual=item.get("epsActual"),
                revenue_estimate=item.get("revenueEstimate"),
                revenue_actual=item.get("revenueActual"),
                time=item.get("hour", ""),
                source="finnhub",
            ))

        logger.info("Fetched %d earnings events from Finnhub", len(events))
        return events

    # -- Company profile ------------------------------------------------------

    def get_company_profile(self, symbol: str) -> dict | None:
        """Fetch basic company profile (name, sector, market cap, etc.)."""
        try:
            data = self._get(
                f"{BASE_URL}/stock/profile2",
                params=self._params({"symbol": symbol}),
            )
            return data if data and data.get("name") else None
        except ProviderError:
            return None

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _extract_tickers(item: dict) -> list[str]:
        """Best-effort ticker extraction from a Finnhub news item."""
        related = item.get("related", "")
        if related:
            return [t.strip() for t in related.split(",") if t.strip()]
        return []
