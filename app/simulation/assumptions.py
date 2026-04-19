"""Assumption builders for simulation methods."""

from __future__ import annotations

from typing import Any

import numpy as np

from app.cma.service import load_cma_correlations, load_cma_entries
from app.simulation.models import FREQUENCY_TO_ANNUAL_PERIODS


def estimate_historical_parameters(
    returns_matrix: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate mean vector and covariance matrix from return matrix."""
    if returns_matrix.size == 0:
        return np.array([]), np.array([[]])
    mu = np.mean(returns_matrix, axis=0)
    cov = np.cov(returns_matrix, rowvar=False)
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])
    return mu, cov


def cma_parameters_for_symbols(
    *,
    profile_name: str,
    symbols: list[str],
    frequency: str,
    symbol_to_asset_class: dict[str, str],
) -> tuple[np.ndarray, np.ndarray]:
    """Build mu/cov from CMA entries mapped by inferred asset classes."""
    cma_entries = load_cma_entries(profile_name)
    if not cma_entries:
        return np.array([]), np.array([[]])
    by_ac = {str(row.get("asset_class") or "").strip().lower(): row for row in cma_entries}
    correlations = load_cma_correlations(profile_name)
    corr_lookup: dict[tuple[str, str], float] = {}
    for row in correlations:
        a = str(row.get("asset_class_a") or "").strip().lower()
        b = str(row.get("asset_class_b") or "").strip().lower()
        corr = float(row.get("correlation") or 0.0)
        corr_lookup[(a, b)] = corr
        corr_lookup[(b, a)] = corr

    annual_periods = FREQUENCY_TO_ANNUAL_PERIODS.get(frequency, 12)
    mu = []
    sigma = []
    ac_list = []
    for symbol in symbols:
        ac = symbol_to_asset_class.get(symbol, "equities")
        ac_list.append(ac)
        entry = by_ac.get(ac)
        if not entry:
            mu.append(0.0)
            sigma.append(0.0)
            continue
        mu.append(float(entry.get("expected_return_pct") or 0.0) / 100.0 / annual_periods)
        sigma.append(float(entry.get("expected_volatility_pct") or 0.0) / 100.0 / np.sqrt(annual_periods))

    n = len(symbols)
    cov = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            if i == j:
                cov[i, j] = sigma[i] ** 2
                continue
            corr = corr_lookup.get((ac_list[i], ac_list[j]), 0.0)
            cov[i, j] = corr * sigma[i] * sigma[j]
    return np.array(mu, dtype=float), cov


def apply_macro_overrides(
    *,
    mu: np.ndarray,
    cov: np.ndarray,
    macro_overrides: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    """Apply structured macro overrides to mean and covariance assumptions."""
    if mu.size == 0:
        return mu, cov
    growth = float(macro_overrides.get("growth_shock", 0.0) or 0.0)
    inflation = float(macro_overrides.get("inflation_shock", 0.0) or 0.0)
    rates = float(macro_overrides.get("rates_shock", 0.0) or 0.0)
    volatility_regime = float(macro_overrides.get("volatility_regime", 0.0) or 0.0)
    correlation_stress = float(macro_overrides.get("correlation_stress", 0.0) or 0.0)

    drift_shift = (growth * 0.0015) - (inflation * 0.0008) - (rates * 0.0012)
    adjusted_mu = mu + drift_shift

    vol_multiplier = max(0.2, 1.0 + (volatility_regime * 0.12))
    adjusted_cov = cov * (vol_multiplier ** 2)

    if correlation_stress != 0:
        corr_shift = max(-0.25, min(0.25, correlation_stress * 0.05))
        std = np.sqrt(np.maximum(np.diag(adjusted_cov), 1e-12))
        corr = adjusted_cov / np.outer(std, std)
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        corr = np.clip(corr + corr_shift, -0.95, 0.95)
        np.fill_diagonal(corr, 1.0)
        adjusted_cov = corr * np.outer(std, std)

    return adjusted_mu, adjusted_cov
