"""Primary sources service: aggregates all no-key primary-source providers.

Instantiated by NewsDataService when enable_primary_sources=True. All three
providers (Fed, EDGAR earnings, BoE) require only a User-Agent string; no API
key purchases are needed.
"""

from __future__ import annotations

from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.sources.primary.boe import BoEProvider
from app.sources.primary.edgar_earnings import EarningsReleaseProvider
from app.sources.primary.fed import FedPressReleaseProvider

logger = get_logger("primary.service")


class PrimarySourcesService:
    """Aggregates Federal Reserve, SEC EDGAR earnings, and Bank of England providers."""

    def __init__(self, settings: Settings):
        ua = settings.sec_user_agent
        timeout = settings.provider_timeout
        self.fed = FedPressReleaseProvider(user_agent=ua, timeout=timeout)
        self.edgar = EarningsReleaseProvider(user_agent=ua, timeout=timeout)
        self.boe = BoEProvider(user_agent=ua, timeout=timeout)
        self._enabled = settings.enable_primary_sources

    def is_available(self) -> bool:
        return self._enabled and (
            self.fed.is_configured()
            or self.edgar.is_configured()
            or self.boe.is_configured()
        )

    def fetch_central_bank_decisions(self, days_back: int = 7) -> list[NormalisedEvent]:
        if not self._enabled:
            return []
        events: list[NormalisedEvent] = []
        events.extend(self.fed.fetch_recent_releases(days_back=days_back))
        events.extend(self.boe.fetch_recent_decisions(days_back=days_back))
        logger.info("Primary: %d central bank events", len(events))
        return events

    def fetch_earnings_releases(
        self,
        tickers: list[str],
        days_back: int = 2,
    ) -> list[NormalisedEvent]:
        if not self._enabled or not tickers:
            return []
        return self.edgar.fetch_earnings_releases(tickers=tickers, days_back=days_back)

    def fetch_all(self, watchlist: list[str] | None = None) -> list[NormalisedEvent]:
        if not self._enabled:
            return []
        events: list[NormalisedEvent] = []
        events.extend(self.fetch_central_bank_decisions())
        events.extend(self.fetch_earnings_releases(tickers=watchlist or []))
        logger.info("Primary sources total: %d events", len(events))
        return events
