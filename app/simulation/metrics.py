"""Metrics and chart payload assembly for simulation outputs."""

from __future__ import annotations

from typing import Any

import numpy as np


def summarize_paths(
    *,
    paths: np.ndarray,
    start_value: float,
    benchmark_paths: np.ndarray | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scaled = paths * float(start_value)
    terminal = scaled[:, -1]
    terminal_returns = (terminal / float(start_value)) - 1.0
    drawdowns = np.array([_max_drawdown(path) for path in scaled], dtype=float)

    p05, p25, p50, p75, p95 = np.percentile(terminal, [5, 25, 50, 75, 95]).tolist()
    prob_loss = float(np.mean(terminal < start_value))
    var95 = float(np.percentile(terminal_returns, 5) * 100.0)
    cvar95 = float(np.mean(terminal_returns[terminal_returns <= np.percentile(terminal_returns, 5)]) * 100.0)
    prob_dd_20 = float(np.mean(drawdowns <= -0.2))

    prob_outperform = None
    if benchmark_paths is not None and benchmark_paths.size:
        bench_scaled = benchmark_paths * float(start_value)
        prob_outperform = float(np.mean(scaled[:, -1] > bench_scaled[:, -1]))

    summary = {
        "median_terminal_value": round(float(p50), 2),
        "percentile_5_terminal_value": round(float(p05), 2),
        "percentile_25_terminal_value": round(float(p25), 2),
        "percentile_75_terminal_value": round(float(p75), 2),
        "percentile_95_terminal_value": round(float(p95), 2),
        "probability_of_loss_pct": round(prob_loss * 100.0, 2),
        "probability_drawdown_breach_20_pct": round(prob_dd_20 * 100.0, 2),
        "var_95_pct": round(var95, 2),
        "cvar_95_pct": round(cvar95, 2),
        "probability_outperform_benchmark_pct": round(prob_outperform * 100.0, 2) if prob_outperform is not None else None,
    }

    metrics = {
        "terminal_return_mean_pct": round(float(np.mean(terminal_returns) * 100.0), 2),
        "terminal_return_std_pct": round(float(np.std(terminal_returns) * 100.0), 2),
        "terminal_return_median_pct": round(float(np.median(terminal_returns) * 100.0), 2),
        "max_drawdown_median_pct": round(float(np.median(drawdowns) * 100.0), 2),
        "max_drawdown_worst_pct": round(float(np.min(drawdowns) * 100.0), 2),
        "simulations": int(paths.shape[0]),
    }

    fan = np.percentile(scaled, [5, 25, 50, 75, 95], axis=0)
    chart_payload = {
        "fan_chart": {
            "x": list(range(scaled.shape[1])),
            "p05": [round(float(v), 4) for v in fan[0]],
            "p25": [round(float(v), 4) for v in fan[1]],
            "p50": [round(float(v), 4) for v in fan[2]],
            "p75": [round(float(v), 4) for v in fan[3]],
            "p95": [round(float(v), 4) for v in fan[4]],
        },
        "terminal_histogram": _histogram_payload(terminal, bins=36),
        "drawdown_histogram": _histogram_payload(drawdowns * 100.0, bins=36),
        "terminal_values": [round(float(v), 4) for v in terminal.tolist()],
        "drawdown_values_pct": [round(float(v * 100.0), 4) for v in drawdowns.tolist()],
        "sample_paths": [
            [round(float(v), 4) for v in scaled[idx]]
            for idx in np.linspace(0, scaled.shape[0] - 1, num=min(30, scaled.shape[0]), dtype=int)
        ],
    }

    return summary, metrics, chart_payload


def build_sensitivity_payload(
    *,
    values: list[dict[str, Any]],
) -> dict[str, Any]:
    if not values:
        return {
            "available": False,
            "reason": "Sensitivity analysis not run for this simulation.",
        }
    return {
        "available": True,
        "x": [round(float(item["growth_shock"]), 2) for item in values],
        "y_median_terminal": [round(float(item["median_terminal_value"]), 2) for item in values],
        "y_prob_loss": [round(float(item["probability_of_loss"]), 6) for item in values],
    }


def _max_drawdown(path: np.ndarray) -> float:
    running_max = float(path[0])
    min_dd = 0.0
    for value in path:
        running_max = max(running_max, float(value))
        dd = float(value) / running_max - 1.0
        if dd < min_dd:
            min_dd = dd
    return min_dd


def _histogram_payload(values: np.ndarray, bins: int = 30) -> dict[str, Any]:
    counts, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    return {
        "x": [round(float(v), 4) for v in centers.tolist()],
        "y": [int(v) for v in counts.tolist()],
    }
