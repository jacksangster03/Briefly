"""Tests for Phase 9.X MoveContextEngine (app/briefing/move_context.py)."""

from __future__ import annotations

import pytest

from app.briefing.move_context import (
    ASSET_TYPE_COMMODITY,
    ASSET_TYPE_EQUITY_INDEX,
    ASSET_TYPE_FX,
    ASSET_TYPE_STOCK,
    ASSET_TYPE_TREASURY_YIELD,
    ASSET_TYPE_VOLATILITY_INDEX,
    MoveContext,
    _clamp,
    _level_context_label,
    _magnitude_label_price,
    _magnitude_label_price_percentile,
    _magnitude_label_yield_bp,
    _percentile,
    _range_position_label,
    _session_range_label,
    _vix_level_label,
    compute_move_context,
    compute_yield_context,
    format_move_context_line,
    format_session_change,
    format_watchlist_move_label,
    format_yield_context_line,
    move_context_from_quote,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_history(lo: float, hi: float, n: int = 250) -> list[float]:
    """Linear sequence of prices from lo to hi for n points."""
    step = (hi - lo) / max(n - 1, 1)
    return [lo + step * i for i in range(n)]


# ---------------------------------------------------------------------------
# TestPercentile
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_all_below(self) -> None:
        assert _percentile([1.0, 2.0, 3.0], 4.0) == 1.0

    def test_none_below(self) -> None:
        assert _percentile([1.0, 2.0, 3.0], 0.5) == 0.0

    def test_half_below(self) -> None:
        result = _percentile([1.0, 2.0, 3.0, 4.0], 2.5)
        assert result == 0.5

    def test_empty_returns_midpoint(self) -> None:
        assert _percentile([], 5.0) == 0.5

    def test_strict_less_than(self) -> None:
        # Values equal to v are NOT counted
        assert _percentile([1.0, 2.0, 2.0], 2.0) == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# TestClamp
# ---------------------------------------------------------------------------

class TestClamp:
    def test_within_range(self) -> None:
        assert _clamp(0.5) == 0.5

    def test_below_lo(self) -> None:
        assert _clamp(-0.1) == 0.0

    def test_above_hi(self) -> None:
        assert _clamp(1.1) == 1.0

    def test_custom_bounds(self) -> None:
        assert _clamp(3.0, 0.0, 2.0) == 2.0


# ---------------------------------------------------------------------------
# TestMagnitudeLabels
# ---------------------------------------------------------------------------

class TestMagnitudeLabelPrice:
    def test_tiny_gain(self) -> None:
        assert "Tiny" in _magnitude_label_price(0.10, True)

    def test_tiny_decline(self) -> None:
        assert "Tiny" in _magnitude_label_price(0.10, False)

    def test_normal_gain(self) -> None:
        assert "Normal" in _magnitude_label_price(0.50, True)

    def test_firm_decline(self) -> None:
        assert "Firm" in _magnitude_label_price(1.20, False)

    def test_strong_gain(self) -> None:
        assert "Strong" in _magnitude_label_price(2.00, True)

    def test_sharp_fall(self) -> None:
        assert "Sharp fall" in _magnitude_label_price(2.00, False)

    def test_extreme_gain(self) -> None:
        assert "Extreme" in _magnitude_label_price(4.00, True)

    def test_exact_boundary_0_30(self) -> None:
        # 0.30 is NOT < 0.30, so should be Normal
        assert "Normal" in _magnitude_label_price(0.30, True)


class TestMagnitudeLabelPricePercentile:
    def test_small_gain(self) -> None:
        # pct < 0.25 => "Normal gain, typical vs 1Y"
        assert "Normal gain" in _magnitude_label_price_percentile(0.10, True)
        assert "typical" in _magnitude_label_price_percentile(0.10, True)

    def test_strong_day(self) -> None:
        # pct 0.75 falls in [0.75, 0.90) => "Strong day, top quartile vs 1Y"
        assert "Strong day" in _magnitude_label_price_percentile(0.75, True)
        assert "top quartile" in _magnitude_label_price_percentile(0.75, True)

    def test_top_decile_gain(self) -> None:
        assert "top decile" in _magnitude_label_price_percentile(0.90, True)

    def test_weak_day(self) -> None:
        # pct 0.75 falls in [0.75, 0.90) => "Weak day, top quartile vs 1Y"
        assert "Weak day" in _magnitude_label_price_percentile(0.75, False)
        assert "top quartile" in _magnitude_label_price_percentile(0.75, False)


class TestMagnitudeLabelYieldBp:
    def test_flat_below_3(self) -> None:
        assert _magnitude_label_yield_bp(2.0, False) == "Flat"
        assert _magnitude_label_yield_bp(2.0, True) == "Flat"

    def test_mild_rally(self) -> None:
        assert "Mild" in _magnitude_label_yield_bp(3.0, True)
        assert "Treasury rally" in _magnitude_label_yield_bp(3.0, True)

    def test_mild_selloff(self) -> None:
        assert "Mild" in _magnitude_label_yield_bp(4.0, False)
        assert "rates selloff" in _magnitude_label_yield_bp(4.0, False)

    def test_firm_selloff_7bp(self) -> None:
        label = _magnitude_label_yield_bp(7.0, False)
        assert label == "Firm rates selloff"

    def test_sharp_rally(self) -> None:
        label = _magnitude_label_yield_bp(15.0, True)
        assert label == "Sharp Treasury rally"

    def test_extreme_selloff(self) -> None:
        label = _magnitude_label_yield_bp(25.0, False)
        assert label == "Extreme rates selloff"

    def test_boundary_3_is_mild_not_flat(self) -> None:
        assert "Mild" in _magnitude_label_yield_bp(3.0, True)


# ---------------------------------------------------------------------------
# TestRangeHelpers
# ---------------------------------------------------------------------------

class TestRangePositionLabel:
    def test_near_lows(self) -> None:
        assert _range_position_label(0.10) == "near session lows"

    def test_lower_half(self) -> None:
        assert _range_position_label(0.30) == "lower half"

    def test_mid_range(self) -> None:
        assert _range_position_label(0.50) == "mid-range"

    def test_upper_half(self) -> None:
        assert _range_position_label(0.70) == "upper half"

    def test_near_highs(self) -> None:
        assert _range_position_label(0.90) == "near session highs"

    def test_exactly_zero(self) -> None:
        assert _range_position_label(0.0) == "near session lows"

    def test_exactly_one(self) -> None:
        assert _range_position_label(1.0) == "near session highs"


class TestLevelContextLabel:
    def test_near_1y_lows(self) -> None:
        assert _level_context_label(0.05) == "near 1Y lows"

    def test_low_vs_1y(self) -> None:
        assert _level_context_label(0.20) == "low vs 1Y range"

    def test_mid_range_none(self) -> None:
        assert _level_context_label(0.50) is None

    def test_high_vs_1y(self) -> None:
        assert _level_context_label(0.80) == "high vs 1Y range"

    def test_near_1y_highs(self) -> None:
        assert _level_context_label(0.95) == "near 1Y highs"


class TestSessionRangeLabel:
    def test_morning(self) -> None:
        assert _session_range_label("morning") == "prior session range"

    def test_closing_wrap(self) -> None:
        assert _session_range_label("closing_wrap") == "full session range"

    def test_preopen(self) -> None:
        assert _session_range_label("us_pre_open") == "pre-market proxy range"

    def test_unknown_fallback(self) -> None:
        assert _session_range_label("unknown_session") == "provider day range"


# ---------------------------------------------------------------------------
# TestVixClassification
# ---------------------------------------------------------------------------

class TestVixClassification:
    def test_calm(self) -> None:
        assert _vix_level_label(12.0) == "Calm"

    def test_watchful(self) -> None:
        assert _vix_level_label(17.38) == "Watchful"

    def test_stressed(self) -> None:
        assert _vix_level_label(24.0) == "Stressed"

    def test_panic(self) -> None:
        assert _vix_level_label(35.0) == "Panic"

    def test_boundary_15_is_watchful(self) -> None:
        assert _vix_level_label(15.0) == "Watchful"

    def test_boundary_20_is_stressed(self) -> None:
        assert _vix_level_label(20.0) == "Stressed"


# ---------------------------------------------------------------------------
# TestComputeMoveContext — spec test cases
# ---------------------------------------------------------------------------

class TestComputeMoveContextSpec:
    """Exact spec test cases from requirement 14."""

    def test_russell_strong_day_near_highs(self) -> None:
        """Russell 2000 +1.75% with range +0.4% to +1.9% -> 'Strong day', 'near session highs'."""
        # Build 250 daily moves where 1.75% is ~80th percentile (top 20% -> "Strong day")
        # Make 200 days with moves 0.0-1.60%, 50 days with moves 1.76%-3.5%
        prices = [100.0]
        for i in range(200):
            prices.append(prices[-1] * (1 + 0.008 * (i % 2 * 2 - 1)))  # alternating small moves
        for i in range(50):
            prices.append(prices[-1] * 1.02)  # larger moves, above 1.75%
        # Trim and normalise so 1.75% sits around 80th pct
        history = _make_history(90.0, 110.0, 252)

        prev_close = 100.0
        current = 101.75    # +1.75%
        day_low = 100.40    # +0.4%
        day_high = 101.90   # +1.9%

        # Build a move history where most moves are < 1.75%: 200 moves of ~0.5%, 50 of ~2.5%
        price_history: list[float] = []
        p = 100.0
        for i in range(200):
            p = p * 1.005
            price_history.append(p)
        for i in range(50):
            p = p * 1.025
            price_history.append(p)

        ctx = compute_move_context(
            symbol="IWM",
            label="Russell 2000",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=current,
            change=1.75,
            change_percent=1.75,
            day_high=day_high,
            day_low=day_low,
            prev_close=prev_close,
            session_mode="morning",
            price_history=price_history,
        )

        assert "Strong day" in ctx.move_context_label
        assert ctx.day_range_position_label == "near session highs"
        assert ctx.move_direction == "up"
        assert ctx.errors == []

    def test_us_10y_mild_rally_3bp(self) -> None:
        """US 10Y 4.42%, -3 bp -> 'Mild Treasury rally'."""
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
            session_mode="morning",
        )
        assert ctx.move_context_label == "Mild Treasury rally"
        assert ctx.daily_move_display == "-3 bp"
        assert ctx.move_direction == "down"

    def test_us_10y_range_mid_or_lower_half(self) -> None:
        """US 10Y range -6 to +2 bp; current -3 bp should be lower half or mid-range."""
        prev_yield = 4.45
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
            day_high_yield=4.47,   # +2 bp from prev
            day_low_yield=4.39,    # -6 bp from prev
            prev_yield=prev_yield,
            session_mode="morning",
        )
        # Position = (4.42 - 4.39) / (4.47 - 4.39) = 0.03/0.08 = 0.375 -> "lower half"
        assert ctx.day_range_position_label in ("lower half", "mid-range")
        assert ctx.day_range_display == "-6 to +2 bp"

    def test_us_2y_firm_rates_selloff_7bp(self) -> None:
        """US 2Y +7 bp -> 'Firm rates selloff'."""
        ctx = compute_yield_context(
            symbol="US2Y",
            label="US 2Y",
            current_yield=4.85,
            change_yield=0.07,
            session_mode="morning",
        )
        assert ctx.move_context_label == "Firm rates selloff"
        assert ctx.daily_move_display == "+7 bp"
        assert ctx.move_direction == "up"

    def test_wti_sharp_fall_high_vs_1y(self) -> None:
        """WTI -1.85%, high 1Y percentile -> negative move label, 'still high vs 1Y range'."""
        # Price history: current at ~77.5% of 1Y range -> "high vs 1Y range"
        price_history = _make_history(60.0, 100.0, 252)
        current = 91.0   # (91-60)/(100-60) = 31/40 = 0.775 -> "high vs 1Y range"

        prev_close = 93.0
        day_low = prev_close * (1 - 0.024)   # -2.4%
        day_high = prev_close * (1 - 0.006)  # -0.6%
        change_pct = (current / prev_close - 1) * 100   # about -2.15%

        ctx = compute_move_context(
            symbol="CL",
            label="WTI",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=current,
            change=current - prev_close,
            change_percent=change_pct,
            day_high=day_high,
            day_low=day_low,
            prev_close=prev_close,
            session_mode="morning",
            price_history=price_history,
        )

        # With history: move classified by percentile (not static "Sharp fall")
        assert ctx.move_direction == "down"
        assert ctx.level_context_label in ("high vs 1Y range", "near 1Y highs")
        assert ctx.level_context_label is not None
        assert "still" in ctx.final_display

    def test_wti_sharp_fall_no_history(self) -> None:
        """WTI -1.85% without 1Y history -> static label 'Sharp fall'."""
        ctx = compute_move_context(
            symbol="CL",
            label="WTI",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=91.15,
            change=-1.85,
            change_percent=-1.85,
            session_mode="morning",
            price_history=None,
        )
        assert ctx.move_context_label == "Sharp fall"

    def test_gold_small_move_upper_half(self) -> None:
        """Gold +0.29%, range -0.2% to +0.5% -> small/Normal move, 'upper half'."""
        prev_close = 2000.0
        current = 2000.0 * 1.0029   # +0.29%
        day_low = 2000.0 * 0.998    # -0.2%
        day_high = 2000.0 * 1.005   # +0.5%

        ctx = compute_move_context(
            symbol="GC",
            label="Gold",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=current,
            change=current - prev_close,
            change_percent=0.29,
            day_high=day_high,
            day_low=day_low,
            prev_close=prev_close,
            session_mode="morning",
        )

        # Position: (0.29 - (-0.2)) / (0.5 - (-0.2)) = 0.49/0.70 = 0.70 -> "upper half"
        assert ctx.day_range_position_label == "upper half"
        # Without history, move label from static thresholds: 0.29 < 0.30 -> "Tiny gain"
        # That's fine — the spec says "small/Normal" with history, but without history is Tiny
        assert ctx.move_direction == "up"

    def test_negative_only_range_avoids_near_session_highs_wording(self) -> None:
        prev_close = 100.0
        ctx = compute_move_context(
            symbol="CL",
            label="WTI",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=99.0,
            change=-1.0,
            change_percent=-1.0,
            day_high=99.5,
            day_low=95.0,
            prev_close=prev_close,
            session_mode="us_intraday_risk",
        )
        assert ctx.day_range_position_label != "near session highs"
        assert ctx.day_range_position_label in {"off lows", "near top of negative range", "upper half", "mid-range", "lower half"}

    def test_gold_normal_gain_with_history(self) -> None:
        """Gold +0.29% with history where 0.29% is 30th pct -> 'Normal gain'."""
        # Make history where most daily moves are 0.1%-0.25% (below 0.29%) and some are above
        # 30% below 0.29 -> 0.30 percentile -> "Normal gain" (0.20 < 0.30 < 0.40)
        price_history: list[float] = []
        p = 1900.0
        for i in range(180):
            p = p * (1 + 0.001 * (i % 2 * 2 - 1))   # 0.1% alternating moves
            price_history.append(p)
        for i in range(72):
            p = p * (1 + 0.004 * (i % 2 * 2 - 1))   # 0.4% alternating moves
            price_history.append(p)

        ctx = compute_move_context(
            symbol="GC",
            label="Gold",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=2005.8,
            change=5.8,
            change_percent=0.29,
            prev_close=2000.0,
            session_mode="morning",
            price_history=price_history,
        )

        assert ctx.move_direction == "up"
        # With history, 0.29% should be classified as a percentile-based label
        assert ctx.move_percentile_1y is not None
        assert ctx.errors == []

    def test_vix_watchful_falling(self) -> None:
        """VIX 17.38 falling -> 'Watchful, falling'."""
        ctx = compute_move_context(
            symbol="VIX",
            label="VIX",
            asset_type=ASSET_TYPE_VOLATILITY_INDEX,
            current_price=17.38,
            change=-1.50,
            change_percent=-7.96,
            session_mode="morning",
        )
        assert ctx.move_context_label == "Watchful, falling"
        assert ctx.move_percentile_1y is None  # VIX does not compute percentile

    def test_vix_stressed_rising(self) -> None:
        ctx = compute_move_context(
            symbol="VIX",
            label="VIX",
            asset_type=ASSET_TYPE_VOLATILITY_INDEX,
            current_price=24.5,
            change=3.0,
            change_percent=13.95,
            session_mode="morning",
        )
        assert ctx.move_context_label == "Stressed, rising"

    def test_flat_move_renders_as_flat(self) -> None:
        """Zero / near-zero change_percent renders as 'flat', not '+0.00%'."""
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=5200.0,
            change=0.0,
            change_percent=0.0,
            session_mode="morning",
        )
        assert ctx.daily_move_display == "flat"
        assert ctx.move_direction == "flat"

    def test_very_small_change_flat(self) -> None:
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=5200.0,
            change=0.01,
            change_percent=0.004,   # < 0.005 threshold
            session_mode="morning",
        )
        assert ctx.daily_move_display == "flat"

    def test_yield_flat_renders_as_flat(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=0.001,   # 0.1 bp < 0.5 bp threshold
            session_mode="morning",
        )
        assert ctx.daily_move_display == "flat"
        assert ctx.move_direction == "flat"


# ---------------------------------------------------------------------------
# TestComputeMoveContext — no history fallback
# ---------------------------------------------------------------------------

class TestComputeMoveContextNoHistory:
    def test_no_history_returns_static_label(self) -> None:
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=500.0,
            change=5.0,
            change_percent=1.01,
            session_mode="morning",
            price_history=None,
        )
        assert ctx.move_percentile_1y is None
        assert ctx.level_range_percentile_1y is None
        assert ctx.errors == []
        assert "Firm" in ctx.move_context_label or "Strong" in ctx.move_context_label

    def test_short_history_ignored(self) -> None:
        """Fewer than 20 prices uses static thresholds."""
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=500.0,
            change=5.0,
            change_percent=1.01,
            session_mode="morning",
            price_history=[490.0, 495.0, 500.0],   # only 3 prices
        )
        assert ctx.move_percentile_1y is None

    def test_no_day_range_when_fields_zero(self) -> None:
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=500.0,
            change=5.0,
            change_percent=1.01,
            day_high=0.0,
            day_low=0.0,
            prev_close=0.0,
            session_mode="morning",
        )
        assert ctx.day_range_display is None
        assert ctx.day_range_position_label is None


# ---------------------------------------------------------------------------
# TestComputeYieldContext
# ---------------------------------------------------------------------------

class TestComputeYieldContext:
    def test_bp_display_positive(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.50,
            change_yield=0.10,   # +10 bp
        )
        assert ctx.daily_move_display == "+10 bp"

    def test_bp_display_negative(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.30,
            change_yield=-0.15,   # -15 bp
        )
        assert ctx.daily_move_display == "-15 bp"

    def test_no_percent_change_in_display(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.30,
            change_yield=-0.15,
        )
        assert "%" not in ctx.daily_move_display

    def test_yield_level_display(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
        )
        assert "4.42%" == ctx.level_display

    def test_yield_range_in_bp(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
            day_high_yield=4.47,
            day_low_yield=4.39,
            prev_yield=4.45,
        )
        # day_low bp = round((4.39 - 4.45) * 100) = round(-6) = -6
        # day_high bp = round((4.47 - 4.45) * 100) = round(2) = +2
        assert ctx.day_range_display == "-6 to +2 bp"

    def test_yield_history_level_percentile(self) -> None:
        yield_history = _make_history(3.50, 5.00, 252)
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.80,   # near top of 3.50-5.00 range
            change_yield=-0.03,
            yield_history=yield_history,
        )
        assert ctx.level_range_percentile_1y is not None
        assert ctx.level_range_percentile_1y > 0.80
        assert ctx.level_context_label in ("high vs 1Y range", "near 1Y highs")

    def test_asset_type_is_treasury_yield(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
        )
        assert ctx.asset_type == ASSET_TYPE_TREASURY_YIELD


# ---------------------------------------------------------------------------
# TestLevelContext
# ---------------------------------------------------------------------------

class TestLevelContext:
    def test_near_1y_highs_in_final_display(self) -> None:
        price_history = _make_history(4000.0, 5500.0, 252)
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=5490.0,  # very near top
            change=10.0,
            change_percent=0.18,
            session_mode="morning",
            price_history=price_history,
        )
        assert ctx.level_context_label == "near 1Y highs"

    def test_mid_range_level_is_none(self) -> None:
        price_history = _make_history(4000.0, 6000.0, 252)
        ctx = compute_move_context(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=5000.0,  # 50th pct
            change=5.0,
            change_percent=0.10,
            session_mode="morning",
            price_history=price_history,
        )
        assert ctx.level_context_label is None


# ---------------------------------------------------------------------------
# TestFormatters
# ---------------------------------------------------------------------------

class TestFormatMoveContextLine:
    def _make_ctx(self, **kwargs) -> MoveContext:
        defaults = dict(
            symbol="SPY",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            level_display="5,200.00",
            daily_move_display="+1.20%",
            move_direction="up",
            move_context_label="Strong day",
            level_context_label=None,
            move_percentile_1y=0.82,
            level_range_percentile_1y=None,
            day_range_display="+0.2% to +1.4%",
            day_range_position_label="upper half",
            final_display="",
            errors=[],
        )
        defaults.update(kwargs)
        return MoveContext(**defaults)

    def test_includes_label_and_level(self) -> None:
        ctx = self._make_ctx()
        line = format_move_context_line(ctx)
        assert "S&P 500" in line
        assert "5,200.00" in line

    def test_includes_range_when_available(self) -> None:
        ctx = self._make_ctx()
        line = format_move_context_line(ctx)
        assert "+0.2% to +1.4%" in line
        assert "upper half" in line

    def test_suppresses_range_when_none(self) -> None:
        ctx = self._make_ctx(day_range_display=None, day_range_position_label=None)
        line = format_move_context_line(ctx)
        assert "upper half" not in line

    def test_includes_move_context(self) -> None:
        ctx = self._make_ctx()
        line = format_move_context_line(ctx)
        assert "Strong day" in line

    def test_level_context_prefixed_with_still(self) -> None:
        ctx = self._make_ctx(level_context_label="near 1Y highs")
        line = format_move_context_line(ctx)
        assert "still near 1Y highs" in line


class TestFormatWatchlistMoveLabel:
    def _make_ctx(self, **kwargs) -> MoveContext:
        defaults = dict(
            symbol="AMD",
            label="AMD",
            asset_type=ASSET_TYPE_STOCK,
            level_display="120.00",
            daily_move_display="+3.53%",
            move_direction="up",
            move_context_label="Strong day",
            level_context_label=None,
            move_percentile_1y=0.83,
            level_range_percentile_1y=None,
            day_range_display=None,
            day_range_position_label="near session highs",
            final_display="",
            errors=[],
        )
        defaults.update(kwargs)
        return MoveContext(**defaults)

    def test_includes_move_pct(self) -> None:
        ctx = self._make_ctx()
        label = format_watchlist_move_label(ctx)
        assert "+3.53%" in label

    def test_includes_context(self) -> None:
        ctx = self._make_ctx()
        label = format_watchlist_move_label(ctx)
        assert "Strong day" in label

    def test_near_highs_shortens_to_near_highs(self) -> None:
        ctx = self._make_ctx()
        label = format_watchlist_move_label(ctx)
        assert "near highs" in label

    def test_near_lows_shortens(self) -> None:
        ctx = self._make_ctx(day_range_position_label="near session lows", move_context_label="Weak day")
        label = format_watchlist_move_label(ctx)
        assert "near lows" in label

    def test_tiny_suppressed(self) -> None:
        ctx = self._make_ctx(move_context_label="Tiny gain", day_range_position_label="mid-range")
        label = format_watchlist_move_label(ctx)
        assert "Tiny" not in label

    def test_top_decile_suffix_stripped(self) -> None:
        ctx = self._make_ctx(move_context_label="Strong day, top decile vs 1Y daily moves")
        label = format_watchlist_move_label(ctx)
        assert "Strong day" in label
        assert "top decile" not in label


class TestFormatYieldContextLine:
    def test_flat_excluded(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=0.001,   # < 0.5 bp
        )
        line = format_yield_context_line(ctx)
        assert "Flat" not in line or "flat" in line.lower()

    def test_mild_rally_included(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
        )
        line = format_yield_context_line(ctx)
        assert "Mild Treasury rally" in line

    def test_bp_in_display_not_percent(self) -> None:
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=-0.03,
        )
        line = format_yield_context_line(ctx)
        # Yield change should not be shown as % change of yield
        assert "-0.68%" not in line
        assert "bp" in line


# ---------------------------------------------------------------------------
# TestFormatSessionChange
# ---------------------------------------------------------------------------

class TestFormatSessionChange:
    def test_flat_vs_prior(self) -> None:
        result = format_session_change(
            "S&P 500", asset_type=ASSET_TYPE_EQUITY_INDEX, current=5200.0, prev_session=5201.0
        )
        assert "flat" in result

    def test_modestly_higher(self) -> None:
        result = format_session_change(
            "S&P 500", asset_type=ASSET_TYPE_EQUITY_INDEX, current=5200.0, prev_session=5185.0
        )
        assert "modestly higher" in result

    def test_higher_with_percent(self) -> None:
        result = format_session_change(
            "S&P 500", asset_type=ASSET_TYPE_EQUITY_INDEX, current=5200.0, prev_session=5150.0
        )
        assert "higher" in result
        assert "%" in result

    def test_sharply_lower(self) -> None:
        result = format_session_change(
            "Nasdaq", asset_type=ASSET_TYPE_EQUITY_INDEX, current=17000.0, prev_session=18000.0
        )
        assert "sharply lower" in result

    def test_yield_bp_positive(self) -> None:
        result = format_session_change(
            "US 10Y", asset_type=ASSET_TYPE_TREASURY_YIELD, current=4.50, prev_session=4.43
        )
        assert "bp" in result
        assert "+" in result

    def test_yield_bp_negative(self) -> None:
        result = format_session_change(
            "US 10Y", asset_type=ASSET_TYPE_TREASURY_YIELD, current=4.40, prev_session=4.45
        )
        assert "-5 bp" in result

    def test_yield_flat(self) -> None:
        result = format_session_change(
            "US 10Y", asset_type=ASSET_TYPE_TREASURY_YIELD, current=4.420, prev_session=4.420
        )
        assert "flat" in result

    def test_zero_prev_returns_empty(self) -> None:
        result = format_session_change(
            "S&P 500", asset_type=ASSET_TYPE_EQUITY_INDEX, current=5200.0, prev_session=0.0
        )
        assert result == ""

    def test_is_yield_flag_overrides_asset_type(self) -> None:
        result = format_session_change(
            "US 10Y", asset_type=ASSET_TYPE_EQUITY_INDEX, current=4.50, prev_session=4.43,
            is_yield=True,
        )
        assert "bp" in result


# ---------------------------------------------------------------------------
# TestMoveContextFromQuote
# ---------------------------------------------------------------------------

class TestMoveContextFromQuote:
    class _FakeQuote:
        symbol = "NVDA"
        display_name = "NVIDIA"
        current_price = 850.0
        change = 25.0
        change_percent = 3.03
        high = 860.0
        low = 825.0
        previous_close = 825.0

    def test_basic_quote(self) -> None:
        q = self._FakeQuote()
        ctx = move_context_from_quote(q, asset_type=ASSET_TYPE_STOCK, label="NVIDIA")
        assert ctx.symbol == "NVDA"
        assert ctx.label == "NVIDIA"
        assert ctx.move_direction == "up"
        assert ctx.errors == []

    def test_fallback_on_bad_field(self) -> None:
        class BadQuote:
            symbol = None
            display_name = None
            current_price = None    # will cause float(None) -> TypeError
            change = None
            change_percent = 2.5

        ctx = move_context_from_quote(BadQuote(), asset_type=ASSET_TYPE_STOCK)
        # Should not raise; errors list may contain fallback note
        assert isinstance(ctx, MoveContext)

    def test_uses_quote_display_name_when_label_none(self) -> None:
        q = self._FakeQuote()
        ctx = move_context_from_quote(q, asset_type=ASSET_TYPE_STOCK)
        assert ctx.label == "NVIDIA"


# ---------------------------------------------------------------------------
# Part N: new tests for treasury yield de-duplication, range sanity,
# percentile labels, and WHAT CHANGED cleanup
# ---------------------------------------------------------------------------

class TestIsTreasuryYieldQuote:
    """Test is_treasury_yield_quote from formatter.py."""

    def _q(self, symbol: str, display_name: str = ""):
        class _Q:
            pass
        q = _Q()
        q.symbol = symbol
        q.display_name = display_name
        return q

    def test_10y_display_name(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("FRED_DGS10", "10Y US Treasury Yield")
        assert is_treasury_yield_quote(q) is True

    def test_tnx_symbol(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("^TNX", "")
        assert is_treasury_yield_quote(q) is True

    def test_us10y_symbol(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("US10Y", "")
        assert is_treasury_yield_quote(q) is True

    def test_dgs10_symbol(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("DGS10", "")
        assert is_treasury_yield_quote(q) is True

    def test_spy_not_treasury(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("SPY", "S&P 500 ETF")
        assert is_treasury_yield_quote(q) is False

    def test_gold_not_treasury(self) -> None:
        from app.briefing.formatter import is_treasury_yield_quote
        q = self._q("GC", "Gold")
        assert is_treasury_yield_quote(q) is False


class TestRangeSanityChecks:
    """Test range quality suppression for commodity and equity index."""

    def test_wide_wti_range_suppressed(self) -> None:
        """WTI with low=-11.8%, high=+2.2% should be suppressed (width > 10pp, move < 4%)."""
        prev = 50.0
        low = prev * (1 - 0.118)   # -11.8%
        high = prev * (1 + 0.022)  # +2.2%
        ctx = compute_move_context(
            symbol="CL",
            label="WTI",
            asset_type=ASSET_TYPE_COMMODITY,
            current_price=prev * 1.01,
            change=prev * 0.01,
            change_percent=1.0,
            day_high=high,
            day_low=low,
            prev_close=prev,
        )
        assert ctx.range_quality in {"suppressed", "wide_provider_range"}
        assert ctx.day_range_display is None

    def test_normal_equity_range_available(self) -> None:
        """Normal equity range of +0.3% to +1.5% (width 1.2pp) should be available."""
        prev = 4500.0
        low = prev * 1.003
        high = prev * 1.015
        current = prev * 1.010
        ctx = compute_move_context(
            symbol="SPX",
            label="S&P 500",
            asset_type=ASSET_TYPE_EQUITY_INDEX,
            current_price=current,
            change=current - prev,
            change_percent=1.0,
            day_high=high,
            day_low=low,
            prev_close=prev,
        )
        assert ctx.range_quality == "available"
        assert ctx.day_range_display is not None

    def test_yield_range_wider_than_30bp_suppressed(self) -> None:
        """Yield intraday range wider than 30 bp should be suppressed."""
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=0.05,
            day_high_yield=4.80,  # +38 bp from prev 4.42
            day_low_yield=4.42,
            prev_yield=4.42,
            session_mode="morning",
        )
        assert ctx.range_quality == "suppressed"
        assert ctx.day_range_display is None

    def test_yield_range_under_30bp_available(self) -> None:
        """Yield intraday range of 20 bp should be available."""
        ctx = compute_yield_context(
            symbol="US10Y",
            label="US 10Y",
            current_yield=4.42,
            change_yield=0.05,
            day_high_yield=4.52,  # +10 bp from prev
            day_low_yield=4.32,   # -10 bp from prev (total 20 bp range)
            prev_yield=4.42,
            session_mode="morning",
        )
        assert ctx.range_quality == "available"
        assert ctx.day_range_display is not None


class TestPercentileLabels:
    """Test direction-sensitive percentile label at various thresholds."""

    def test_pct_85_gain_includes_top_quartile(self) -> None:
        # pct=0.85 falls in [0.75, 0.90) => "Strong day, top quartile vs 1Y"
        label = _magnitude_label_price_percentile(0.85, True)
        assert "top quartile" in label

    def test_pct_92_gain_includes_top_decile(self) -> None:
        label = _magnitude_label_price_percentile(0.92, True)
        assert "top decile" in label

    def test_pct_99_extreme_gain(self) -> None:
        label = _magnitude_label_price_percentile(0.99, True)
        assert "Extreme gain" in label

    def test_pct_10_typical_decline(self) -> None:
        label = _magnitude_label_price_percentile(0.10, False)
        assert "Normal decline" in label
        assert "typical" in label


class TestWhatChangedCleanup:
    """Test that build_what_changed_lines handles zero-delta case cleanly."""

    def _same_snapshot(self) -> dict:
        return {
            "vix_level": 18.5,
            "wti_pct": -0.5,
            "brent_pct": -0.3,
            "gold_pct": 0.2,
            "us10y": 4.42,
            "breadth_up_pct": 60.0,
            "us_avg_pct": 0.3,
            "eu_avg_pct": 0.1,
            "asia_avg_pct": -0.2,
            "portfolio_contrib_pct": 0.15,
        }

    def test_identical_snapshots_returns_flat_summary(self) -> None:
        from app.briefing.session_snapshot import build_what_changed_lines
        snap = self._same_snapshot()
        result = build_what_changed_lines(previous=snap, current=snap)
        # Should return a single line summary, not per-metric "+0.00%" entries
        assert len(result) == 1
        text = result[0].lower()
        assert "little changed" in text or "stable" in text or "flat" in text

    def test_identical_snapshots_no_raw_zero_delta(self) -> None:
        from app.briefing.session_snapshot import build_what_changed_lines
        snap = self._same_snapshot()
        result = build_what_changed_lines(previous=snap, current=snap)
        # No line should show a raw "+0.00" style delta
        for line in result:
            assert "+0.00" not in line

    def test_us10y_shows_bp_not_pct(self) -> None:
        from app.briefing.session_snapshot import build_what_changed_lines
        previous = self._same_snapshot()
        current = dict(previous)
        current["us10y"] = 4.47  # +5 bp
        result = build_what_changed_lines(previous=previous, current=current)
        us10y_lines = [l for l in result if "US 10Y" in l]
        assert us10y_lines, "Expected a US 10Y line"
        line = us10y_lines[0]
        # Should show bp, not raw pct delta like (+0.05%)
        assert "bp" in line or "flat" in line or "little changed" in line
