"""News data service: aggregates news events from multiple providers."""

from __future__ import annotations

from app.data_sources.providers.finnhub import FinnhubProvider
from app.data_sources.providers.newsapi import NewsAPIProvider
from app.data_sources.providers.sec_provider import SECProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.settings import Settings

logger = get_logger("news_data")


class NewsDataService:
    """Aggregates news from Finnhub, NewsAPI, and SEC EDGAR."""

    def __init__(self, settings: Settings):
        self.finnhub = (
            FinnhubProvider(
                api_key=settings.finnhub_api_key,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.finnhub_configured
            else None
        )
        self.newsapi = (
            NewsAPIProvider(
                api_key=settings.newsapi_key,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.newsapi_configured
            else None
        )
        self.sec = (
            SECProvider(
                user_agent=settings.sec_user_agent,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.sec_user_agent
            else None
        )

    def fetch_market_news(self) -> list[NormalisedEvent]:
        """Fetch general market news from all configured providers."""
        all_events: list[NormalisedEvent] = []

        if self.finnhub and self.finnhub.is_configured():
            events = self.finnhub.get_market_news()
            all_events.extend(events)
            logger.info("Finnhub market news: %d items", len(events))

        if self.newsapi and self.newsapi.is_configured():
            events = self.newsapi.get_top_headlines()
            all_events.extend(events)
            logger.info("NewsAPI headlines: %d items", len(events))

        logger.info("Total market news: %d items", len(all_events))
        return all_events

    def fetch_company_news(self, symbols: list[str], days_back: int = 1) -> list[NormalisedEvent]:
        """Fetch company-specific news for a list of tickers."""
        all_events: list[NormalisedEvent] = []

        if self.finnhub and self.finnhub.is_configured():
            for symbol in symbols:
                events = self.finnhub.get_company_news(symbol, days_back=days_back)
                all_events.extend(events)

        logger.info("Company news for %d tickers: %d items", len(symbols), len(all_events))
        return all_events

    def fetch_filings(self, tickers: list[str] | None = None) -> list[NormalisedEvent]:
        """Fetch recent SEC filings, optionally filtered by tickers."""
        if not self.sec or not self.sec.is_configured():
            return []

        events = self.sec.search_filings(tickers=tickers)
        logger.info("SEC filings: %d items", len(events))
        return events

    def fetch_all(self, watchlist: list[str] | None = None) -> list[NormalisedEvent]:
        """Fetch all available news, filings, and headlines."""
        all_events: list[NormalisedEvent] = []

        all_events.extend(self.fetch_market_news())
        if watchlist:
            all_events.extend(self.fetch_company_news(watchlist))
        all_events.extend(self.fetch_filings(tickers=watchlist))

        logger.info("Total events from all sources: %d", len(all_events))
        return all_events
