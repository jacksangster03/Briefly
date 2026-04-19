"""Phase 5.8 simulation service tests."""

from __future__ import annotations

import numpy as np

from app.simulation.service import parse_simulation_config, run_simulation


def test_simulation_service_runs_with_stubbed_returns(
    validation_test_settings,
    monkeypatch,
):
    from app.simulation import service as sim_service

    def _stubbed_returns_matrix(*, symbols, benchmark_symbol, lookback_days, frequency):
        rng = np.random.default_rng(7)
        matrix = rng.normal(loc=0.001, scale=0.02, size=(400, len(symbols)))
        benchmark = rng.normal(loc=0.0007, scale=0.015, size=(400,))
        return matrix, benchmark

    monkeypatch.setattr(sim_service, "_fetch_returns_matrix", _stubbed_returns_matrix)

    fallback_holdings = [
        {"symbol": "NVDA", "weight_pct": 30.0},
        {"symbol": "MSFT", "weight_pct": 25.0},
        {"symbol": "AAPL", "weight_pct": 20.0},
        {"symbol": "BND", "weight_pct": 15.0},
        {"symbol": "CASH", "weight_pct": 10.0},
    ]
    payload = {
        "mode": "portfolio",
        "methods": ["monte_carlo", "historical", "bootstrap"],
        "frequency": "monthly",
        "horizon_periods": 36,
        "simulation_count": 1200,
        "assumption_source": "historical",
        "benchmark_symbol": "ACWI",
        "start_value": 100,
    }
    config = parse_simulation_config(
        payload=payload,
        fallback_holdings=fallback_holdings,
        fallback_benchmark_symbol="ACWI",
    )
    result = run_simulation(
        profile_name="default_user",
        settings=validation_test_settings,
        config=config,
        persist=False,
    )
    assert result["summary"]["median_terminal_value"] > 0
    assert "fan_chart" in result["charts"]
    assert "terminal_histogram" in result["charts"]
    assert "sensitivity" in result["charts"]
    assert len(result["scenarios"]) > 0
    assert "monte_carlo" in result["methods"]

