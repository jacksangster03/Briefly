"""Phase 5.8 simulation API + web route tests."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from app.web.app import create_web_app


def test_simulation_routes_and_api(validation_test_settings, monkeypatch):
    from app.simulation import service as sim_service

    def _stubbed_returns_matrix(*, symbols, benchmark_symbol, lookback_days, frequency):
        rng = np.random.default_rng(11)
        matrix = rng.normal(loc=0.001, scale=0.018, size=(420, len(symbols)))
        benchmark = rng.normal(loc=0.0008, scale=0.014, size=(420,))
        return matrix, benchmark

    monkeypatch.setattr(sim_service, "_fetch_returns_matrix", _stubbed_returns_matrix)

    client = TestClient(create_web_app(validation_test_settings))

    simulation_page = client.get("/ui/portfolio/simulation?profile=default_user")
    assert simulation_page.status_code == 200
    assert "Simulation Lab" in simulation_page.text
    assert "Run Simulation" in simulation_page.text

    run_resp = client.post(
        "/api/v1/profile/default_user/simulation/run",
        json={
            "payload": {
                "methods": ["monte_carlo", "historical"],
                "frequency": "monthly",
                "horizon_periods": 24,
                "simulation_count": 800,
            }
        },
    )
    assert run_resp.status_code == 200
    payload = run_resp.json()
    assert payload["summary"]["median_terminal_value"] > 0
    assert "fan_chart" in payload["charts"]

    runs_resp = client.get("/api/v1/profile/default_user/simulation/runs")
    assert runs_resp.status_code == 200
    runs = runs_resp.json()["runs"]
    assert len(runs) >= 1
    run_id = runs[0]["run_id"]

    single_resp = client.get(f"/api/v1/profile/default_user/simulation/runs/{run_id}")
    assert single_resp.status_code == 200
    single = single_resp.json()
    assert single["run_id"] == run_id
    assert "summary" in single
    assert "charts" in single

