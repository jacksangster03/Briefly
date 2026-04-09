"""Market data service: aggregates quotes across providers with fallback."""

from __future__ import annotations

from app.data_sources.providers.finnhub import FinnhubProvider
from app.data_sources.providers.yfinance_provider import YFinanceProvider
from app.logger import get_logger
from app.schemas.events import QuoteData
from app.settings import Settings

logger = get_logger("market_data")


class MarketDataService:
    """Fetches quotes from Finnhub (primary) with yfinance fallback."""

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
        self.yfinance = YFinanceProvider(
            timeout=settings.provider_timeout,
            max_retries=1,
        )

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
        """Fetch quotes for symbols, falling back provider-by-provider."""
        results: dict[str, QuoteData] = {}

        # Try Finnhub first
        if self.finnhub and self.finnhub.is_configured():
            for quote in self.finnhub.get_quotes(symbols):
                results[quote.symbol] = quote

        # Fill gaps with yfinance
        missing = [s for s in symbols if s not in results]
        if missing and self.yfinance.is_configured():
            logger.info("Falling back to yfinance for %d symbols", len(missing))
            for quote in self.yfinance.get_quotes(missing):
                results[quote.symbol] = quote

        fetched = len(results)
        logger.info("Market data: %d/%d symbols fetched", fetched, len(symbols))
        return list(results.values())

    def get_quote(self, symbol: str) -> QuoteData | None:
        """Single-symbol convenience wrapper."""
        quotes = self.get_quotes([symbol])
        return quotes[0] if quotes else None
