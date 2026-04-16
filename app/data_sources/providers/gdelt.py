"""GDELT DOC provider: broad global article recall for market-linked events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("gdelt")


class GDELTProvider(BaseProvider):
    name = "gdelt"

    def __init__(self, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def get_market_news(
        self,
        *,
        query: str,
        max_records: int = 50,
    ) -> list[NormalisedEvent]:
        """Fetch broad global articles from GDELT DOC API."""
        if not query.strip():
            return []

        try:
            data = self._get(
                self.base_url,
                params={
                    "query": query,
                    "mode": "ArtList",
                    "maxrecords": max(1, min(max_records, 250)),
                    "format": "json",
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch GDELT global news")
            return []

        articles = data.get("articles", []) if isinstance(data, dict) else []
        events: list[NormalisedEvent] = []

        for article in articles:
            title = (article.get("title") or "").strip()
            url = (article.get("url") or article.get("urlmobile") or "").strip()
            if not title or not url:
                continue

            evt = NormalisedEvent(
                source="gdelt",
                source_type="news",
                published_at=self._parse_timestamp(article.get("seendate", "")),
                title=title,
                summary=(article.get("snippet") or article.get("socialimage") or "").strip(),
                url=url,
                event_type="global_news",
                factual_confidence_score=0.58,
                raw_data={
                    "source_name": article.get("domain", ""),
                    "provider_event_id": article.get("url", ""),
                    "source_country": article.get("sourcecountry", ""),
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d global news items from GDELT", len(events))
        return events

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        # Common GDELT shape: YYYYMMDDTHHMMSSZ
        for pattern in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
