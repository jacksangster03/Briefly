"""News data service: aggregates news events from multiple providers."""

from __future__ import annotations

from app.data_sources.global_news_hub import GlobalNewsHubService
from app.data_sources.providers.finnhub import FinnhubProvider
from app.data_sources.providers.newsapi import NewsAPIProvider
from app.data_sources.providers.sec_provider import SECProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.sources.primary.ipo_service import IpoIntelligenceService
from app.sources.primary.service import PrimarySourcesService

logger = get_logger("news_data")


class NewsDataService:
    """Aggregates news from core + optional global providers, SEC EDGAR, and primary sources."""

    def __init__(self, settings: Settings):
        # Finnhub's /news endpoint has been intermittently unresponsive; the
        # default 30s * 2-retry budget can stall a manual run for 2 minutes
        # before the NewsAPI/SEC fallbacks get to contribute. Bound the
        # worst-case wait to ~15s so the briefing cycle stays responsive
        # under provider instability, while keeping a floor so a single
        # slow round-trip still has room to succeed.
        news_timeout = max(10, min(settings.provider_timeout, 15))
        news_retries = 1
        self.finnhub = (
            FinnhubProvider(
                api_key=settings.finnhub_api_key,
                timeout=news_timeout,
                max_retries=news_retries,
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
        self.global_hub = GlobalNewsHubService(
            settings=settings,
            finnhub=self.finnhub,
            newsapi=self.newsapi,
        )
        self.primary = PrimarySourcesService(settings=settings)
        self.ipo = IpoIntelligenceService(settings=settings)

    def fetch_market_news(self) -> list[NormalisedEvent]:
        """Fetch general market news from all configured providers."""
        all_events = self.global_hub.fetch_market_news()
        logger.info("Total market news: %d items", len(all_events))
        return all_events

    def fetch_company_news(self, symbols: list[str], days_back: int = 1) -> list[NormalisedEvent]:
        """Fetch company-specific news for a list of tickers.

        When the Finnhub ``/company-news`` endpoint is unhealthy, every
        ticker in a 20-wide watchlist would otherwise burn a full retry
        budget before returning, turning a manual run into a multi-minute
        stall. Mirror the ``get_quotes`` pattern and abort the batch once
        two consecutive symbols come back empty without ever succeeding.
        Symbols that have already produced news stay un-penalised so a
        single slow day for one ticker does not truncate the batch.
        """
        all_events: list[NormalisedEvent] = []

        if self.finnhub and self.finnhub.is_configured():
            consecutive_empty = 0
            any_hit = False
            for symbol in symbols:
                events = self.finnhub.get_company_news(symbol, days_back=days_back)
                if events:
                    all_events.extend(events)
                    consecutive_empty = 0
                    any_hit = True
                    continue

                consecutive_empty += 1
                if not any_hit and consecutive_empty >= 2:
                    logger.warning(
                        "Finnhub company-news endpoint appears unhealthy; "
                        "aborting batch after %d consecutive misses (remaining: %d)",
                        consecutive_empty,
                        max(0, len(symbols) - symbols.index(symbol) - 1),
                    )
                    break

        logger.info("Company news for %d tickers: %d items", len(symbols), len(all_events))
        return all_events

    def fetch_filings(self, tickers: list[str] | None = None) -> list[NormalisedEvent]:
        """Fetch recent SEC filings, optionally filtered by tickers."""
        if not self.sec or not self.sec.is_configured():
            return []

        events = self.sec.search_filings(tickers=tickers)
        logger.info("SEC filings: %d items", len(events))
        return events

    def fetch_insider_trades(
        self,
        tickers: list[str] | None = None,
        days_back: int = 7,
    ) -> list[NormalisedEvent]:
        """Fetch Form 4 insider transactions, optionally filtered by tickers."""
        if not self.sec or not self.sec.is_configured():
            return []

        events = self.sec.search_insider_trades(tickers=tickers, days_back=days_back)
        insider_events = [event for event in events if event.event_type == "insider_transaction"]
        logger.info("Insider trades (Form 4): %d items", len(insider_events))
        return insider_events

    def fetch_all(self, watchlist: list[str] | None = None) -> list[NormalisedEvent]:
        """Fetch all available news, filings, and headlines."""
        all_events: list[NormalisedEvent] = []

        all_events.extend(self.fetch_market_news())
        if watchlist:
            all_events.extend(self.fetch_company_news(watchlist))
        all_events.extend(self.fetch_filings(tickers=watchlist))
        all_events.extend(self.fetch_insider_trades(tickers=watchlist))
        if self.primary.is_available():
            all_events.extend(self.primary.fetch_all(watchlist=watchlist))
        if self.ipo.is_available():
            all_events.extend(self.ipo.fetch_all(watchlist=watchlist))

        logger.info("Total events from all sources: %d", len(all_events))
        return all_events
