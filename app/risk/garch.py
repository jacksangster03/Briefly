"""GARCH(1,1) volatility modelling for portfolio return series."""

from __future__ import annotations

import math
from typing import Any

_MIN_OBS = 60
_TRADING_DAYS = 252


def compute_garch_metrics(daily_returns: list[float]) -> dict[str, Any]:
    """Fit a GARCH(1,1) model and return conditional volatility metrics."""
    if len(daily_returns) < _MIN_OBS:
        return {"available": False, "reason": f"Need >= {_MIN_OBS} observations, got {len(daily_returns)}."}

    try:
        import numpy as np
        from arch import arch_model

        returns_pct = np.asarray(daily_returns, dtype=float) * 100.0
        model = arch_model(returns_pct, vol="Garch", p=1, q=1, dist="normal", rescale=False)
        fitted = model.fit(disp="off", show_warning=False)

        params = fitted.params
        omega = float(params.get("omega", 0.0))
        alpha = float(params.get("alpha[1]", 0.0))
        beta = float(params.get("beta[1]", 0.0))
        persistence = alpha + beta

        cond_vol_daily_pct = float(fitted.conditional_volatility[-1])
        conditional_vol_pct = cond_vol_daily_pct * math.sqrt(_TRADING_DAYS)

        forecast = fitted.forecast(horizon=30, reindex=False)
        mean_var_30d = float(forecast.variance.values[-1].mean())
        forecast_30d_vol_pct = math.sqrt(mean_var_30d) * math.sqrt(_TRADING_DAYS)

        if 0.0 <= persistence < 1.0:
            long_run_var = omega / (1.0 - persistence)
            long_run_vol_pct = math.sqrt(long_run_var) * math.sqrt(_TRADING_DAYS)
        else:
            long_run_vol_pct = conditional_vol_pct

        if conditional_vol_pct < long_run_vol_pct * 0.85:
            regime = "low"
        elif conditional_vol_pct > long_run_vol_pct * 1.15:
            regime = "high"
        else:
            regime = "medium"

        return {
            "available": True,
            "conditional_vol_pct": round(conditional_vol_pct, 4),
            "forecast_30d_vol_pct": round(forecast_30d_vol_pct, 4),
            "long_run_vol_pct": round(long_run_vol_pct, 4),
            "regime": regime,
            "omega": round(omega, 8),
            "alpha": round(alpha, 6),
            "beta": round(beta, 6),
            "persistence": round(persistence, 6),
            "display": {
                "conditional_vol": f"{conditional_vol_pct:.1f}%",
                "forecast_30d_vol": f"{forecast_30d_vol_pct:.1f}%",
                "long_run_vol": f"{long_run_vol_pct:.1f}%",
                "regime": regime,
                "persistence": f"{persistence:.3f}",
            },
        }
    except ImportError:
        return {"available": False, "reason": "arch library not installed. Run: pip install arch"}
    except Exception as exc:
        return {"available": False, "reason": f"GARCH fitting failed: {exc}"}
