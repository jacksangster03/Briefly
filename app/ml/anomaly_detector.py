"""IsolationForest-based anomaly detection on daily cross-asset impulse vectors.

Compares today's impulse vector (rates, commodities, VIX delta) against historical
daily snapshots stored in the market_snapshots table. Returns an anomaly score and
a flag indicating whether this day should trigger an additional Telegram alert.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("anomaly_detector")

# Contamination: expect ~5% of days to be anomalous
_CONTAMINATION = 0.05
_MIN_HISTORY_DAYS = 20
_ALERT_THRESHOLD = -0.4   # IsolationForest score; more negative = more anomalous


def _impulse_vector(impulses: list[dict[str, Any]]) -> list[float]:
    """Convert an impulse strip (list of {name, impulse, unit}) into a fixed-length feature vector."""
    FEATURE_KEYS = [
        ("yield", "bps"),
        ("curve", "bps"),
        ("wti", "pct"),
        ("gold", "pct"),
        ("vix", "pct"),
    ]
    vec: list[float] = []
    for key, _ in FEATURE_KEYS:
        match = next((row for row in impulses if key in str(row.get("name") or "").lower()), None)
        vec.append(float(match.get("impulse") or 0.0) if match else 0.0)
    return vec


def detect_regime_anomaly(
    today_impulses: list[dict[str, Any]],
    historical_impulse_matrix: list[list[float]],
) -> dict[str, Any]:
    """Fit IsolationForest on history and score today's impulse vector.

    Args:
        today_impulses: list of impulse dicts from _cross_asset_impulse_spec
        historical_impulse_matrix: list of daily feature vectors (from DB or cache)

    Returns:
        {
          "anomaly_score": float (lower = more anomalous, range roughly -1 to 0),
          "is_anomaly": bool,
          "trigger_alert": bool,
          "feature_vector": list[float],
        }
    """
    today_vec = _impulse_vector(today_impulses)
    result: dict[str, Any] = {
        "anomaly_score": 0.0,
        "is_anomaly": False,
        "trigger_alert": False,
        "feature_vector": today_vec,
    }

    if len(historical_impulse_matrix) < _MIN_HISTORY_DAYS:
        logger.debug("Insufficient history (%d days) for anomaly detection", len(historical_impulse_matrix))
        return result

    try:
        from sklearn.ensemble import IsolationForest  # type: ignore

        X = np.array(historical_impulse_matrix, dtype=float)
        today = np.array([today_vec], dtype=float)

        clf = IsolationForest(contamination=_CONTAMINATION, random_state=42, n_jobs=1)
        clf.fit(X)
        score = float(clf.score_samples(today)[0])
        is_anomaly = bool(clf.predict(today)[0] == -1)
        trigger = score <= _ALERT_THRESHOLD

        result["anomaly_score"] = round(score, 4)
        result["is_anomaly"] = is_anomaly
        result["trigger_alert"] = trigger
        logger.info("Anomaly score: %.3f  is_anomaly=%s  trigger=%s", score, is_anomaly, trigger)
    except Exception as exc:
        logger.warning("Anomaly detection failed: %s", exc)

    return result


def build_impulse_vector_from_spec(cross_asset_spec: dict[str, Any]) -> list[float]:
    """Convenience: extract feature vector directly from a cross_asset_impulse_strip spec dict."""
    impulses = list(cross_asset_spec.get("series") or [])
    return _impulse_vector(impulses)
