"""Tests for SEC Form 4 insider trade fetching."""
from unittest.mock import MagicMock, patch

import pytest

from app.data_sources.providers.sec_provider import SECProvider


class TestForm4Materiality:
    def test_form4_passes_materiality_check(self):
        """Form 4 must not be filtered out by _is_material_hit."""
        assert SECProvider._is_material_hit("4", "") is True
        assert SECProvider._is_material_hit("4", "Statement of Changes in Beneficial Ownership") is True

    def test_form4_classified_as_insider_transaction(self):
        assert SECProvider._classify_form("4") == "insider_transaction"

    def test_search_insider_trades_calls_search_filings(self):
        provider = SECProvider(user_agent="test test@test.com")
        mock_events = [
            MagicMock(event_type="insider_transaction", tickers=["AAPL"]),
        ]
        with patch.object(provider, "search_filings", return_value=mock_events) as mock_search:
            result = provider.search_insider_trades(tickers=["AAPL"], days_back=3)
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args
        assert "4" in call_kwargs.kwargs.get("forms", call_kwargs.args[1] if len(call_kwargs.args) > 1 else [])
        assert result == mock_events

    def test_fetch_insider_trades_returns_only_form4_events(self):
        from app.data_sources.news_data import NewsDataService
        from app.settings import Settings

        settings = Settings(sec_user_agent="test test@test.com", dry_run=True)
        svc = NewsDataService(settings)

        mock_events = [
            MagicMock(event_type="insider_transaction"),
            MagicMock(event_type="current_report"),
        ]
        with patch.object(svc.sec, "search_insider_trades", return_value=mock_events):
            result = svc.fetch_insider_trades(tickers=["AAPL"])
        # Only insider_transaction events should be returned
        assert all(e.event_type == "insider_transaction" for e in result)
