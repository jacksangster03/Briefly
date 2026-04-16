"""Alpha Vantage NEWS_SENTIMENT provider."""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("alpha_vantage_news")


class AlphaVantageNewsProvider(BaseProvider):
    name = "alpha_vantage_news"

    def __init__(self, api_key: str, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.base_url = base_url

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def get_market_news(
        self,
        *,
        topics: str,
        limit: int = 50,
    ) -> list[NormalisedEvent]:
        """Fetch topic-filtered global finance headlines."""
        try:
            data = self._get(
                self.base_url,
                params={
                    "function": "NEWS_SENTIMENT",
                    "topics": topics,
                    "limit": max(1, min(limit, 1000)),
                    "sort": "LATEST",
                    "apikey": self.api_key,
                },
            )
        except ProviderError:
            logger.warning("Failed to fetch Alpha Vantage NEWS_SENTIMENT")
            return []

        feed = data.get("feed", []) if isinstance(data, dict) else []
        events: list[NormalisedEvent] = []

        for item in feed:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue

            ticker_sentiment = item.get("ticker_sentiment") or []
            tickers = [
                str(entry.get("ticker", "")).upper()
                for entry in ticker_sentiment
                if entry.get("ticker")
            ]
            score = self._parse_float(item.get("overall_sentiment_score", 0.0))
            evt = NormalisedEvent(
                source="alpha_vantage",
                source_type="news",
                published_at=self._parse_timestamp(item.get("time_published", "")),
                title=title,
                summary=(item.get("summary") or "").strip(),
                url=url,
                tickers=tickers,
                event_type="news_sentiment",
                sentiment=max(-1.0, min(score, 1.0)),
                factual_confidence_score=0.62,
                raw_data={
                    "source_name": item.get("source", ""),
                    "provider_event_id": item.get("url", ""),
                    "topics": topics,
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d news items from Alpha Vantage", len(events))
        return events

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        # Common shape: YYYYMMDDTHHMMSS
        for pattern in ("%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                parsed = datetime.strptime(value, pattern)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
