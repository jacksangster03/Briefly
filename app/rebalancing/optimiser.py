"""Portfolio optimisation helpers for rebalancing methods.

Supports:
- hrp: Hierarchical Risk Parity
- min_cvar: Minimum CVaR (95%)
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def optimise_weights(returns_df: pd.DataFrame, method: str = "hrp") -> dict[str, float]:
    """Return long-only weights keyed by symbol.

    Raises:
        ValueError: for invalid inputs or unsupported methods.
        ImportError: if riskfolio-lib is not installed.
        RuntimeError: when optimisation fails.
    """
    if returns_df is None or returns_df.empty:
        raise ValueError("returns_df must contain at least one row")
    if returns_df.shape[1] < 2:
        raise ValueError("returns_df must contain at least two assets")

    clean = returns_df.dropna(axis=1, how="all").dropna(axis=0, how="any")
    if clean.empty or clean.shape[1] < 2:
        raise ValueError("returns_df has insufficient clean data for optimisation")

    method_key = (method or "").strip().lower()
    if method_key not in {"hrp", "min_cvar"}:
        raise ValueError(f"Unsupported optimisation method: {method}")

    try:
        import riskfolio as rp
    except Exception as exc:  # pragma: no cover
        raise ImportError("riskfolio-lib is required for optimiser methods") from exc

    if method_key == "hrp":
        model = rp.HCPortfolio(returns=clean)
        try:
            weights_df = model.optimization(
                model="HRP",
                codependence="pearson",
                rm="MV",
                rf=0,
                linkage="ward",
                max_k=10,
                leaf_order=True,
            )
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"HRP optimisation failed: {exc}") from exc
    else:
        model = rp.Portfolio(returns=clean)
        model.assets_stats(method_mu="hist", method_cov="hist")
        try:
            weights_df = model.optimization(
                model="Classic",
                rm="CVaR",
                obj="MinRisk",
                rf=0,
                l=0,
                hist=True,
            )
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"min_cvar optimisation failed: {exc}") from exc

    weights = _normalise_weights(weights_df, clean.columns.tolist())
    return weights


def _normalise_weights(weights_obj: Any, columns: list[str]) -> dict[str, float]:
    if weights_obj is None:
        raise RuntimeError("Optimiser returned empty weights")

    if isinstance(weights_obj, pd.Series):
        series = weights_obj
    elif isinstance(weights_obj, pd.DataFrame):
        if weights_obj.empty:
            raise RuntimeError("Optimiser returned empty weight frame")
        first_col = weights_obj.columns[0]
        series = weights_obj[first_col]
    elif isinstance(weights_obj, dict):
        series = pd.Series(weights_obj)
    else:
        raise RuntimeError("Unsupported optimiser output format")

    series = series.reindex(columns).fillna(0.0).clip(lower=0.0)
    total = float(series.sum())
    if total <= 0:
        n = len(columns)
        if n == 0:
            raise RuntimeError("No assets to normalise")
        equal = 1.0 / n
        return {c: equal for c in columns}

    normalised = series / total
    return {c: float(normalised.get(c, 0.0)) for c in columns}
