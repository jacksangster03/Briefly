"""Marketaux provider: finance-focused global market headlines."""

from __future__ import annotations

from datetime import datetime

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("marketaux")


class MarketauxProvider(BaseProvider):
    name = "marketaux"

    def __init__(self, api_key: str, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.base_url = base_url

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def get_market_news(
        self,
        *,
        limit: int = 50,
        language: str = "en",
    ) -> list[NormalisedEvent]:
        """Fetch latest market headlines from Marketaux."""
        try:
            data = self._get(
                self.base_url,
                params={
                    "api_token": self.api_key,
                    "language": language,
                    "limit": max(1, min(limit, 100)),
                    "sort": "published_desc",
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch Marketaux news")
            return []

        rows = data.get("data", []) if isinstance(data, dict) else []
        events: list[NormalisedEvent] = []
        for row in rows:
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            if not title or not url:
                continue

            tickers = self._extract_tickers(row)
            evt = NormalisedEvent(
                source="marketaux",
                source_type="news",
                published_at=self._parse_timestamp(row.get("published_at", "")),
                title=title,
                summary=(row.get("description") or row.get("snippet") or "").strip(),
                url=url,
                tickers=tickers,
                event_type="market_news",
                factual_confidence_score=0.63,
                raw_data={
                    "source_name": self._extract_source_name(row),
                    "provider_event_id": row.get("uuid") or row.get("id") or url,
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d news items from Marketaux", len(events))
        return events

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _extract_source_name(row: dict) -> str:
        source = row.get("source")
        if isinstance(source, dict):
            return str(source.get("name") or source.get("domain") or "").strip()
        if isinstance(source, str):
            return source.strip()
        return str(row.get("source_name") or "").strip()

    @staticmethod
    def _extract_tickers(row: dict) -> list[str]:
        entities = row.get("entities")
        if not isinstance(entities, list):
            return []
        out: list[str] = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            symbol = str(entity.get("symbol") or "").strip().upper()
            if symbol and symbol not in out:
                out.append(symbol)
        return out
