"""Tests for earnings estimate enrichment from yfinance."""

from unittest.mock import MagicMock, patch

import pytest

from app.schemas.events import EarningsEvent


class TestEarningsEnrichment:
    def _make_event(self, symbol="AAPL", eps_estimate=None, eps_actual=None):
        return EarningsEvent(
            symbol=symbol,
            report_date="2026-05-01",
            eps_estimate=eps_estimate,
            eps_actual=eps_actual,
            source="finnhub",
        )

    def test_surprise_computed_when_both_values_present(self):
        from app.briefing.morning_generator import _compute_earnings_surprise

        event = self._make_event(eps_estimate=2.0, eps_actual=2.5)
        enriched = _compute_earnings_surprise(event)
        assert enriched.surprise_percent == pytest.approx(25.0)

    def test_surprise_is_none_when_estimate_missing(self):
        from app.briefing.morning_generator import _compute_earnings_surprise

        event = self._make_event(eps_estimate=None, eps_actual=2.5)
        enriched = _compute_earnings_surprise(event)
        assert enriched.surprise_percent is None

    def test_surprise_is_none_when_actual_missing(self):
        from app.briefing.morning_generator import _compute_earnings_surprise

        event = self._make_event(eps_estimate=2.0, eps_actual=None)
        enriched = _compute_earnings_surprise(event)
        assert enriched.surprise_percent is None

    def test_surprise_handles_zero_estimate(self):
        from app.briefing.morning_generator import _compute_earnings_surprise

        event = self._make_event(eps_estimate=0.0, eps_actual=0.5)
        enriched = _compute_earnings_surprise(event)
        assert enriched.surprise_percent is None

    def test_enrich_from_yfinance_fills_missing_estimate(self):
        from app.briefing.morning_generator import enrich_earnings_from_yfinance

        event = self._make_event(symbol="AAPL", eps_estimate=None)
        mock_df_row = MagicMock()
        mock_df_row.__getitem__ = lambda self, key: {
            "EPS Estimate": 1.85,
            "Reported EPS": 2.01,
        }[key]
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iterrows = MagicMock(return_value=iter([("2026-05-01", mock_df_row)]))

        mock_ticker = MagicMock()
        mock_ticker.earnings_dates = mock_df

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = enrich_earnings_from_yfinance([event])
        assert result[0].eps_estimate == pytest.approx(1.85)

    def test_enrich_gracefully_handles_yfinance_failure(self):
        from app.briefing.morning_generator import enrich_earnings_from_yfinance

        event = self._make_event(symbol="AAPL", eps_estimate=None)
        with patch("yfinance.Ticker", side_effect=Exception("yf down")):
            result = enrich_earnings_from_yfinance([event])
        assert result[0].eps_estimate is None
