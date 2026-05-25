"""Tests for data freshness labelling: VIX availability, Europe equity freshness,
Brent stale handling, and related quality guard flags.

All tests are deterministic and use no live provider calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.briefing.quality_guard import BriefingQualityGuard, flags_from_briefing
from app.briefing.session_freshness import (
    FRESHNESS_DELAYED,
    FRESHNESS_NEAR_REAL_TIME,
    FRESHNESS_PRIOR_CLOSE,
    FRESHNESS_STALE,
    build_data_basis_lines,
    classify_quote_freshness,
)
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(
    symbol: str,
    name: str,
    ts: datetime,
    pct: float = 0.0,
    price: float = 100.0,
) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=name,
        current_price=price,
        change_percent=pct,
        timestamp=ts,
        source="yfinance",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Part 2: VIX availability tests
# ---------------------------------------------------------------------------

class TestVixSymbolAndAvailability:
    """VIX availability propagation through the quality guard."""

    def test_vix_available_true_when_price_present(self):
        """flags_from_briefing returns vix_available=True when a VIX quote has a price."""
        ts = _now()
        vix_q = _q("^VIX", "VIX", ts, price=18.5)
        setup = MarketSetup(
            index_quotes=[vix_q],
            macro_quotes=[],
        )
        briefing = MorningBriefing(
            generated_at=ts,
            market_setup=setup,
        )
        flags = flags_from_briefing(briefing)
        assert flags["vix_available"] is True

    def test_vix_available_false_when_no_vix_quote(self):
        """flags_from_briefing returns vix_available=False when no VIX quote exists."""
        ts = _now()
        setup = MarketSetup(
            index_quotes=[_q("SPY", "S&P 500", ts, price=500.0)],
            macro_quotes=[],
        )
        briefing = MorningBriefing(
            generated_at=ts,
            market_setup=setup,
        )
        flags = flags_from_briefing(briefing)
        assert flags["vix_available"] is False

    def test_vix_available_false_when_price_zero(self):
        """flags_from_briefing returns vix_available=False when VIX quote has price 0.0 (no data)."""
        ts = _now()
        # QuoteData requires a float; 0.0 indicates unavailable
        vix_q = QuoteData(
            symbol="VIX",
            display_name="VIX",
            current_price=0.0,
            timestamp=ts,
        )
        setup = MarketSetup(index_quotes=[vix_q], macro_quotes=[])
        briefing = MorningBriefing(generated_at=ts, market_setup=setup)
        flags = flags_from_briefing(briefing)
        assert flags["vix_available"] is False

    def test_vix_unavailable_suppresses_vix_confirms_text(self):
        """BriefingQualityGuard removes 'VIX confirms' when vix_available=False."""
        guard = BriefingQualityGuard(vix_available=False)
        sections = ["<b>GEO RISK</b>\nVIX confirms broad geo stress is building."]
        result = guard.apply(sections)
        assert "VIX confirms" not in "\n".join(result)
        assert "VIX unavailable" in "\n".join(result)

    def test_vix_available_preserves_confirms_text(self):
        """BriefingQualityGuard keeps 'VIX confirms' when vix_available=True."""
        guard = BriefingQualityGuard(vix_available=True)
        sections = ["<b>GEO RISK</b>\nVIX confirms broad geo stress."]
        result = guard.apply(sections)
        assert "VIX confirms" in "\n".join(result)

    def test_vix_stale_suppresses_confirms_text(self):
        """BriefingQualityGuard removes 'VIX confirms' when vix_stale=True."""
        guard = BriefingQualityGuard(vix_available=True, vix_stale=True)
        sections = ["<b>GEO RISK</b>\nVIX confirms the move."]
        result = guard.apply(sections)
        assert "VIX confirms" not in "\n".join(result)

    def test_basis_lines_vix_unavailable_sets_flag(self):
        """Legacy 'vix: unavailable' in data_basis_lines correctly sets vix_available=False."""
        ts = _now()
        briefing = MorningBriefing(
            generated_at=ts,
            data_basis_lines=["vix: unavailable (provider path did not return a live quote this cycle)."],
        )
        flags = flags_from_briefing(briefing)
        assert flags["vix_available"] is False

    def test_vix_data_symbol_mapping_to_yfinance(self):
        """The _YAHOO_SYMBOL_ALIASES dict maps 'VIX' to '^VIX' for yfinance."""
        from app.data_sources.market_data import _YAHOO_SYMBOL_ALIASES
        assert _YAHOO_SYMBOL_ALIASES.get("VIX") == "^VIX", (
            "VIX must map to ^VIX so yfinance can resolve the CBOE index symbol"
        )


# ---------------------------------------------------------------------------
# Part 3: Europe equity freshness tests
# ---------------------------------------------------------------------------

class TestEuropeEquityFreshness:
    """Europe equity freshness labelling during open vs closed sessions."""

    def test_europe_midday_open_session_fresh_quote_labelled_delayed(self):
        """Europe equity with a fresh quote (within 30 min) during europe_midday labels as delayed."""
        generated = datetime(2026, 5, 12, 9, 30, tzinfo=timezone.utc)   # 11:30 CEST
        quote_ts = datetime(2026, 5, 12, 9, 15, tzinfo=timezone.utc)    # 15 min ago

        q = _q("^STOXX50E", "EURO STOXX 50", quote_ts)
        meta = classify_quote_freshness(
            quote=q,
            generated_at=generated,
            session_key="europe_midday",
            timezone_name="Europe/Madrid",
        )
        assert meta.freshness_state in {FRESHNESS_NEAR_REAL_TIME, FRESHNESS_DELAYED}, (
            f"Expected near_real_time or delayed for fresh EU quote, got {meta.freshness_state}"
        )

    def test_europe_midday_open_session_stale_quote_labelled_prior_close(self):
        """Europe equity with a prior-day timestamp during europe_midday labels as prior_close with warning."""
        generated = datetime(2026, 5, 12, 9, 30, tzinfo=timezone.utc)
        quote_ts = datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc)   # prior day close

        q = _q("^STOXX50E", "EURO STOXX 50", quote_ts)
        meta = classify_quote_freshness(
            quote=q,
            generated_at=generated,
            session_key="europe_midday",
            timezone_name="Europe/Madrid",
        )
        assert meta.freshness_state == FRESHNESS_PRIOR_CLOSE
        # Should have a warning indicating provider returned prior close during open session
        assert meta.warning is not None

    def test_europe_out_of_hours_always_prior_close(self):
        """Europe equity with a prior close timestamp outside trading hours labels as prior_close (no warning)."""
        # 02:00 UTC is outside European market hours
        generated = datetime(2026, 5, 12, 1, 0, tzinfo=timezone.utc)
        quote_ts = datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc)

        q = _q("^STOXX50E", "EURO STOXX 50", quote_ts)
        meta = classify_quote_freshness(
            quote=q,
            generated_at=generated,
            session_key="morning",
            timezone_name="Europe/Madrid",
        )
        assert meta.freshness_state == FRESHNESS_PRIOR_CLOSE

    def test_data_basis_europe_prior_close_during_open_session_annotated(self):
        """build_data_basis_lines annotates when Europe cash is open but provider returned prior close."""
        generated = datetime(2026, 5, 12, 9, 30, tzinfo=timezone.utc)
        prior_ts = datetime(2026, 5, 11, 15, 30, tzinfo=timezone.utc)

        eu_q = _q("^STOXX50E", "EURO STOXX 50", prior_ts)
        us_q = _q("SPY", "S&P 500", prior_ts)

        lines = build_data_basis_lines(
            session_key="europe_midday",
            generated_at=generated,
            timezone_name="Europe/Madrid",
            index_quotes=[eu_q, us_q],
            macro_quotes=[],
            watchlist_quotes=[],
        )
        full = " ".join(lines).lower()
        # Should contain a note about prior close / provider context
        assert "prior" in full or "stale" in full


# ---------------------------------------------------------------------------
# Part 4: Brent stale handling tests
# ---------------------------------------------------------------------------

class TestBrentStaleHandling:
    """Brent stale detection and deduplication."""

    def test_brent_stale_replaces_confirms_language(self):
        """Stale Brent: 'Brent confirms' is replaced with stale language."""
        guard = BriefingQualityGuard(brent_stale=True)
        sections = ["<b>OIL</b>\nBrent confirms energy stress. WTI +2%."]
        result = guard.apply(sections)
        assert "Brent confirms" not in "\n".join(result)

    def test_brent_stale_caveat_deduplicated(self):
        """Brent stale caveat appears at most once across all sections."""
        guard = BriefingQualityGuard(brent_stale=True)
        sections = [
            "<b>OIL</b>\nBrent stale/provider-held; WTI used for live energy impulse.",
            "<b>GEO RISK</b>\nBrent stale/provider-held; WTI used for live energy impulse.",
        ]
        result = guard.apply(sections)
        full = "\n".join(result)
        import re
        count = len(re.findall(r"Brent stale", full, re.IGNORECASE))
        assert count <= 1, f"Expected Brent stale caveat at most once, found {count}"

    def test_brent_fresh_shown_normally(self):
        """Fresh Brent: shown without stale label."""
        guard = BriefingQualityGuard(brent_stale=False)
        sections = ["<b>OIL</b>\nBrent confirms energy stress."]
        result = guard.apply(sections)
        assert "Brent confirms" in "\n".join(result)
        assert "stale" not in "\n".join(result).lower()

    def test_flags_from_briefing_brent_stale_via_freshness(self):
        """flags_from_briefing detects brent_stale when freshness state is 'stale'."""
        ts = _now()
        briefing = MorningBriefing(
            generated_at=ts,
            quote_freshness={"BRENT": {"freshness_state": "stale", "freshness_label": "stale"}},
        )
        flags = flags_from_briefing(briefing)
        assert flags["brent_stale"] is True

    def test_flags_from_briefing_brent_fresh(self):
        """flags_from_briefing returns brent_stale=False when Brent has live freshness."""
        ts = _now()
        briefing = MorningBriefing(
            generated_at=ts,
            quote_freshness={"BRENT": {"freshness_state": "near_real_time", "freshness_label": "live"}},
        )
        flags = flags_from_briefing(briefing)
        assert flags["brent_stale"] is False


# ---------------------------------------------------------------------------
# Part 5: US holiday data freshness tests
# ---------------------------------------------------------------------------

class TestUSHolidayDataFreshness:
    """On a US market holiday, US equity basis must never say near-real-time."""

    def test_memorial_day_us_basis_is_not_near_real_time(self):
        """build_data_basis_lines on Memorial Day must not say 'near-real-time' for US equities."""
        # 2026-05-25 15:00 UTC: Memorial Day, NYSE closed
        generated = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
        # Simulate a quote with a recent timestamp (as if provider returned something)
        recent_ts = datetime(2026, 5, 25, 14, 30, tzinfo=timezone.utc)
        us_q = _q("SPY", "S&P 500", recent_ts)

        lines = build_data_basis_lines(
            session_key="us_pre_open",
            generated_at=generated,
            timezone_name="Europe/Madrid",
            index_quotes=[us_q],
            macro_quotes=[],
            watchlist_quotes=[],
        )
        full = " ".join(lines).lower()
        # Must not claim live/near-real-time US equity data on a holiday.
        # "not live" is acceptable (appears in "Friday close, not live").
        # "live scan" is acceptable (news scan line).
        sanitised = full.replace("not live", "").replace("live scan", "")
        assert "near-real-time" not in sanitised, (
            f"US equity basis should not be near-real-time on a holiday. Lines: {lines}"
        )
        assert "holiday" in full or "friday close" in full, (
            f"US holiday context should appear in basis lines. Lines: {lines}"
        )

    def test_memorial_day_basis_includes_holiday_caveat(self):
        """build_data_basis_lines on Memorial Day must include a holiday caveat line."""
        generated = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
        recent_ts = datetime(2026, 5, 25, 14, 30, tzinfo=timezone.utc)
        us_q = _q("SPY", "S&P 500", recent_ts)

        lines = build_data_basis_lines(
            session_key="morning",
            generated_at=generated,
            timezone_name="Europe/Madrid",
            index_quotes=[us_q],
            macro_quotes=[],
            watchlist_quotes=[],
        )
        # At least one line must mention the holiday
        holiday_lines = [l for l in lines if "memorial day" in l.lower() or "holiday" in l.lower()]
        assert holiday_lines, f"Expected at least one holiday caveat line. Lines: {lines}"

    def test_non_holiday_weekday_us_basis_can_be_near_real_time(self):
        """On a regular trading day, near-real-time US basis is valid."""
        # 2026-05-26 15:00 UTC: regular Tuesday
        generated = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)
        recent_ts = datetime(2026, 5, 26, 14, 50, tzinfo=timezone.utc)  # 10 min ago
        us_q = _q("SPY", "S&P 500", recent_ts)

        lines = build_data_basis_lines(
            session_key="us_intraday_risk",
            generated_at=generated,
            timezone_name="Europe/Madrid",
            index_quotes=[us_q],
            macro_quotes=[],
            watchlist_quotes=[],
        )
        full = " ".join(lines).lower()
        # Should NOT have a holiday caveat on a normal day
        assert "memorial day" not in full
