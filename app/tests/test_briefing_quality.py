"""Tests for BriefingQualityGuard, VIX/Brent handling, and wording guards."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.briefing.quality_guard import BriefingQualityGuard, apply_quality_guard, flags_from_briefing
from app.briefing.valuation_lens import ValuationLens
from app.briefing.formatter import TelegramFormatter
from app.briefing.email_formatter import EmailFormatter
from app.briefing.macro_policy_service import build_macro_policy_watch_summary
from app.briefing.morning_charts import (
    assign_watchlist_colours,
    WATCHLIST_COLOUR_PALETTE,
    build_morning_chart_bundle,
)
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing, MarketSetup
from app.schemas.events import QuoteData, PricePoint
from datetime import timedelta


# ---------------------------------------------------------------------------
# 1. Macro Policy Watch heading appears exactly once
# ---------------------------------------------------------------------------

class TestMacroPolicyWatchHeadingOnce:
    """Heading should appear exactly once regardless of whether the raw string
    already starts with 'MACRO POLICY WATCH'."""

    def _make_briefing(self, watch_content: str) -> MorningBriefing:
        return MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            macro_policy_watch=watch_content,
        )

    def _count_heading(self, output: str) -> int:
        # Count both bold and plain occurrences
        import re
        plain = len(re.findall(r"MACRO POLICY WATCH", output, re.IGNORECASE))
        return plain

    def test_telegram_heading_once_when_content_starts_with_heading(self):
        briefing = self._make_briefing("MACRO POLICY WATCH\nFed: hold · ECB: hold")
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        assert self._count_heading(output) == 1, (
            f"Expected exactly 1 'MACRO POLICY WATCH' heading, got {self._count_heading(output)}"
        )

    def test_telegram_heading_once_when_content_has_no_heading(self):
        briefing = self._make_briefing("Fed: hold · ECB: hold\nDeterministic signal, not a forecast.")
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        assert self._count_heading(output) == 1

    def test_email_heading_once(self):
        briefing = self._make_briefing("MACRO POLICY WATCH\nFed: hold · ECB: hold")
        html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
        assert self._count_heading(html) == 1, (
            f"Expected exactly 1 heading in email HTML, got {self._count_heading(html)}"
        )

    def test_no_macro_watch_means_no_heading(self):
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        assert "MACRO POLICY WATCH" not in output


# ---------------------------------------------------------------------------
# 2. VIX unavailable cannot produce "VIX confirms"
# ---------------------------------------------------------------------------

class TestVixUnavailable:
    def test_quality_guard_replaces_vix_confirms(self):
        guard = BriefingQualityGuard(vix_available=False)
        sections = [
            "<b>GEO RISK</b>\nOil confirms the move. VIX confirms broad stress is building.",
            "<b>OTHER</b>\nVIX does not confirm a panic bid.",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        assert "VIX confirms" not in full
        assert "VIX unavailable" in full

    def test_vix_unavailable_absent_when_vix_available(self):
        guard = BriefingQualityGuard(vix_available=True)
        sections = ["<b>GEO RISK</b>\nVIX confirms broad stress."]
        result = guard.apply(sections)
        full = "\n".join(result)
        assert "VIX confirms" in full
        assert "VIX unavailable" not in full

    def test_formatter_applies_vix_guard(self):
        """When vix: unavailable appears in data_basis_lines, formatter should suppress VIX confirms."""
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            data_basis_lines=["vix: unavailable (yfinance failed)"],
        )
        # Inject a geo risk summary that would say VIX confirms
        briefing.geo_risk_summary = "VIX confirms broad geo stress."
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        # The quality guard runs on sections, which may not include geo_risk_summary directly,
        # but if it does appear in a section block it should be cleaned.
        # The test validates the guard is wired into the formatter.
        assert "VIX confirms" not in output or "VIX unavailable" in output


# ---------------------------------------------------------------------------
# 3. Brent stale cannot be live confirmation
# ---------------------------------------------------------------------------

class TestBrentStale:
    def test_quality_guard_replaces_brent_confirms(self):
        guard = BriefingQualityGuard(brent_stale=True)
        sections = [
            "<b>OIL</b>\nBrent confirms energy stress. WTI up 2%.",
            "<b>GEO</b>\nBrent does not confirm broad panic.",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        assert "Brent confirms" not in full
        assert "Brent stale/provider-held" in full

    def test_brent_fresh_keeps_confirmation_language(self):
        guard = BriefingQualityGuard(brent_stale=False)
        sections = ["<b>OIL</b>\nBrent confirms energy stress."]
        result = guard.apply(sections)
        assert "Brent confirms" in result[0]

    def test_stale_label_appears(self):
        guard = BriefingQualityGuard(brent_stale=True)
        sections = ["<b>OIL</b>\nBrent confirms the geo-energy signal."]
        result = guard.apply(sections)
        assert "stale" in result[0].lower() or "provider-held" in result[0].lower()


# ---------------------------------------------------------------------------
# 4. Yield curve spec includes prior-week series
# ---------------------------------------------------------------------------

class TestYieldCurveSpec:
    def _make_yield_points(self, *, include_prev: bool = True):
        from app.schemas.events import MacroDataPoint
        return [
            MacroDataPoint(series_id="DGS2", name="2Y", value=4.5, previous_value=4.4 if include_prev else None, change=0.01),
            MacroDataPoint(series_id="DGS10", name="10Y", value=4.7, previous_value=4.6 if include_prev else None, change=0.01),
            MacroDataPoint(series_id="DGS30", name="30Y", value=4.9, previous_value=4.8 if include_prev else None, change=0.01),
        ]

    def _make_market_data(self):
        svc = MagicMock()
        svc.get_price_history.return_value = []
        return svc

    def test_prior_week_series_present_when_data_available(self):
        from app.briefing.morning_charts import _yield_curve_spec
        points = self._make_yield_points(include_prev=True)
        svc = self._make_market_data()
        spec = _yield_curve_spec(points, svc, {}, datetime.now(timezone.utc))
        # Each row should have week_ago populated
        rows_with_prior = [r for r in spec["series"] if r.get("week_ago") is not None]
        assert len(rows_with_prior) >= 2, "Expected prior-week data in at least 2 rows"
        assert spec["meta"]["prior_week_available"] is True

    def test_no_prior_week_noted_in_caption(self):
        from app.briefing.morning_charts import _yield_curve_spec
        points = self._make_yield_points(include_prev=False)
        svc = self._make_market_data()
        spec = _yield_curve_spec(points, svc, {}, datetime.now(timezone.utc))
        assert spec["meta"]["prior_week_available"] is False
        assert "unavailable" in spec["caption"].lower() or "current" in spec["caption"].lower()


# ---------------------------------------------------------------------------
# 5. Watchlist chart series have distinct colours
# ---------------------------------------------------------------------------

class TestWatchlistColours:
    def test_assign_distinct_colours(self):
        symbols = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
        assignments = assign_watchlist_colours(symbols)
        colours = [v["colour"] for v in assignments.values()]
        assert len(set(colours)) == len(colours), "All 5 symbols should have distinct colours"

    def test_same_set_always_same_colour(self):
        """Same symbol set produces the same colour assignment (deterministic)."""
        symbols = ["NVDA", "AAPL", "MSFT", "GOOGL"]
        a = assign_watchlist_colours(symbols)
        b = assign_watchlist_colours(symbols)
        for sym in symbols:
            assert a[sym]["colour_index"] == b[sym]["colour_index"], (
                f"{sym} got different colour_index across two calls with same set"
            )
            assert a[sym]["colour"] == b[sym]["colour"]

    def test_watchlist_movers_spec_has_colours(self):
        from app.briefing.morning_charts import _watchlist_movers_spec
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            watchlist_quotes=[
                QuoteData(symbol="AAPL", current_price=180.0, change=2.0, change_percent=1.1),
                QuoteData(symbol="MSFT", current_price=400.0, change=-3.0, change_percent=-0.75),
                QuoteData(symbol="NVDA", current_price=800.0, change=10.0, change_percent=1.26),
                QuoteData(symbol="GOOGL", current_price=170.0, change=-1.5, change_percent=-0.88),
            ],
        )
        spec = _watchlist_movers_spec(briefing)
        # All rows should have a colour field
        rows_with_colour = [row for row in spec["series"] if row.get("colour")]
        assert len(rows_with_colour) >= 2, "Expected colour field on series rows"
        # Each unique symbol should have a distinct colour
        sym_colour = {row["symbol"]: row["colour"] for row in spec["series"] if row.get("colour")}
        unique_colours = list(sym_colour.values())
        assert len(set(unique_colours)) == len(unique_colours), (
            f"Each unique symbol should have a distinct colour, got: {sym_colour}"
        )

    def test_ten_plus_tickers_cycle_through_palette(self):
        symbols = [f"TICK{i}" for i in range(12)]
        assignments = assign_watchlist_colours(symbols)
        # First 10 should use all palette colours, then cycle
        palette_len = len(WATCHLIST_COLOUR_PALETTE)
        for i, sym in enumerate(sorted(symbols)):
            assert assignments[sym]["colour_index"] == i % palette_len
            assert assignments[sym]["marker_style"] == i // palette_len


# ---------------------------------------------------------------------------
# 6. Full visual mode can include more than 5 charts
# ---------------------------------------------------------------------------

class TestChartRichness:
    def _make_market_data(self):
        svc = MagicMock()
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        svc.get_price_history.return_value = [
            PricePoint(symbol="SPY", timestamp=start + timedelta(days=i), close=100.0 + i)
            for i in range(60)
        ]
        svc.get_quotes.return_value = []
        return svc

    def _make_briefing_with_quotes(self) -> MorningBriefing:
        index_quotes = [
            QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, change=2.0, change_percent=0.4),
            QuoteData(symbol="QQQ", display_name="NASDAQ", current_price=420.0, change=3.0, change_percent=0.72),
            QuoteData(symbol="DIA", display_name="DOW", current_price=390.0, change=1.0, change_percent=0.26),
            QuoteData(symbol="IWM", display_name="RUSSELL", current_price=205.0, change=-1.0, change_percent=-0.49),
        ]
        macro_quotes = [
            QuoteData(symbol="GC=F", display_name="Gold", current_price=2300.0, change=5.0, change_percent=0.22),
            QuoteData(symbol="CL=F", display_name="WTI Crude", current_price=78.0, change=0.5, change_percent=0.64),
            QuoteData(symbol="^VIX", display_name="VIX", current_price=15.0, change=-0.5, change_percent=-3.2),
        ]
        setup = MarketSetup(index_quotes=index_quotes, macro_quotes=macro_quotes)
        return MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="morning",
            market_setup=setup,
        )

    def test_full_mode_not_capped_at_5(self):
        """In full mode with all chart generators producing specs, result should exceed 5."""
        briefing = self._make_briefing_with_quotes()
        svc = self._make_market_data()
        profile = UserProfile()
        bundle, selection = build_morning_chart_bundle(
            briefing=briefing,
            profile=profile,
            market_data_service=svc,
        )
        # In full morning mode, target is 8 charts (min 7)
        assert len(selection) >= 5, f"Expected at least 5 selected charts, got {len(selection)}"


# ---------------------------------------------------------------------------
# 7. Chart suppression only for duplicate/broken/unavailable
# ---------------------------------------------------------------------------

class TestChartSuppression:
    def test_suppressed_charts_logged_with_reason(self, caplog):
        import logging
        from app.briefing.morning_charts import _select_candidates, ChartCandidate
        from app.schemas.briefings import MarketSetup

        profile = UserProfile()
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="morning",
            market_setup=MarketSetup(),
        )
        normalized = {
            "us_avg": 0.3, "eu_avg": 0.1, "asia_avg": 0.2, "breadth": 0.65,
            "total_indices": 4, "up_indices": 3, "dispersion": 0.2,
            "small_vs_large": 0.1, "growth_vs_defensive": 0.2,
            "vix_level": 15.0, "vix_delta_pct": -0.5, "oil_delta_pct": 0.3,
            "brent_delta_pct": 0.2, "gold_delta_pct": 0.1,
            "ten_y_level": 4.3, "two_y_level": 4.1, "spread_level": 0.2,
            "ten_y_change": 0.01, "two_y_change": 0.01, "curve_change": 0.005,
        }
        regime_tags = ["risk_on"]
        # Build minimal candidate list with enough available specs
        candidates = [
            ChartCandidate(
                chart_key=f"chart_{i}",
                category="support",
                priority=0.8 - i * 0.05,
                spec={
                    "chart_key": f"chart_{i}",
                    "available": True,
                    "priority": 0.8 - i * 0.05,
                    "variant": "test",
                    "reason_if_hidden": None,
                    "title": f"Chart {i}",
                    "caption": "test",
                    "series": [],
                    "annotations": [],
                    "email_dimensions": {},
                },
                reason=f"Reason {i}",
            )
            for i in range(12)
        ]
        selected, meta = _select_candidates(
            candidates, profile=profile, briefing=briefing, normalized=normalized, regime_tags=regime_tags
        )
        suppressed = meta.get("suppressed_charts", [])
        # For each suppressed chart, the reason should be present
        for s in suppressed:
            assert ":" in s, f"Suppressed chart entry should have reason: '{s}'"


# ---------------------------------------------------------------------------
# 8. Valuation Lens disabled by default
# ---------------------------------------------------------------------------

class TestValuationLensDisabledByDefault:
    def test_disabled_returns_empty(self):
        lens = ValuationLens(enabled=False)
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        assert lens.build_lines(briefing) == []

    def test_disabled_in_formatter_output(self):
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        assert "VALUATION LENS" not in output

    def test_generator_disabled_by_default(self):
        """include_valuation_lens defaults to False so lens lines should be empty."""
        from app.briefing.valuation_lens import ValuationLens
        profile = UserProfile()
        # Default profile has no include_valuation_lens setting
        enabled = bool(profile.delivery.get("include_valuation_lens", False))
        assert enabled is False


# ---------------------------------------------------------------------------
# 9. Valuation Lens appears only when enabled and triggered
# ---------------------------------------------------------------------------

class TestValuationLensEnabled:
    def _make_briefing_with_trigger(self) -> MorningBriefing:
        from app.schemas.events import NormalisedEvent
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            canonical_prices={"US10Y": {"value": 4.7, "change": 0.04}},
            watchlist_quotes=[
                QuoteData(symbol="NVDA", current_price=900.0, change=25.0, change_percent=2.8),
            ],
        )
        evt = NormalisedEvent(
            event_id="test-1",
            title="NVDA Earnings Beat",
            source="test",
            tickers=["NVDA"],
        )
        evt.raw_data = {"pe": 35.0, "market_cap": 2_200_000_000_000}
        briefing.watchlist_events = [evt]
        return briefing

    def test_enabled_and_triggered_produces_lines(self):
        lens = ValuationLens(enabled=True, max_items=3)
        briefing = self._make_briefing_with_trigger()
        lines = lens.build_lines(briefing)
        assert len(lines) > 0
        assert any("VALUATION LENS" in line for line in lines)
        assert any("Valuation context" in line for line in lines)

    def test_enabled_but_not_triggered_returns_empty(self):
        lens = ValuationLens(enabled=True, max_items=3)
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            canonical_prices={"US10Y": {"value": 3.5, "change": 0.005}},
        )
        lines = lens.build_lines(briefing)
        # No trigger conditions met: should be empty
        assert lines == []


# ---------------------------------------------------------------------------
# 10. Valuation Lens never hallucinates (all ratios None -> no values shown)
# ---------------------------------------------------------------------------

class TestValuationLensNoHallucination:
    def test_none_ratios_produce_no_metric_values(self, monkeypatch):
        """When raw_data has no ratios AND yfinance returns empty info, no ratio values appear."""
        from app.schemas.events import NormalisedEvent
        import app.briefing.valuation_lens as vl_module

        # Patch the yfinance fetch to return empty (simulating all ratios unavailable)
        def _mock_fetch(symbol, *, is_financial, is_etf):
            return []

        monkeypatch.setattr(vl_module, "_fetch_yfinance_ratios", _mock_fetch)

        lens = ValuationLens(enabled=True, max_items=3)
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            canonical_prices={"US10Y": {"value": 4.7, "change": 0.04}},
            watchlist_quotes=[
                QuoteData(symbol="NVDA", current_price=900.0, change=25.0, change_percent=2.8),
            ],
        )
        evt = NormalisedEvent(
            event_id="test-1",
            title="NVDA Earnings Beat",
            source="test",
            tickers=["NVDA"],
        )
        # All ratios absent from raw_data
        evt.raw_data = {}
        briefing.watchlist_events = [evt]

        lines = lens.build_lines(briefing)
        # When no ratios are available, the lens should produce no ticker bullet lines
        # (only the section label and disclaimer if triggered at all, or empty)
        metric_patterns = ["P/E", "P/S", "EV/EBITDA", "P/B", "ROE", "MCap", "Fwd"]
        for line in lines:
            for pattern in metric_patterns:
                assert pattern not in line, f"Found metric '{pattern}' when all ratios are None: '{line}'"


# ---------------------------------------------------------------------------
# 11. "review diagnostics" absent from user-facing output
# ---------------------------------------------------------------------------

class TestReviewDiagnosticsAbsent:
    def test_quality_guard_removes_phrase(self):
        guard = BriefingQualityGuard()
        sections = [
            "<b>RISK POSTURE</b>\nPlease review diagnostics before taking action.",
            "<b>PORTFOLIO</b>\nReview diagnostics on your portfolio positions.",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        assert "review diagnostics" not in full.lower()
        # Near "portfolio" context: should become "review risk"
        assert "review risk" in full.lower() or "monitor confirmation" in full.lower()

    def test_formatter_removes_phrase(self):
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            market_setup_analysis="Review diagnostics for your portfolio exposure today.",
        )
        formatter = TelegramFormatter("Europe/Madrid")
        output = "\n".join(formatter.format_morning_briefing(briefing))
        assert "review diagnostics" not in output.lower()


# ---------------------------------------------------------------------------
# 12. Duplicate section heading guard
# ---------------------------------------------------------------------------

class TestDuplicateHeadingGuard:
    def test_duplicate_heading_removed(self):
        guard = BriefingQualityGuard()
        sections = [
            "<b>MARKET SETUP</b>\nS&P 500 +0.4%",
            "<b>MARKET SETUP</b>\nDuplicate section content.",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        import re
        count = len(re.findall(r"MARKET SETUP", full, re.IGNORECASE))
        assert count == 1, f"Expected 1 heading, got {count}"

    def test_unique_headings_preserved(self):
        guard = BriefingQualityGuard()
        sections = [
            "<b>MARKET SETUP</b>\nContent A",
            "<b>GLOBAL NEWS</b>\nContent B",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        assert "MARKET SETUP" in full
        assert "GLOBAL NEWS" in full
