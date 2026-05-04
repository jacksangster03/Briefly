"""Market data service: Finnhub (real-time quotes) → Alpaca (history + quote fallback) → yfinance (last resort)."""

from __future__ import annotations

from app.data_sources.providers.finnhub import FinnhubProvider
from app.data_sources.providers.yfinance_provider import YFinanceProvider
from app.logger import get_logger
from app.schemas.events import PricePoint, QuoteData
from app.settings import Settings

logger = get_logger("market_data")

# Finnhub /quote is strongest on listed equity symbols.
# Skip Yahoo-specific and macro-style symbols there to avoid false
# "endpoint unhealthy" signals and rely on fallbacks for those.
_FINNHUB_QUOTE_BLACKLIST_PREFIXES = ("DGS", "T10Y", "DTWEX", "ECB_", "EUROSTAT_")


class MarketDataService:
    """Aggregates market data with a three-tier fallback chain.

    Quote priority:   Finnhub → Alpaca → yfinance
    History priority: Alpaca → yfinance
    """

    def __init__(self, settings: Settings):
        quote_timeout = max(5, min(settings.provider_timeout, 12))

        self.finnhub = (
            FinnhubProvider(
                api_key=settings.finnhub_api_key,
                timeout=quote_timeout,
                max_retries=1,
            )
            if settings.finnhub_configured
            else None
        )

        self.alpaca = None
        if settings.alpaca_configured:
            from app.data_sources.providers.alpaca import AlpacaProvider
            self.alpaca = AlpacaProvider(
                api_key=settings.alpaca_api_key,
                api_secret=settings.alpaca_api_secret,
                timeout=settings.provider_timeout,
                max_retries=1,
            )

        self.yfinance = YFinanceProvider(
            timeout=settings.provider_timeout,
            max_retries=1,
        )

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
        """Fetch quotes: Finnhub primary, Alpaca secondary, yfinance tertiary."""
        results: dict[str, QuoteData] = {}

        if self.finnhub and self.finnhub.is_configured():
            finnhub_symbols = [sym for sym in symbols if self._is_finnhub_quote_symbol(sym)]
            if finnhub_symbols:
                for quote in self.finnhub.get_quotes(finnhub_symbols):
                    results[quote.symbol] = quote
            skipped = len(symbols) - len(finnhub_symbols)
            if skipped > 0:
                logger.info("Skipping Finnhub for %d non-equity/index-formatted symbols", skipped)

        missing = [s for s in symbols if s not in results]
        if missing and self.alpaca and self.alpaca.is_configured():
            logger.info("Falling back to Alpaca for %d symbols", len(missing))
            for quote in self.alpaca.get_quotes(missing):
                results[quote.symbol] = quote

        still_missing = [s for s in symbols if s not in results]
        if still_missing and self.yfinance.is_configured():
            logger.info("Falling back to yfinance for %d symbols", len(still_missing))
            for quote in self.yfinance.get_quotes(still_missing):
                results[quote.symbol] = quote

        logger.info("Market data: %d/%d symbols fetched", len(results), len(symbols))
        return list(results.values())

    def get_quote(self, symbol: str) -> QuoteData | None:
        quotes = self.get_quotes([symbol])
        return quotes[0] if quotes else None

    def get_price_history(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> list[PricePoint]:
        """Fetch historical OHLCV: Alpaca primary, yfinance fallback."""
        if self.alpaca and self.alpaca.is_configured():
            history = self.alpaca.get_price_history(symbol, period=period, interval=interval)
            if history:
                return history
            logger.info("Alpaca history empty for %s; falling back to yfinance", symbol)

        if self.yfinance.is_configured():
            return self.yfinance.get_price_history(symbol, period=period, interval=interval)

        return []

    @staticmethod
    def _is_finnhub_quote_symbol(symbol: str) -> bool:
        sym = (symbol or "").strip().upper()
        if not sym:
            return False
        if sym.startswith(_FINNHUB_QUOTE_BLACKLIST_PREFIXES):
            return False
        # Yahoo-style index/futures/commodities symbols.
        if "^" in sym or "=" in sym or ":" in sym:
            return False
        return True
