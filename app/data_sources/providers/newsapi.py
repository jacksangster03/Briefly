"""NewsAPI provider: broad news headlines for enrichment.

Free tier: 100 requests/day, headlines only, 24h delay on /everything.
Docs: https://newsapi.org/docs
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("newsapi")

BASE_URL = "https://newsapi.org/v2"


class NewsAPIProvider(BaseProvider):
    name = "newsapi"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # -- Top headlines --------------------------------------------------------

    def get_top_headlines(
        self,
        category: str = "business",
        country: str = "us",
        page_size: int = 30,
    ) -> list[NormalisedEvent]:
        """Fetch top headlines from NewsAPI."""
        try:
            data = self._get(
                f"{BASE_URL}/top-headlines",
                params={
                    "apiKey": self.api_key,
                    "category": category,
                    "country": country,
                    "pageSize": page_size,
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch NewsAPI headlines")
            return []

        articles = data.get("articles", []) if isinstance(data, dict) else []
        events = []

        for article in articles:
            if article.get("title") in (None, "", "[Removed]"):
                continue

            evt = NormalisedEvent(
                source="newsapi",
                source_type="news",
                published_at=self._parse_timestamp(article.get("publishedAt", "")),
                title=article.get("title", ""),
                summary=article.get("description", "") or "",
                url=article.get("url", ""),
                event_type="headline",
                factual_confidence_score=0.55,
                raw_data={
                    "source_name": (article.get("source") or {}).get("name", ""),
                    "author": article.get("author", ""),
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d headlines from NewsAPI", len(events))
        return events

    # -- Everything search (limited on free tier) -----------------------------

    def search_news(
        self,
        query: str,
        page_size: int = 20,
        sort_by: str = "publishedAt",
    ) -> list[NormalisedEvent]:
        """Search all articles. Note: free tier has 24h delay on /everything."""
        try:
            data = self._get(
                f"{BASE_URL}/everything",
                params={
                    "apiKey": self.api_key,
                    "q": query,
                    "pageSize": page_size,
                    "sortBy": sort_by,
                    "language": "en",
                },
            )
        except ProviderError:
            logger.warning("Failed to search NewsAPI for '%s'", query)
            return []

        articles = data.get("articles", []) if isinstance(data, dict) else []
        events = []

        for article in articles:
            if article.get("title") in (None, "", "[Removed]"):
                continue

            evt = NormalisedEvent(
                source="newsapi",
                source_type="news",
                published_at=self._parse_timestamp(article.get("publishedAt", "")),
                title=article.get("title", ""),
                summary=article.get("description", "") or "",
                url=article.get("url", ""),
                event_type="news_search",
                factual_confidence_score=0.55,
                raw_data={
                    "source_name": (article.get("source") or {}).get("name", ""),
                    "author": article.get("author", ""),
                    "query": query,
                },
            )
            evt.compute_hash()
            events.append(evt)

        return events

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _parse_timestamp(ts: str) -> datetime | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
