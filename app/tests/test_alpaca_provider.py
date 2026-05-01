"""Tests for the Alpaca market data provider."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.events import PricePoint, QuoteData


class TestAlpacaProvider:
    def _make_provider(self):
        from app.data_sources.providers.alpaca import AlpacaProvider
        return AlpacaProvider(api_key="test_key", api_secret="test_secret")

    def test_is_configured_with_credentials(self):
        provider = self._make_provider()
        assert provider.is_configured() is True

    def test_is_not_configured_without_credentials(self):
        from app.data_sources.providers.alpaca import AlpacaProvider
        p = AlpacaProvider(api_key="", api_secret="")
        assert p.is_configured() is False

    def test_get_quote_returns_quote_data(self):
        provider = self._make_provider()

        mock_bar_prev = MagicMock()
        mock_bar_prev.close = 148.0

        mock_bar = MagicMock()
        mock_bar.open = 149.0
        mock_bar.high = 151.0
        mock_bar.low = 148.5
        mock_bar.close = 150.45
        mock_bar.volume = 1_500_000
        mock_bar.timestamp = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)

        with patch.object(provider, "_fetch_recent_daily_bars", return_value=[mock_bar_prev, mock_bar]):
            result = provider.get_quote("AAPL")

        assert isinstance(result, QuoteData)
        assert result.symbol == "AAPL"
        assert result.current_price == pytest.approx(150.45)
        assert result.previous_close == pytest.approx(148.0)
        assert result.source == "alpaca"

    def test_get_quote_returns_none_on_failure(self):
        provider = self._make_provider()
        with patch.object(provider, "_fetch_recent_daily_bars", side_effect=Exception("API down")):
            result = provider.get_quote("AAPL")
        assert result is None

    def test_get_price_history_returns_price_points(self):
        provider = self._make_provider()

        mock_bar1 = MagicMock()
        mock_bar1.timestamp = datetime(2026, 4, 30, 0, 0, tzinfo=timezone.utc)
        mock_bar1.open = 148.0
        mock_bar1.high = 151.0
        mock_bar1.low = 147.5
        mock_bar1.close = 150.0
        mock_bar1.volume = 1_200_000

        mock_bar2 = MagicMock()
        mock_bar2.timestamp = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
        mock_bar2.open = 150.0
        mock_bar2.high = 152.0
        mock_bar2.low = 149.0
        mock_bar2.close = 151.5
        mock_bar2.volume = 1_100_000

        with patch.object(provider, "_fetch_bars", return_value=[mock_bar1, mock_bar2]):
            result = provider.get_price_history("AAPL", period="1mo", interval="1d")

        assert len(result) == 2
        assert all(isinstance(p, PricePoint) for p in result)
        assert result[0].close == pytest.approx(150.0)
        assert result[1].source == "alpaca"

    def test_get_quotes_uses_batch_call(self):
        """get_quotes calls _get_quotes_batch, not serial get_quote."""
        provider = self._make_provider()
        expected = [
            QuoteData(symbol="AAPL", current_price=150.0, source="alpaca"),
            QuoteData(symbol="MSFT", current_price=300.0, source="alpaca"),
        ]
        with patch.object(provider, "_get_quotes_batch", return_value=expected) as mock_batch:
            result = provider.get_quotes(["AAPL", "MSFT"])
        mock_batch.assert_called_once_with(["AAPL", "MSFT"])
        assert result == expected

    def test_get_quotes_falls_back_to_serial_on_batch_failure(self):
        """If batch fails, get_quotes falls back to serial."""
        provider = self._make_provider()
        with patch.object(provider, "_get_quotes_batch", side_effect=Exception("batch error")):
            with patch.object(provider, "get_quote", side_effect=[
                QuoteData(symbol="AAPL", current_price=150.0, source="alpaca"),
                None,
            ]):
                result = provider.get_quotes(["AAPL", "FAIL"])
        assert len(result) == 1
        assert result[0].symbol == "AAPL"
