"""Tests for provider outage fallback hierarchy (Part 2+3)."""
from __future__ import annotations
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


def _stale_quote(symbol: str, name: str, price: float, pct: float, session: str) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=name,
        current_price=price,
        change_percent=pct,
        source=f"stale_snapshot:{session}",
        timestamp=datetime(2026, 5, 27, 13, 34, tzinfo=timezone.utc),
    )


class TestSnapshotFallbackLabelling:
    def test_stale_snapshot_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_STALE_SNAPSHOT
        q = _stale_quote("SPY", "S&P 500", 500.0, 0.5, "us_pre_open")
        assert source_basis_for_quote(q) == SOURCE_BASIS_STALE_SNAPSHOT

    def test_live_quote_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_LIVE
        q = QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, source="finnhub")
        assert source_basis_for_quote(q) == SOURCE_BASIS_LIVE

    def test_yfinance_quote_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_NEAR_REAL_TIME
        q = QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, source="yfinance")
        assert source_basis_for_quote(q) == SOURCE_BASIS_NEAR_REAL_TIME


class TestBriefingStaleSnapshotBehaviour:
    """When intraday provider fetch returns empty but pre-open snapshot exists,
    briefing must use stale_snapshot values and label them."""

    def test_stale_snapshot_not_empty_output(self):
        """A briefing with stale_snapshot quotes should not show 'Market Prices: unavailable'."""
        from app.briefing.formatter import TelegramFormatter
        q = _stale_quote("SPY", "S&P 500", 5000.0, -0.3, "us_pre_open")
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[q], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "Market Prices: unavailable" not in text

    def test_stale_snapshot_banner_present(self):
        """Briefing with stale_snapshot_used=True must show degraded data banner."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "LIVE DATA DEGRADED" in text or "stale" in text.lower()

    def test_no_snapshot_produces_outage_block(self):
        """Briefing with market_data_outage=True and no snapshot shows outage block."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
            market_data_outage=True,
            stale_snapshot_used=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "OUTAGE" in text.upper() or "unavailable" in text.lower()

    def test_stale_snapshot_quotes_not_shown_as_live(self):
        """Quotes from a stale snapshot must not produce 'live' freshness labels."""
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_STALE_SNAPSHOT
        q = _stale_quote("VIX", "VIX", 18.0, -0.5, "us_pre_open")
        assert source_basis_for_quote(q) == SOURCE_BASIS_STALE_SNAPSHOT
        assert source_basis_for_quote(q) != "live"

    def test_provider_outage_no_snapshot_no_regime_shift_from_missing_data(self):
        """No regime shift should be generated when all data is missing."""
        from app.briefing.session_snapshot import build_what_changed_lines
        previous = {"vix_level": 18.0, "us10y": 4.50, "wti_pct": -1.2}
        current = {}  # all missing
        lines = build_what_changed_lines(previous=previous, current=current)
        # Should not generate a regime shift line from empty data
        for line in lines:
            assert "risk_on" not in line.lower() or "->" not in line, (
                f"Unexpected regime shift from empty data: {line}"
            )


class TestOutageEmailBehaviour:
    def test_outage_with_snapshot_not_unavailable(self):
        """Provider outage with snapshot does not output 'Market Prices: unavailable'."""
        from app.briefing.formatter import TelegramFormatter
        q = _stale_quote("SPY", "S&P 500", 5000.0, 0.2, "us_pre_open")
        vix_q = _stale_quote("VIX", "VIX", 18.5, -0.3, "us_pre_open")
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[q, vix_q], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "Market Prices: unavailable" not in text

    def test_outage_without_snapshot_shows_outage_email(self):
        """Provider outage with no snapshot shows concise outage email."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_outage=True,
            stale_snapshot_used=False,
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "OUTAGE" in text.upper() or "unavailable" in text.lower() or "degraded" in text.lower()
