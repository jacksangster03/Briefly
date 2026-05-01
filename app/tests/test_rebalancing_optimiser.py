"""Tests for optimisation-enabled rebalancing."""

from __future__ import annotations

import pandas as pd
import pytest


def test_normalise_weights_dataframe():
    from app.rebalancing.optimiser import _normalise_weights

    df = pd.DataFrame({"weights": [0.7, 0.3]}, index=["AAPL", "MSFT"])
    out = _normalise_weights(df, ["AAPL", "MSFT"])
    assert out["AAPL"] == pytest.approx(0.7, abs=1e-6)
    assert out["MSFT"] == pytest.approx(0.3, abs=1e-6)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)


def test_optimise_weights_rejects_bad_method():
    from app.rebalancing.optimiser import optimise_weights

    returns = pd.DataFrame({"AAPL": [0.01, -0.01], "MSFT": [0.005, 0.002]})
    with pytest.raises(ValueError):
        optimise_weights(returns, method="unsupported")


def test_optimise_weights_rejects_too_few_assets():
    from app.rebalancing.optimiser import optimise_weights

    returns = pd.DataFrame({"AAPL": [0.01, -0.01]})
    with pytest.raises(ValueError):
        optimise_weights(returns, method="hrp")


def test_compute_rebalance_uses_optimiser_path(monkeypatch):
    from app.rebalancing import service

    returns = pd.DataFrame({"AAPL": [0.01, -0.02, 0.01], "MSFT": [0.0, 0.01, -0.01]})

    monkeypatch.setattr(service, "_fetch_returns_for_symbols", lambda symbols: returns)

    def _fake_opt(df, method="hrp"):
        assert method == "hrp"
        assert list(df.columns) == ["AAPL", "MSFT"]
        return {"AAPL": 0.6, "MSFT": 0.4}

    monkeypatch.setattr("app.rebalancing.optimiser.optimise_weights", _fake_opt)

    rows = [
        {
            "asset_class": "equities",
            "actual_pct": 100.0,
            "target_pct": 100.0,
            "min_pct": 90.0,
            "max_pct": 100.0,
            "drift_pct": 0.0,
            "status": "within_band",
        }
    ]
    holdings = [{"symbol": "AAPL", "weight_pct": 50.0}, {"symbol": "MSFT", "weight_pct": 50.0}]
    config = {"method": "hrp", "min_trade_pct": 0.5, "transaction_cost_bps": 10.0}

    proposal = service.compute_rebalance_proposal("default_user", rows, holdings, config)
    active = [t for t in proposal["trades"] if not t.get("skipped", True)]
    assert len(active) == 2
    labels = {t["label"] for t in active}
    assert labels == {"AAPL", "MSFT"}
    assert all(t["status"] == "optimised" for t in active)


def test_compute_rebalance_falls_back_when_returns_unavailable(monkeypatch):
    from app.rebalancing import service

    monkeypatch.setattr(service, "_fetch_returns_for_symbols", lambda symbols: None)

    rows = [
        {
            "asset_class": "equities",
            "label": "Equities",
            "actual_pct": 60.0,
            "target_pct": 50.0,
            "min_pct": 45.0,
            "max_pct": 55.0,
            "drift_pct": 10.0,
            "status": "above_band",
        }
    ]
    holdings = [{"symbol": "AAPL", "weight_pct": 60.0}]
    config = {"method": "hrp", "min_trade_pct": 0.5, "transaction_cost_bps": 10.0}

    proposal = service.compute_rebalance_proposal("default_user", rows, holdings, config)
    active = [t for t in proposal["trades"] if not t.get("skipped", True)]
    assert len(active) == 1
    assert active[0]["label"] == "Equities"
    assert active[0]["status"] == "above_band"
