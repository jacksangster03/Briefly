"""Tests for market-data quote fallback behavior."""

from __future__ import annotations

from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.data_sources.providers.finnhub import FinnhubProvider
from app.schemas.events import QuoteData
from app.settings import Settings


def test_market_data_uses_tighter_timeout_for_quote_provider():
    settings = Settings(
        finnhub_api_key="test-key",
        provider_timeout=30,
        provider_max_retries=2,
    )
    service = MarketDataService(settings)
    assert service.finnhub is not None
    assert service.finnhub.timeout == 12
    assert service.finnhub.max_retries == 1


def test_market_data_respects_lower_timeout_floor():
    settings = Settings(
        finnhub_api_key="test-key",
        provider_timeout=7,
        provider_max_retries=2,
    )
    service = MarketDataService(settings)
    assert service.finnhub is not None
    assert service.finnhub.timeout == 7


def test_finnhub_get_quotes_fails_fast_on_batch_timeouts():
    provider = FinnhubProvider(api_key="test-key", timeout=30, max_retries=2)
    calls: list[str] = []

    def _always_fail(symbol: str):
        calls.append(symbol)
        return None

    provider.get_quote = _always_fail  # type: ignore[assignment]
    quotes = provider.get_quotes(["SPY", "QQQ", "DIA", "IWM"])
    assert quotes == []
    # Batch should abort quickly instead of timing out every symbol.
    assert calls == ["SPY", "QQQ"]


def test_finnhub_get_quotes_does_not_abort_after_initial_success():
    provider = FinnhubProvider(api_key="test-key", timeout=30, max_retries=2)
    calls: list[str] = []

    def _first_only(symbol: str):
        calls.append(symbol)
        if symbol == "SPY":
            return QuoteData(symbol="SPY", current_price=500.0)
        return None

    provider.get_quote = _first_only  # type: ignore[assignment]
    quotes = provider.get_quotes(["SPY", "QQQ", "DIA", "IWM"])
    assert len(quotes) == 1
    assert quotes[0].symbol == "SPY"
    # Once we already have a hit, keep scanning rest of batch.
    assert calls == ["SPY", "QQQ", "DIA", "IWM"]


def test_news_data_tightens_finnhub_timeout_and_retries():
    """Finnhub /news has been unstable; the service must cap per-call wait
    so a manual run never stalls ~2 minutes before NewsAPI fallback runs.
    """
    settings = Settings(
        finnhub_api_key="test-key",
        provider_timeout=30,
        provider_max_retries=2,
    )
    service = NewsDataService(settings)
    assert service.finnhub is not None
    assert service.finnhub.timeout == 15
    assert service.finnhub.max_retries == 1


def test_news_data_respects_low_timeout_floor():
    settings = Settings(
        finnhub_api_key="test-key",
        provider_timeout=8,
        provider_max_retries=2,
    )
    service = NewsDataService(settings)
    assert service.finnhub is not None
    # Floor keeps a minimum budget for a single round-trip to succeed.
    assert service.finnhub.timeout == 10
    assert service.finnhub.max_retries == 1


def test_fetch_company_news_fails_fast_on_batch_empties():
    """A dead Finnhub /company-news endpoint must not burn through 20
    tickers worth of timeout budget before falling back.
    """
    settings = Settings(finnhub_api_key="test-key")
    service = NewsDataService(settings)
    assert service.finnhub is not None

    calls: list[str] = []

    def _always_empty(symbol: str, days_back: int = 1):
        calls.append(symbol)
        return []

    service.finnhub.get_company_news = _always_empty  # type: ignore[assignment]
    service.fetch_company_news(["AAPL", "MSFT", "NVDA", "AMZN"])
    # Batch should abort after two consecutive empties with no prior hit.
    assert calls == ["AAPL", "MSFT"]


def test_fetch_company_news_keeps_going_once_any_ticker_hits():
    """A single hit unblocks the rest of the batch so genuinely quiet
    tickers don't cause premature truncation.
    """
    settings = Settings(finnhub_api_key="test-key")
    service = NewsDataService(settings)
    assert service.finnhub is not None

    from app.schemas.events import NormalisedEvent

    calls: list[str] = []

    def _first_ticker_only(symbol: str, days_back: int = 1):
        calls.append(symbol)
        if symbol == "AAPL":
            return [NormalisedEvent(source="finnhub", title="hit")]
        return []

    service.finnhub.get_company_news = _first_ticker_only  # type: ignore[assignment]
    service.fetch_company_news(["AAPL", "MSFT", "NVDA", "AMZN"])
    # AAPL hit → never abort; scan full batch.
    assert calls == ["AAPL", "MSFT", "NVDA", "AMZN"]
