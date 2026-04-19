"""Simulation engines for Phase 5.8."""

from __future__ import annotations

from typing import Any

import numpy as np


def run_monte_carlo_paths(
    *,
    mu: np.ndarray,
    cov: np.ndarray,
    weights: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n_assets = len(weights)
    if n_assets == 0:
        return np.ones((simulation_count, horizon_periods + 1), dtype=float)
    draws = rng.multivariate_normal(
        mean=mu,
        cov=_ensure_pos_semidefinite(cov),
        size=(simulation_count, horizon_periods),
    )
    port_rets = np.tensordot(draws, weights, axes=([2], [0]))
    paths = np.cumprod(1.0 + port_rets, axis=1)
    return np.concatenate([np.ones((simulation_count, 1), dtype=float), paths], axis=1)


def run_historical_paths(
    *,
    returns_matrix: np.ndarray,
    weights: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if returns_matrix.size == 0:
        return np.ones((simulation_count, horizon_periods + 1), dtype=float)
    n_obs = returns_matrix.shape[0]
    idx = rng.integers(0, n_obs, size=(simulation_count, horizon_periods))
    sampled = returns_matrix[idx, :]
    port_rets = np.tensordot(sampled, weights, axes=([2], [0]))
    paths = np.cumprod(1.0 + port_rets, axis=1)
    return np.concatenate([np.ones((simulation_count, 1), dtype=float), paths], axis=1)


def run_filtered_historical_paths(
    *,
    returns_matrix: np.ndarray,
    weights: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    target_vol_scale: float = 1.0,
    seed: int = 42,
) -> np.ndarray:
    if returns_matrix.size == 0:
        return np.ones((simulation_count, horizon_periods + 1), dtype=float)
    scaled = returns_matrix * max(0.2, float(target_vol_scale))
    return run_historical_paths(
        returns_matrix=scaled,
        weights=weights,
        horizon_periods=horizon_periods,
        simulation_count=simulation_count,
        seed=seed,
    )


def run_block_bootstrap_paths(
    *,
    returns_matrix: np.ndarray,
    weights: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    block_size: int = 5,
    seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if returns_matrix.size == 0:
        return np.ones((simulation_count, horizon_periods + 1), dtype=float)
    n_obs = returns_matrix.shape[0]
    block = max(1, int(block_size))
    steps = []
    for _ in range(simulation_count):
        seq = []
        while len(seq) < horizon_periods:
            start = int(rng.integers(0, max(1, n_obs - block)))
            seq.extend(list(range(start, min(n_obs, start + block))))
        steps.append(seq[:horizon_periods])
    idx = np.array(steps, dtype=int)
    sampled = returns_matrix[idx, :]
    port_rets = np.tensordot(sampled, weights, axes=([2], [0]))
    paths = np.cumprod(1.0 + port_rets, axis=1)
    return np.concatenate([np.ones((simulation_count, 1), dtype=float), paths], axis=1)


def _ensure_pos_semidefinite(cov: np.ndarray) -> np.ndarray:
    cov = np.array(cov, dtype=float)
    if cov.size == 0:
        return cov
    if cov.ndim == 0:
        cov = np.array([[float(cov)]], dtype=float)
    cov = (cov + cov.T) / 2.0
    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvals = np.maximum(eigvals, 1e-12)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T


def deterministic_scenario_impacts(
    *,
    weights_by_symbol: dict[str, float],
    symbol_to_asset_class: dict[str, str],
) -> list[dict[str, Any]]:
    """Simple deterministic scenario pack impacts by asset-class shock maps."""
    scenarios = {
        "recession": {"equities": -0.15, "high_quality_bonds": 0.04, "credit": -0.08, "gold": 0.03, "cash_liquidity": 0.0},
        "soft_landing": {"equities": 0.08, "high_quality_bonds": -0.02, "credit": 0.03, "gold": 0.0, "cash_liquidity": 0.0},
        "inflation_reacceleration": {"equities": -0.06, "high_quality_bonds": -0.09, "credit": -0.04, "real_assets": 0.04, "gold": 0.05},
        "rates_up_100bps": {"equities": -0.05, "high_quality_bonds": -0.1, "credit": -0.05, "cash_liquidity": 0.01},
        "rates_cut_100bps": {"equities": 0.06, "high_quality_bonds": 0.07, "credit": 0.04, "cash_liquidity": -0.005},
        "oil_shock_up": {"equities": -0.03, "real_assets": 0.05, "credit": -0.01, "gold": 0.02},
        "usd_spike": {"equities": -0.04, "gold": -0.03, "high_quality_bonds": 0.01},
        "ai_capex_boom": {"equities": 0.1, "credit": 0.03, "high_quality_bonds": -0.02},
    }
    rows: list[dict[str, Any]] = []
    for name, shocks in scenarios.items():
        total = 0.0
        top: list[tuple[str, float]] = []
        for symbol, weight in weights_by_symbol.items():
            ac = symbol_to_asset_class.get(symbol, "equities")
            shock = float(shocks.get(ac, 0.0))
            impact = weight * shock
            total += impact
            top.append((symbol, impact))
        top_sorted = sorted(top, key=lambda item: abs(item[1]), reverse=True)[:4]
        rows.append(
            {
                "name": name,
                "impact_pct": round(total * 100.0, 2),
                "impact_display": f"{total * 100.0:+.2f}%",
                "top_holdings": [
                    {"symbol": symbol, "impact_pct": round(value * 100.0, 2), "impact_display": f"{value * 100.0:+.2f}%"}
                    for symbol, value in top_sorted
                ],
            }
        )
    rows.sort(key=lambda item: abs(item["impact_pct"]), reverse=True)
    return rows

