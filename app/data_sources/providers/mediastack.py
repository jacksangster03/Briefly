"""Mediastack provider: low-volume global business headline backup."""

from __future__ import annotations

from datetime import datetime

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("mediastack")


class MediastackProvider(BaseProvider):
    name = "mediastack"

    def __init__(self, api_key: str, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.base_url = base_url

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def get_market_news(
        self,
        *,
        limit: int = 25,
        countries: str = "us,gb",
        languages: str = "en",
    ) -> list[NormalisedEvent]:
        """Fetch business headlines from Mediastack."""
        try:
            data = self._get(
                self.base_url,
                params={
                    "access_key": self.api_key,
                    "categories": "business",
                    "countries": countries,
                    "languages": languages,
                    "sort": "published_desc",
                    "limit": max(1, min(limit, 100)),
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch Mediastack news")
            return []

        rows = data.get("data", []) if isinstance(data, dict) else []
        events: list[NormalisedEvent] = []

        for row in rows:
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            if not title or not url:
                continue

            evt = NormalisedEvent(
                source="mediastack",
                source_type="news",
                published_at=self._parse_timestamp(row.get("published_at", "")),
                title=title,
                summary=(row.get("description") or "").strip(),
                url=url,
                event_type="headline",
                factual_confidence_score=0.52,
                raw_data={
                    "source_name": row.get("source", ""),
                    "provider_event_id": row.get("url", ""),
                    "country": row.get("country", ""),
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d news items from Mediastack", len(events))
        return events

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
