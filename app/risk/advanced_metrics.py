"""Advanced portfolio risk and performance metrics (Phase 5.9).

Pure functions. Inputs are daily return series (lists of floats, one per trading day).
All annualisation uses 252 trading days. Monthly aggregation uses non-overlapping 21-day blocks.

This module deliberately has no DB or web dependencies so it stays unit-testable.
"""

from __future__ import annotations

import math
from typing import Any

_TRADING_DAYS = 252
_MONTH_DAYS = 21


def _safe_float(value: float, digits: int = 6) -> float:
    if value is None or math.isnan(value) or math.isinf(value):
        return float("nan")
    return round(float(value), digits)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: list[float], ddof: int = 1) -> float:
    n = len(values)
    if n <= ddof:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (n - ddof)


def _std(values: list[float], ddof: int = 1) -> float:
    return math.sqrt(_variance(values, ddof=ddof))


def _covariance(a: list[float], b: list[float], ddof: int = 1) -> float:
    n = min(len(a), len(b))
    if n <= ddof:
        return 0.0
    ma = _mean(a[:n])
    mb = _mean(b[:n])
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - ddof)


def _correlation(a: list[float], b: list[float]) -> float:
    sa = _std(a)
    sb = _std(b)
    if sa == 0 or sb == 0:
        return float("nan")
    return _covariance(a, b) / (sa * sb)


def _to_monthly_returns(daily_returns: list[float]) -> list[float]:
    """Compound non-overlapping 21-day blocks into monthly returns.

    The trailing remainder block is included if it has at least 5 observations,
    so very short lookbacks still produce something usable.
    """
    monthly: list[float] = []
    n = len(daily_returns)
    i = 0
    while i + _MONTH_DAYS <= n:
        block = daily_returns[i : i + _MONTH_DAYS]
        prod = 1.0
        for r in block:
            prod *= 1 + r
        monthly.append(prod - 1)
        i += _MONTH_DAYS
    remainder = daily_returns[i:]
    if len(remainder) >= 5:
        prod = 1.0
        for r in remainder:
            prod *= 1 + r
        monthly.append(prod - 1)
    return monthly


def _max_drawdown_pct(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily_returns:
        cum *= 1 + r
        peak = max(peak, cum)
        dd = (cum - peak) / peak
        max_dd = min(max_dd, dd)
    return max_dd * 100


def compute_advanced_metrics(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    risk_free_rate_pct: float,
) -> dict[str, Any]:
    """Compute the extended Phase 5.9 metric set.

    Returns a dict with raw numbers (suffix `_pct` where percentage, ratios unitless)
    plus pre-formatted display strings for direct rendering in templates.
    """
    n = min(len(portfolio_returns), len(benchmark_returns))
    if n < 5:
        return {"available": False, "reason": f"Need at least 5 observations, got {n}."}

    port = portfolio_returns[:n]
    bench = benchmark_returns[:n]

    rf_daily = (1 + risk_free_rate_pct / 100) ** (1 / _TRADING_DAYS) - 1

    cum_port = 1.0
    for r in port:
        cum_port *= 1 + r
    total_return_pct = (cum_port - 1) * 100

    cum_bench = 1.0
    for r in bench:
        cum_bench *= 1 + r
    bench_total_return_pct = (cum_bench - 1) * 100

    annual_port = ((1 + _mean(port)) ** _TRADING_DAYS - 1) * 100
    annual_bench = ((1 + _mean(bench)) ** _TRADING_DAYS - 1) * 100

    monthly_port = _to_monthly_returns(port)
    monthly_bench = _to_monthly_returns(bench)
    n_months = len(monthly_port)

    if n_months >= 1:
        mp = 1.0
        for r in monthly_port:
            mp *= 1 + r
        avg_monthly_geom_pct = (mp ** (1 / n_months) - 1) * 100
    else:
        avg_monthly_geom_pct = float("nan")

    var_bench = _variance(bench)
    cov_pb = _covariance(port, bench)
    beta = cov_pb / var_bench if var_bench > 0 else float("nan")

    corr = _correlation(port, bench)
    r_squared = corr * corr if not math.isnan(corr) else float("nan")

    if not math.isnan(beta) and beta != 0:
        treynor = (annual_port - risk_free_rate_pct) / (beta * 100)
    else:
        treynor = float("nan")

    if not math.isnan(beta):
        capm_expected_pct = risk_free_rate_pct + beta * (annual_bench - risk_free_rate_pct)
        jensens_alpha_pct = annual_port - capm_expected_pct
    else:
        capm_expected_pct = float("nan")
        jensens_alpha_pct = float("nan")

    if monthly_port:
        losses = [r for r in monthly_port if r < 0]
        prob_loss_pct = len(losses) / n_months * 100
        avg_loss_pct = (_mean(losses) * 100) if losses else 0.0
    else:
        prob_loss_pct = float("nan")
        avg_loss_pct = float("nan")

    downside = [r - rf_daily for r in port if r < rf_daily]
    if downside:
        downside_dev = math.sqrt(_mean([d * d for d in downside]))
        downside_risk_pct = downside_dev * math.sqrt(_TRADING_DAYS) * 100
    else:
        downside_risk_pct = 0.0

    if n_months >= 1:
        active_monthly = [p - b for p, b in zip(monthly_port, monthly_bench)]
        under = [a for a in active_monthly if a < 0]
        over = [a for a in active_monthly if a > 0]
        prob_under_pct = len(under) / n_months * 100
        prob_over_pct = len(over) / n_months * 100
        avg_under_pct = (_mean(under) * 100) if under else 0.0
        avg_over_pct = (_mean(over) * 100) if over else 0.0

        bull_active = [active_monthly[i] for i in range(n_months) if monthly_bench[i] >= 0]
        bear_active = [active_monthly[i] for i in range(n_months) if monthly_bench[i] < 0]
        avg_bull_active_pct = (_mean(bull_active) * 100) if bull_active else 0.0
        avg_bear_active_pct = (_mean(bear_active) * 100) if bear_active else 0.0
        bull_months = len(bull_active)
        bear_months = len(bear_active)
    else:
        prob_under_pct = float("nan")
        prob_over_pct = float("nan")
        avg_under_pct = float("nan")
        avg_over_pct = float("nan")
        avg_bull_active_pct = float("nan")
        avg_bear_active_pct = float("nan")
        bull_months = 0
        bear_months = 0

    metrics: dict[str, Any] = {
        "available": True,
        "n_observations": n,
        "n_months": n_months,
        "bull_months": bull_months,
        "bear_months": bear_months,
        "total_return_pct": _safe_float(total_return_pct, 4),
        "benchmark_total_return_pct": _safe_float(bench_total_return_pct, 4),
        "avg_monthly_geom_pct": _safe_float(avg_monthly_geom_pct, 4),
        "annualised_return_pct": _safe_float(annual_port, 4),
        "benchmark_annualised_return_pct": _safe_float(annual_bench, 4),
        "max_drawdown_pct": _safe_float(_max_drawdown_pct(port), 4),
        "beta": _safe_float(beta, 4),
        "r_squared": _safe_float(r_squared, 4),
        "correlation": _safe_float(corr, 4),
        "treynor_ratio": _safe_float(treynor, 4),
        "jensens_alpha_pct": _safe_float(jensens_alpha_pct, 4),
        "capm_expected_return_pct": _safe_float(capm_expected_pct, 4),
        "probability_of_loss_pct": _safe_float(prob_loss_pct, 2),
        "average_loss_pct": _safe_float(avg_loss_pct, 4),
        "downside_risk_pct": _safe_float(downside_risk_pct, 4),
        "probability_of_underperformance_pct": _safe_float(prob_under_pct, 2),
        "average_underperformance_pct": _safe_float(avg_under_pct, 4),
        "probability_of_outperformance_pct": _safe_float(prob_over_pct, 2),
        "average_outperformance_pct": _safe_float(avg_over_pct, 4),
        "average_bull_active_pct": _safe_float(avg_bull_active_pct, 4),
        "average_bear_active_pct": _safe_float(avg_bear_active_pct, 4),
    }

    def _pct_disp(v: float, digits: int = 2) -> str:
        if v is None or math.isnan(v):
            return ""
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.{digits}f}%"

    def _ratio_disp(v: float, digits: int = 2) -> str:
        if v is None or math.isnan(v):
            return ""
        return f"{v:.{digits}f}"

    metrics["display"] = {
        "avg_monthly_geom": _pct_disp(metrics["avg_monthly_geom_pct"]),
        "beta": _ratio_disp(metrics["beta"]),
        "r_squared": _ratio_disp(metrics["r_squared"], 3),
        "treynor_ratio": _ratio_disp(metrics["treynor_ratio"]),
        "jensens_alpha": _pct_disp(metrics["jensens_alpha_pct"]),
        "capm_expected_return": _pct_disp(metrics["capm_expected_return_pct"]),
        "probability_of_loss": (
            f"{metrics['probability_of_loss_pct']:.1f}%"
            if not math.isnan(metrics["probability_of_loss_pct"])
            else ""
        ),
        "average_loss": _pct_disp(metrics["average_loss_pct"]),
        "downside_risk": _pct_disp(metrics["downside_risk_pct"]),
        "probability_of_underperformance": (
            f"{metrics['probability_of_underperformance_pct']:.1f}%"
            if not math.isnan(metrics["probability_of_underperformance_pct"])
            else ""
        ),
        "average_underperformance": _pct_disp(metrics["average_underperformance_pct"]),
        "probability_of_outperformance": (
            f"{metrics['probability_of_outperformance_pct']:.1f}%"
            if not math.isnan(metrics["probability_of_outperformance_pct"])
            else ""
        ),
        "average_outperformance": _pct_disp(metrics["average_outperformance_pct"]),
        "average_bull_active": _pct_disp(metrics["average_bull_active_pct"]),
        "average_bear_active": _pct_disp(metrics["average_bear_active_pct"]),
    }

    return metrics


def classify_beta(beta: float) -> str:
    if math.isnan(beta):
        return "unavailable"
    if beta < 0.5:
        return "low-beta"
    if beta < 0.8:
        return "defensive"
    if beta < 1.2:
        return "market-like"
    if beta < 1.5:
        return "aggressive"
    return "high-beta"


def classify_r_squared(r2: float) -> str:
    if math.isnan(r2):
        return "unavailable"
    if r2 < 0.5:
        return "low-explanation"
    if r2 < 0.75:
        return "moderate-explanation"
    if r2 < 0.9:
        return "high-explanation"
    return "very-high-explanation"


def classify_alpha(alpha_pct: float) -> str:
    if math.isnan(alpha_pct):
        return "unavailable"
    if alpha_pct > 2:
        return "strong-positive"
    if alpha_pct > 0:
        return "positive"
    if alpha_pct > -2:
        return "negative"
    return "strong-negative"
