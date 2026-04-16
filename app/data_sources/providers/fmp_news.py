"""Financial Modeling Prep general news provider."""

from __future__ import annotations

from datetime import datetime

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("fmp_news")


class FMPNewsProvider(BaseProvider):
    name = "fmp_news"

    def __init__(self, api_key: str, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.base_url = base_url

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def get_market_news(self, *, limit: int = 50, page: int = 0) -> list[NormalisedEvent]:
        """Fetch general/latest financial headlines from FMP."""
        try:
            data = self._get(
                self.base_url,
                params={
                    "apikey": self.api_key,
                    "limit": max(1, min(limit, 250)),
                    "page": max(0, page),
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch FMP news")
            return []

        rows = data if isinstance(data, list) else []
        events: list[NormalisedEvent] = []

        for row in rows:
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            if not title or not url:
                continue

            symbol = (row.get("symbol") or "").upper().strip()
            tickers = [symbol] if symbol else []
            evt = NormalisedEvent(
                source="fmp",
                source_type="news",
                published_at=self._parse_timestamp(row.get("publishedDate", "")),
                title=title,
                summary=(row.get("text") or row.get("content") or "").strip(),
                url=url,
                tickers=tickers,
                event_type="market_news",
                factual_confidence_score=0.55,
                raw_data={
                    "source_name": row.get("site", ""),
                    "provider_event_id": row.get("url", ""),
                    "symbol": symbol,
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d news items from FMP", len(events))
        return events

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
