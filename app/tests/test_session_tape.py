"""Tests for session tape recap, rates/macro tape, and valuation lens improvements."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.briefing.session_tape import (
    SessionTapeEntry,
    _compute_range_label,
    _compute_tape_verdict,
    _build_entry_from_quote,
    build_session_tape,
    build_rates_macro_tape,
    format_session_tape_text,
    format_watchlist_tape_compact,
    session_range_strip_spec,
)
from app.briefing.valuation_lens import ValuationLens
from app.schemas.briefings import MorningBriefing, MarketSetup
from app.schemas.events import QuoteData, MacroDataPoint


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_quote(
    symbol: str,
    current_price: float = 100.0,
    previous_close: float = 99.0,
    open_price: float = 99.5,
    high: float = 101.0,
    low: float = 98.0,
    change_percent: float = 1.01,
    display_name: str = "",
) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=display_name or symbol,
        current_price=current_price,
        previous_close=previous_close,
        open=open_price,
        high=high,
        low=low,
        change=current_price - previous_close,
        change_percent=change_percent,
    )


def _make_tape_entry(
    *,
    latest: float = 95.0,
    low: float = 90.0,
    high: float = 100.0,
    session_open: float | None = 93.0,
    prior_close: float | None = 94.0,
    symbol: str = "TEST",
    label: str = "Test",
    data_quality: str = "full",
) -> SessionTapeEntry:
    if low is not None and high is not None and high > low and latest is not None:
        rpos = (latest - low) / (high - low)
    else:
        rpos = None

    cvpc = (latest - prior_close) / prior_close * 100.0 if prior_close else None
    cvop = (latest - session_open) / session_open * 100.0 if session_open else None

    return SessionTapeEntry(
        label=label,
        symbol=symbol,
        prior_close=prior_close,
        session_open=session_open,
        session_high=high,
        session_low=low,
        latest=latest,
        change_vs_prior_close_pct=cvpc,
        change_vs_open_pct=cvop,
        range_position_pct=rpos,
        range_label=_compute_range_label(rpos),
        tape_verdict=_compute_tape_verdict(cvop, cvpc, rpos),
        data_quality=data_quality,
        asset_class="equity",
    )


# ---------------------------------------------------------------------------
# 1. Range position near highs
# ---------------------------------------------------------------------------

class TestRangePositionNearHighs:
    def test_near_highs(self):
        entry = _make_tape_entry(latest=99, low=90, high=100)
        assert entry.range_position_pct == pytest.approx(0.9)
        assert entry.range_label == "near highs"


# ---------------------------------------------------------------------------
# 2. Range position near lows
# ---------------------------------------------------------------------------

class TestRangePositionNearLows:
    def test_near_lows(self):
        entry = _make_tape_entry(latest=91, low=90, high=100)
        assert entry.range_position_pct == pytest.approx(0.1)
        assert entry.range_label == "near lows"


# ---------------------------------------------------------------------------
# 3. Range position mid-range
# ---------------------------------------------------------------------------

class TestRangePositionMidRange:
    def test_mid_range(self):
        entry = _make_tape_entry(latest=95, low=90, high=100)
        assert entry.range_position_pct == pytest.approx(0.5)
        assert entry.range_label == "mid-range"


# ---------------------------------------------------------------------------
# 4. No fake OHLC when unavailable
# ---------------------------------------------------------------------------

class TestNoFakeOhlcWhenUnavailable:
    def test_prior_close_only(self):
        entry = _make_tape_entry(
            latest=95.0,
            low=None,   # type: ignore[arg-type]
            high=None,  # type: ignore[arg-type]
            session_open=None,
            prior_close=94.0,
            data_quality="prior_close_only",
        )
        # Manually set as prior_close_only (factory override)
        assert entry.range_label == "range unavailable"
        assert entry.data_quality == "prior_close_only"

    def test_build_entry_from_quote_no_ohlc(self):
        """QuoteData with zero open/high/low should produce prior_close_only entry."""
        q = QuoteData(
            symbol="TEST",
            current_price=100.0,
            previous_close=99.0,
            open=0.0,
            high=0.0,
            low=0.0,
            change=1.0,
            change_percent=1.01,
        )
        entry = _build_entry_from_quote(q)
        assert entry is not None
        assert entry.data_quality == "prior_close_only"
        assert entry.range_label == "range unavailable"
        assert entry.session_open is None
        assert entry.session_high is None
        assert entry.session_low is None


# ---------------------------------------------------------------------------
# 5. Tape verdict: rallied from open
# ---------------------------------------------------------------------------

class TestTapeVerdictRalliedFromOpen:
    def test_rallied_from_open(self):
        # change_vs_open > 0.3%, range_position >= 0.70
        entry = _make_tape_entry(
            latest=99,
            low=90,
            high=100,
            session_open=98,
            prior_close=97,
        )
        assert entry.tape_verdict == "rallied from open"


# ---------------------------------------------------------------------------
# 6. Tape verdict: faded from highs
# ---------------------------------------------------------------------------

class TestTapeVerdictFadedFromHighs:
    def test_faded_from_highs(self):
        # change_vs_open > 0.3% but range_position < 0.40
        # open=93, latest=94 => change_vs_open ~1.08%
        # latest=94, low=90, high=100 => range_position=0.4 (boundary, need <0.40)
        entry = _make_tape_entry(
            latest=93.5,  # range_pos = (93.5-90)/(100-90) = 0.35
            low=90,
            high=100,
            session_open=93.0,  # change_vs_open ~0.54%
            prior_close=90.0,
        )
        assert entry.range_position_pct == pytest.approx(0.35)
        assert entry.change_vs_open_pct is not None
        assert entry.change_vs_open_pct > 0.3
        assert entry.tape_verdict == "faded from highs"


# ---------------------------------------------------------------------------
# 7. Closing wrap includes US session recap
# ---------------------------------------------------------------------------

class TestClosingWrapIncludesUsSessionRecap:
    def test_us_session_recap_heading(self):
        entries = [
            _make_tape_entry(symbol="^GSPC", label="S&P 500", latest=99, low=90, high=100),
            _make_tape_entry(symbol="^IXIC", label="Nasdaq", latest=95, low=90, high=100),
        ]
        text = format_session_tape_text(entries, "us_cash", "closing_wrap")
        assert "US CASH SESSION RECAP" in text


# ---------------------------------------------------------------------------
# 8. Europe midday uses "EUROPE SESSION SO FAR"
# ---------------------------------------------------------------------------

class TestEuropeMiddayUsesSessionSoFar:
    def test_europe_midday_heading(self):
        entries = [
            _make_tape_entry(symbol="^STOXX50E", label="EURO STOXX 50", latest=95, low=90, high=100),
        ]
        text = format_session_tape_text(entries, "europe_cash", "europe_midday")
        assert "EUROPE SESSION SO FAR" in text
        assert "RECAP" not in text


# ---------------------------------------------------------------------------
# 9. into_close uses "EUROPE CASH SESSION RECAP"
# ---------------------------------------------------------------------------

class TestIntoCloseUsesEuropeRecap:
    def test_into_close_europe_heading(self):
        entries = [
            _make_tape_entry(symbol="^STOXX50E", label="EURO STOXX 50", latest=95, low=90, high=100),
        ]
        text = format_session_tape_text(entries, "europe_cash", "into_close")
        assert "EUROPE CASH SESSION RECAP" in text


# ---------------------------------------------------------------------------
# 10. Watchlist tape suppressed when prior_close_only
# ---------------------------------------------------------------------------

class TestWatchlistTapeSuppressedWhenPriorCloseOnly:
    def test_suppressed(self):
        entries = [
            _make_tape_entry(
                symbol=f"SYM{i}",
                label=f"Symbol {i}",
                session_open=None,
                data_quality="prior_close_only",
            )
            for i in range(5)
        ]
        # Override change_vs_open_pct to None (simulate prior_close_only)
        for e in entries:
            object.__setattr__(e, "change_vs_open_pct", None)

        result = format_watchlist_tape_compact(entries)
        assert result == ""


# ---------------------------------------------------------------------------
# 11. Trigger wording: 10Y above 4.45 in rates tape
# ---------------------------------------------------------------------------

class TestTriggerWordingBreachedInRatesTape:
    def _make_briefing_with_yields(self, ten_y: float, two_y: float) -> MorningBriefing:
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="closing_wrap",
        )
        briefing.market_setup.treasury_10y = MacroDataPoint(
            series_id="DGS10",
            name="US 10Y",
            value=ten_y,
            change=0.02,
            source="fred",
        )
        briefing.market_setup.treasury_2y = MacroDataPoint(
            series_id="DGS2",
            name="US 2Y",
            value=two_y,
            change=0.03,
            source="fred",
        )
        return briefing

    def test_above_4_45_trigger(self):
        briefing = self._make_briefing_with_yields(ten_y=4.48, two_y=3.95)
        tape = build_rates_macro_tape(briefing)
        assert tape != ""
        assert "above 4.45" in tape

    def test_below_trigger_no_breach_note(self):
        briefing = self._make_briefing_with_yields(ten_y=4.30, two_y=3.80)
        tape = build_rates_macro_tape(briefing)
        # May be empty (need >=3 values) but should not say "above 4.45"
        assert "above 4.45" not in tape


# ---------------------------------------------------------------------------
# 12. Valuation lens no hallucination when yfinance returns None
# ---------------------------------------------------------------------------

class TestValuationLensNoHallucination:
    def test_no_numeric_ratio_when_all_none(self):
        from app.briefing.valuation_lens import _fetch_yfinance_ratios

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            result = _fetch_yfinance_ratios("FAKE", is_financial=False, is_etf=False)

        # When all values are None/missing, no numeric ratios should appear
        assert result == [], f"Expected [], got: {result}"

    def test_valuation_lens_build_lines_no_hallucination(self):
        """ValuationLens should return [] when yfinance info is all None."""
        lens = ValuationLens(enabled=True, max_items=3)

        briefing = MagicMock()
        briefing.canonical_prices = {"US10Y": {"value": 4.5, "change": 0.03}}
        briefing.watchlist_quotes = [
            MagicMock(symbol="NVDA", change_percent=3.0),
            MagicMock(symbol="MSFT", change_percent=2.5),
        ]
        briefing.portfolio_quotes = []
        briefing.top_themes = []
        briefing.portfolio_focus = []
        briefing.watchlist_events = []

        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.info = {}
            lines = lens.build_lines(briefing)

        # Should not contain any fabricated numeric ratios
        for line in lines:
            # Ratios like "35.0x" or "12.5%" should not appear when data is all None
            assert "x" not in line or "VALUATION" in line or "advice" in line, (
                f"Unexpected numeric ratio in line: {line!r}"
            )


# ---------------------------------------------------------------------------
# 13. Session range strip suppressed when data insufficient
# ---------------------------------------------------------------------------

class TestSessionRangeStripSuppressedWhenDataInsufficient:
    def test_fewer_than_3_full_ohlc(self):
        entries = [
            _make_tape_entry(symbol="A", label="Asset A", data_quality="full"),
            _make_tape_entry(symbol="B", label="Asset B", data_quality="prior_close_only"),
        ]
        result = session_range_strip_spec(entries)
        assert result is None

    def test_exactly_3_full_ohlc_produces_spec(self):
        entries = [
            _make_tape_entry(symbol="A", label="S&P 500", data_quality="full"),
            _make_tape_entry(symbol="B", label="Nasdaq", data_quality="full"),
            _make_tape_entry(symbol="C", label="Russell", data_quality="full"),
        ]
        result = session_range_strip_spec(entries)
        assert result is not None
        assert result["available"] is True
        assert len(result["series"]) == 3


# ---------------------------------------------------------------------------
# 14. Chart richness preserved: closing_wrap selection includes existing charts
# ---------------------------------------------------------------------------

class TestChartRichnessPreserved:
    """Existing chart keys should still be present in closing_wrap spec list."""

    def _build_minimal_briefing(self) -> MorningBriefing:
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="closing_wrap",
        )
        return briefing

    def test_range_strip_is_extra_not_replacement(self):
        from app.briefing.morning_charts import _build_candidates, _normalize_inputs

        briefing = self._build_minimal_briefing()
        briefing.market_setup = MarketSetup(
            index_quotes=[
                _make_quote("^GSPC", display_name="S&P 500"),
                _make_quote("^IXIC", display_name="Nasdaq"),
                _make_quote("^RUT", display_name="Russell 2000"),
            ],
        )

        from app.personalization.user_profile import UserProfile
        profile = UserProfile(name="default_user", timezone="UTC")

        class _StubMDS:
            def get_price_history(self, symbol, period="1mo", interval="1d"):
                return []
            def get_quotes(self, symbols):
                return []

        normalized = _normalize_inputs(
            briefing.market_setup.index_quotes,
            briefing.market_setup.macro_quotes,
            briefing.macro_context,
            {},
        )
        candidates = _build_candidates(
            briefing=briefing,
            profile=profile,
            market_data_service=_StubMDS(),
            normalized=normalized,
            regime_tags=["mixed"],
        )
        chart_keys = {c.chart_key for c in candidates}

        # Existing charts should still exist
        assert "global_relative_performance" in chart_keys
        assert "cross_asset_impulse_strip" in chart_keys
        assert "watchlist_movers_card" in chart_keys

        # Range strip should be included for closing_wrap
        assert "session_range_strip" in chart_keys
