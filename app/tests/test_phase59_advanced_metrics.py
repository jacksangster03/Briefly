"""Unit tests for Phase 5.9 advanced risk and performance metrics.

Validates the maths against hand-calculated expected values for known synthetic series.
"""

from __future__ import annotations

import math

from app.risk.advanced_metrics import (
    classify_alpha,
    classify_beta,
    classify_r_squared,
    compute_advanced_metrics,
)


def _approx(a: float, b: float, tol: float = 1e-3) -> bool:
    if math.isnan(a) and math.isnan(b):
        return True
    return abs(a - b) <= tol


def test_returns_unavailable_for_too_few_observations():
    out = compute_advanced_metrics([0.01, -0.01], [0.005, -0.005], risk_free_rate_pct=0.0)
    assert out["available"] is False


def test_zero_returns_zero_metrics():
    port = [0.0] * 252
    bench = [0.0] * 252
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert _approx(out["total_return_pct"], 0.0)
    assert _approx(out["avg_monthly_geom_pct"], 0.0)
    assert math.isnan(out["beta"]) or _approx(out["beta"], 0.0)
    assert _approx(out["probability_of_loss_pct"], 0.0)


def test_perfect_tracking_yields_unit_beta_and_zero_alpha():
    port = [0.001 * (i % 7 - 3) for i in range(252)]
    bench = list(port)
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert _approx(out["beta"], 1.0, tol=1e-6)
    assert _approx(out["r_squared"], 1.0, tol=1e-6)
    assert _approx(out["jensens_alpha_pct"], 0.0, tol=1e-6)
    assert _approx(out["probability_of_underperformance_pct"], 0.0)
    assert _approx(out["probability_of_outperformance_pct"], 0.0)


def test_doubled_portfolio_has_beta_two():
    bench = [0.001 * ((i % 11) - 5) for i in range(252)]
    port = [2 * r for r in bench]
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert _approx(out["beta"], 2.0, tol=1e-6)
    assert _approx(out["r_squared"], 1.0, tol=1e-6)


def test_uncorrelated_series_low_r_squared():
    port = [0.001 if i % 2 == 0 else -0.001 for i in range(252)]
    bench = [0.001 if i % 3 == 0 else -0.001 for i in range(252)]
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert out["r_squared"] < 0.5


def test_loss_probability_counts_negative_months():
    port = [-0.001] * 252
    bench = [0.0] * 252
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert out["probability_of_loss_pct"] == 100.0
    assert out["average_loss_pct"] < 0


def test_bull_bear_split_buckets_correctly():
    bench = [0.001 if i < 126 else -0.001 for i in range(252)]
    port = [0.0015 if i < 126 else -0.0005 for i in range(252)]
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=0.0)
    assert out["available"] is True
    assert out["bull_months"] >= 5
    assert out["bear_months"] >= 5
    assert out["average_bear_active_pct"] > 0


def test_classifiers_map_to_buckets():
    assert classify_beta(0.4) == "low-beta"
    assert classify_beta(1.0) == "market-like"
    assert classify_beta(1.6) == "high-beta"
    assert classify_r_squared(0.95) == "very-high-explanation"
    assert classify_alpha(3.0) == "strong-positive"
    assert classify_alpha(-3.0) == "strong-negative"


def test_display_strings_present_and_formatted():
    bench = [0.001 * ((i % 9) - 4) for i in range(252)]
    port = [1.2 * r + 0.0001 for r in bench]
    out = compute_advanced_metrics(port, bench, risk_free_rate_pct=4.0)
    disp = out["display"]
    assert "%" in disp["avg_monthly_geom"]
    assert disp["beta"]
    assert disp["r_squared"]
    assert disp["treynor_ratio"]
    assert disp["jensens_alpha"]
