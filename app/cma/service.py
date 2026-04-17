"""Capital Market Assumptions service for Phase 5.3."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.db.models import CMACorrelation, CMAEntry
from app.db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_cma_entries(profile_name: str) -> list[dict[str, Any]]:
    """Load active CMA entries for a profile."""
    with get_session() as session:
        rows = (
            session.query(CMAEntry)
            .filter(
                CMAEntry.profile_name == profile_name,
                CMAEntry.active.is_(True),
            )
            .order_by(CMAEntry.asset_class.asc())
            .all()
        )
        return [
            {
                "asset_class": row.asset_class,
                "expected_return_pct": float(row.expected_return_pct),
                "expected_volatility_pct": float(row.expected_volatility_pct),
                "notes": row.notes or "",
            }
            for row in rows
        ]


def save_cma_entries(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace all active CMA entries for a profile."""
    now = _utcnow()
    with get_session() as session:
        session.query(CMAEntry).filter(
            CMAEntry.profile_name == profile_name,
            CMAEntry.active.is_(True),
        ).update({"active": False, "updated_at": now})

        saved: list[dict[str, Any]] = []
        for row in rows:
            asset_class = str(row.get("asset_class") or "").strip().lower()
            if not asset_class:
                continue
            try:
                ret_pct = float(row.get("expected_return_pct") or 0.0)
                vol_pct = float(row.get("expected_volatility_pct") or 0.0)
            except (TypeError, ValueError):
                continue
            entry = CMAEntry(
                profile_name=profile_name,
                asset_class=asset_class,
                expected_return_pct=ret_pct,
                expected_volatility_pct=vol_pct,
                notes=str(row.get("notes") or "").strip() or None,
                active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            saved.append({
                "asset_class": asset_class,
                "expected_return_pct": ret_pct,
                "expected_volatility_pct": vol_pct,
                "notes": str(row.get("notes") or "").strip(),
            })
    return saved


def load_cma_correlations(profile_name: str) -> list[dict[str, Any]]:
    """Load active correlation pairs for a profile."""
    with get_session() as session:
        rows = (
            session.query(CMACorrelation)
            .filter(
                CMACorrelation.profile_name == profile_name,
                CMACorrelation.active.is_(True),
            )
            .all()
        )
        return [
            {
                "asset_class_a": row.asset_class_a,
                "asset_class_b": row.asset_class_b,
                "correlation": float(row.correlation),
            }
            for row in rows
        ]


def save_cma_correlations(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace all active correlations for a profile."""
    now = _utcnow()
    with get_session() as session:
        session.query(CMACorrelation).filter(
            CMACorrelation.profile_name == profile_name,
            CMACorrelation.active.is_(True),
        ).update({"active": False, "updated_at": now})

        saved: list[dict[str, Any]] = []
        for row in rows:
            ac_a = str(row.get("asset_class_a") or "").strip().lower()
            ac_b = str(row.get("asset_class_b") or "").strip().lower()
            if not ac_a or not ac_b:
                continue
            try:
                corr = float(row.get("correlation") or 0.0)
            except (TypeError, ValueError):
                corr = 0.0
            corr = max(-1.0, min(1.0, corr))
            entry = CMACorrelation(
                profile_name=profile_name,
                asset_class_a=ac_a,
                asset_class_b=ac_b,
                correlation=corr,
                active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            saved.append({"asset_class_a": ac_a, "asset_class_b": ac_b, "correlation": corr})
    return saved


def build_correlation_matrix(
    asset_classes: list[str],
    correlations: list[dict[str, Any]],
) -> list[list[float]]:
    """Build NxN correlation matrix from flat list of pairs. Missing pairs default to 0.0."""
    n = len(asset_classes)
    index = {ac: i for i, ac in enumerate(asset_classes)}
    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for row in correlations:
        ac_a = str(row.get("asset_class_a") or "").strip().lower()
        ac_b = str(row.get("asset_class_b") or "").strip().lower()
        if ac_a not in index or ac_b not in index:
            continue
        i, j = index[ac_a], index[ac_b]
        corr = float(row.get("correlation") or 0.0)
        matrix[i][j] = corr
        matrix[j][i] = corr
    return matrix


def compute_expected_portfolio_return(
    weights: dict[str, float],
    cma_entries: dict[str, dict],
) -> float:
    """Weighted average of expected returns. Returns annualised % or 0.0 if no overlap."""
    total = 0.0
    for ac, weight in weights.items():
        entry = cma_entries.get(ac)
        if entry is None:
            continue
        total += weight * float(entry.get("expected_return_pct") or 0.0)
    return total


def compute_expected_portfolio_volatility(
    weights: dict[str, float],
    cma_entries: dict[str, dict],
    correlation_matrix: list[list[float]],
    asset_classes: list[str],
) -> float:
    """
    Portfolio variance: w^T * Cov * w where Cov[i][j] = sigma_i * rho_ij * sigma_j.
    Returns annualised vol %.
    """
    n = len(asset_classes)
    index = {ac: i for i, ac in enumerate(asset_classes)}
    w = [0.0] * n
    sigma = [0.0] * n
    for ac, weight in weights.items():
        if ac not in index:
            continue
        i = index[ac]
        w[i] = weight / 100.0 if weight > 1.5 else weight
        entry = cma_entries.get(ac)
        if entry:
            sigma[i] = float(entry.get("expected_volatility_pct") or 0.0) / 100.0

    variance = 0.0
    for i in range(n):
        for j in range(n):
            variance += w[i] * w[j] * sigma[i] * correlation_matrix[i][j] * sigma[j]

    return math.sqrt(max(variance, 0.0)) * 100.0


def _sharpe_label(sharpe: float) -> str:
    if sharpe >= 1.5:
        return "excellent"
    if sharpe >= 1.0:
        return "good"
    if sharpe >= 0.5:
        return "moderate"
    return "poor"


def _fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}%"


def _fmt_signed_pct(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}%"


def _fmt_ratio(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _unavailable_block(error: str) -> dict[str, Any]:
    zero_metrics = {
        "expected_return_pct": 0.0,
        "expected_volatility_pct": 0.0,
        "expected_sharpe": 0.0,
        "coverage_pct": 0.0,
        "expected_return_display": "0.0%",
        "expected_volatility_display": "0.0%",
        "expected_sharpe_display": "0.00",
    }
    return {
        "available": False,
        "error": error,
        "entries": [],
        "actual": dict(zero_metrics),
        "saa": dict(zero_metrics),
        "policy": {
            "target_return_pct": None,
            "max_volatility_pct": None,
            "risk_free_rate_pct": 4.5,
        },
        "policy_gap": {
            "return_gap_pct": 0.0,
            "vol_gap_pct": 0.0,
            "return_meets_target": False,
            "vol_within_limit": True,
            "return_gap_display": "+0.0%",
            "vol_gap_display": "+0.0%",
            "summary": "No CMA assumptions configured.",
        },
        "saa_gap": {
            "return_shortfall_pct": 0.0,
            "vol_difference_pct": 0.0,
            "sharpe_difference": 0.0,
            "return_shortfall_display": "+0.0%",
            "vol_difference_display": "+0.0%",
            "sharpe_difference_display": "+0.00",
            "summary": "No CMA assumptions configured.",
        },
        "sharpe_label": "poor",
        "saa_sharpe_label": "poor",
    }


def compute_cma_analytics(
    profile_name: str,
    actual_allocation: list[dict],
    target_allocation: list[dict],
    policy: dict | None,
) -> dict[str, Any]:
    """Main entry point. Returns cma_analytics block."""
    entries_raw = load_cma_entries(profile_name)
    if not entries_raw:
        return _unavailable_block("No CMA assumptions configured")

    correlations_raw = load_cma_correlations(profile_name)

    cma_by_ac: dict[str, dict] = {row["asset_class"]: row for row in entries_raw}
    asset_classes = list(cma_by_ac.keys())
    corr_matrix = build_correlation_matrix(asset_classes, correlations_raw)

    actual_ac_set = {str(row.get("asset_class") or "") for row in actual_allocation}
    target_ac_set = {str(row.get("asset_class") or "") for row in target_allocation}

    entries_display = [
        {
            "asset_class": ac,
            "label": _label_from_ac(ac),
            "expected_return_pct": entry["expected_return_pct"],
            "expected_volatility_pct": entry["expected_volatility_pct"],
            "notes": entry.get("notes") or "",
            "has_allocation": ac in actual_ac_set,
        }
        for ac, entry in cma_by_ac.items()
    ]

    policy_data = policy or {}
    target_return_pct = _float_or_none(policy_data.get("target_return_percent"))
    max_volatility_pct = _float_or_none(policy_data.get("max_volatility_percent"))
    risk_free_rate_pct = 4.5

    actual_weights: dict[str, float] = {}
    for row in actual_allocation:
        ac = str(row.get("asset_class") or "")
        pct = _float_or_none(row.get("actual_pct"))
        if ac and pct is not None:
            actual_weights[ac] = pct / 100.0

    target_weights: dict[str, float] = {}
    for row in target_allocation:
        ac = str(row.get("asset_class") or "")
        pct = _float_or_none(row.get("target_weight_pct") or row.get("target_pct"))
        if ac and pct is not None:
            target_weights[ac] = pct / 100.0

    covered_actual = sum(w for ac, w in actual_weights.items() if ac in cma_by_ac)
    total_actual = sum(actual_weights.values())
    coverage_actual_pct = (covered_actual / total_actual * 100.0) if total_actual > 0 else 0.0

    covered_target = sum(w for ac, w in target_weights.items() if ac in cma_by_ac)
    total_target = sum(target_weights.values())
    coverage_target_pct = (covered_target / total_target * 100.0) if total_target > 0 else 0.0

    actual_ret = compute_expected_portfolio_return(actual_weights, cma_by_ac)
    actual_vol = compute_expected_portfolio_volatility(
        actual_weights,
        cma_by_ac,
        corr_matrix,
        asset_classes,
    )
    actual_sharpe = (actual_ret - risk_free_rate_pct) / actual_vol if actual_vol > 0 else 0.0

    saa_ret = compute_expected_portfolio_return(target_weights, cma_by_ac)
    saa_vol = compute_expected_portfolio_volatility(
        target_weights,
        cma_by_ac,
        corr_matrix,
        asset_classes,
    )
    saa_sharpe = (saa_ret - risk_free_rate_pct) / saa_vol if saa_vol > 0 else 0.0

    return_gap = actual_ret - (target_return_pct or 0.0) if target_return_pct is not None else 0.0
    vol_gap = actual_vol - (max_volatility_pct or 0.0) if max_volatility_pct is not None else 0.0
    return_meets_target = (target_return_pct is None) or (actual_ret >= target_return_pct)
    vol_within_limit = (max_volatility_pct is None) or (actual_vol <= max_volatility_pct)

    if target_return_pct is None and max_volatility_pct is None:
        policy_summary = "No policy targets configured."
    elif return_meets_target and vol_within_limit:
        policy_summary = "Expected return meets the policy target and volatility is within limit."
    elif not return_meets_target and not vol_within_limit:
        policy_summary = "Expected return is below the policy target and volatility exceeds the limit."
    elif not return_meets_target:
        policy_summary = "Expected return is below the policy target."
    else:
        policy_summary = "Expected volatility exceeds the policy limit."

    return_shortfall = actual_ret - saa_ret
    vol_difference = actual_vol - saa_vol
    sharpe_difference = actual_sharpe - saa_sharpe

    if abs(return_shortfall) < 0.05 and abs(vol_difference) < 0.05:
        saa_summary = "Actual portfolio expected metrics are broadly in line with the SAA."
    elif return_shortfall >= 0:
        saa_summary = f"Actual portfolio is expected to outperform the SAA by {_fmt_pct(abs(return_shortfall))} in return."
    else:
        saa_summary = f"Actual portfolio expected return trails the SAA by {_fmt_pct(abs(return_shortfall))}."

    return {
        "available": True,
        "error": None,
        "entries": entries_display,
        "actual": {
            "expected_return_pct": round(actual_ret, 2),
            "expected_volatility_pct": round(actual_vol, 2),
            "expected_sharpe": round(actual_sharpe, 2),
            "coverage_pct": round(coverage_actual_pct, 1),
            "expected_return_display": _fmt_pct(actual_ret),
            "expected_volatility_display": _fmt_pct(actual_vol),
            "expected_sharpe_display": _fmt_ratio(actual_sharpe),
        },
        "saa": {
            "expected_return_pct": round(saa_ret, 2),
            "expected_volatility_pct": round(saa_vol, 2),
            "expected_sharpe": round(saa_sharpe, 2),
            "coverage_pct": round(coverage_target_pct, 1),
            "expected_return_display": _fmt_pct(saa_ret),
            "expected_volatility_display": _fmt_pct(saa_vol),
            "expected_sharpe_display": _fmt_ratio(saa_sharpe),
        },
        "policy": {
            "target_return_pct": target_return_pct,
            "max_volatility_pct": max_volatility_pct,
            "risk_free_rate_pct": risk_free_rate_pct,
        },
        "policy_gap": {
            "return_gap_pct": round(return_gap, 2),
            "vol_gap_pct": round(vol_gap, 2),
            "return_meets_target": return_meets_target,
            "vol_within_limit": vol_within_limit,
            "return_gap_display": _fmt_signed_pct(return_gap),
            "vol_gap_display": _fmt_signed_pct(vol_gap),
            "summary": policy_summary,
        },
        "saa_gap": {
            "return_shortfall_pct": round(return_shortfall, 2),
            "vol_difference_pct": round(vol_difference, 2),
            "sharpe_difference": round(sharpe_difference, 2),
            "return_shortfall_display": _fmt_signed_pct(return_shortfall),
            "vol_difference_display": _fmt_signed_pct(vol_difference),
            "sharpe_difference_display": _fmt_signed_pct(sharpe_difference, digits=2),
            "summary": saa_summary,
        },
        "sharpe_label": _sharpe_label(actual_sharpe),
        "saa_sharpe_label": _sharpe_label(saa_sharpe),
    }


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _label_from_ac(ac: str) -> str:
    labels = {
        "equities": "Equities",
        "high_quality_bonds": "High-Quality Bonds",
        "credit": "Credit",
        "alternatives": "Alternatives",
        "real_assets": "Real Assets",
        "gold": "Gold",
        "cash_liquidity": "Cash / Liquidity",
    }
    if ac in labels:
        return labels[ac]
    return ac.replace("_", " ").title()
