"""GARCH(1,1) volatility forecasting and 1-day Value-at-Risk estimation.

Uses the `arch` library. Falls back gracefully when fewer than 60 observations
are available or if the model fails to converge.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("garch_var")

_MIN_OBSERVATIONS = 60
_CONFIDENCE_LEVEL = 0.95  # 1-day 95% VaR
_ARCH_IMPORT_ERROR_LOGGED = False


def _pct_returns(prices: list[float]) -> list[float]:
    if len(prices) < 2:
        return []
    return [((prices[i] / prices[i - 1]) - 1.0) * 100.0 for i in range(1, len(prices))]


def estimate_var(prices: list[float], confidence: float = _CONFIDENCE_LEVEL) -> dict[str, float | None]:
    """Fit GARCH(1,1) and return forecast volatility + VaR.

    Returns:
        {
          "garch_vol_pct": annualised conditional volatility %,
          "var_1d_pct":    1-day VaR at `confidence` (positive = loss),
          "observations":  int,
        }
    """
    raw_returns = _pct_returns(prices)
    returns = [r for r in raw_returns if np.isfinite(r)]
    n = len(returns)
    result: dict[str, Any] = {"garch_vol_pct": None, "var_1d_pct": None, "observations": n}

    if n < _MIN_OBSERVATIONS:
        logger.debug("Too few observations (%d) for GARCH — need %d", n, _MIN_OBSERVATIONS)
        return result

    global _ARCH_IMPORT_ERROR_LOGGED
    try:
        from arch import arch_model  # type: ignore

        r = np.array(returns, dtype=float)
        model = arch_model(r, vol="Garch", p=1, q=1, dist="normal", rescale=True)
        fit = model.fit(disp="off", show_warning=False)
        # 1-step ahead forecast
        forecast = fit.forecast(horizon=1, reindex=False)
        variance_1d = float(forecast.variance.iloc[-1, 0])
        cond_vol_pct = float(np.sqrt(variance_1d))  # daily, pct

        from scipy.stats import norm  # type: ignore
        z = float(norm.ppf(1.0 - confidence))
        var_pct = abs(z * cond_vol_pct)
        annualised = cond_vol_pct * float(np.sqrt(252))

        result["garch_vol_pct"] = round(annualised, 2)
        result["var_1d_pct"] = round(var_pct, 2)
        logger.debug("GARCH fit: ann_vol=%.2f%% VaR(%.0f%%)=%.2f%%", annualised, confidence * 100, var_pct)
    except ModuleNotFoundError as exc:
        # Avoid noisy repeated warnings on each symbol when optional dependency
        # is intentionally not installed.
        if not _ARCH_IMPORT_ERROR_LOGGED:
            logger.info("GARCH disabled: optional dependency missing (%s)", exc)
            _ARCH_IMPORT_ERROR_LOGGED = True
    except Exception as exc:
        logger.warning("GARCH estimation failed: %s", exc)

    return result


def portfolio_var(
    holdings: list[dict[str, Any]],
    price_histories: dict[str, list[float]],
    confidence: float = _CONFIDENCE_LEVEL,
) -> dict[str, Any]:
    """Compute weighted-average GARCH VaR across portfolio holdings.

    Args:
        holdings: list of {"symbol": str, "weight": float (0-1)}
        price_histories: {symbol: [close prices]}

    Returns:
        {
          "portfolio_var_pct": weighted 1-day VaR,
          "per_holding": {symbol: {"garch_vol_pct", "var_1d_pct", "weight"}},
        }
    """
    per_holding: dict[str, Any] = {}
    weighted_var = 0.0
    total_weight = 0.0

    for holding in holdings:
        sym = str(holding.get("symbol") or "")
        weight = float(holding.get("weight") or 0.0)
        prices = price_histories.get(sym) or []
        if not prices or weight <= 0:
            continue
        var_result = estimate_var(prices, confidence)
        var_pct = var_result.get("var_1d_pct")
        if var_pct is not None:
            weighted_var += weight * float(var_pct)
            total_weight += weight
        per_holding[sym] = {**var_result, "weight": round(weight * 100, 2)}

    portfolio_var_pct = round(weighted_var / total_weight, 2) if total_weight > 0 else None
    return {"portfolio_var_pct": portfolio_var_pct, "per_holding": per_holding}
