"""Tests for GARCH volatility metrics."""

from __future__ import annotations

import math


class TestGarchMetrics:
    def _sample_returns(self, n: int = 300) -> list[float]:
        returns = []
        for i in range(n):
            r = 0.001 * math.sin(i * 0.1) + 0.002 * ((-1) ** i) * 0.5
            returns.append(r)
        return returns

    def test_returns_expected_keys(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics(self._sample_returns())
        assert result["available"] is True
        assert "conditional_vol_pct" in result
        assert "forecast_30d_vol_pct" in result
        assert "regime" in result
        assert "omega" in result
        assert "alpha" in result
        assert "beta" in result

    def test_conditional_vol_is_positive(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics(self._sample_returns())
        assert result["conditional_vol_pct"] > 0

    def test_persistence_between_zero_and_one(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics(self._sample_returns())
        persistence = result["alpha"] + result["beta"]
        assert 0.0 <= persistence < 1.0

    def test_regime_is_valid_label(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics(self._sample_returns())
        assert result["regime"] in ("low", "medium", "high")

    def test_too_few_observations_returns_unavailable(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics([0.001, -0.002, 0.001])
        assert result["available"] is False

    def test_display_strings_present(self):
        from app.risk.garch import compute_garch_metrics

        result = compute_garch_metrics(self._sample_returns())
        assert result["display"]["conditional_vol"] != ""
        assert result["display"]["forecast_30d_vol"] != ""
