"""Brinson-Hood-Beebower attribution service for Phase 5.5."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import AttributionSnapshot
from app.db.session import get_session


_ASSET_CLASS_LABELS: dict[str, str] = {
    "equities": "Equities",
    "high_quality_bonds": "High-Quality Bonds",
    "credit": "Credit",
    "alternatives": "Alternatives",
    "real_assets": "Real Assets",
    "gold": "Gold",
    "cash_liquidity": "Cash / Liquidity",
}


def _fmt_pct(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}%"


def _fmt_signed_pct(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def _label_from_ac(ac: str) -> str:
    if ac in _ASSET_CLASS_LABELS:
        return _ASSET_CLASS_LABELS[ac]
    return ac.replace("_", " ").title()


def _unavailable_block(error: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "method": "brinson_cma",
        "method_label": "Brinson Attribution (CMA-based)",
        "method_note": (
            "Allocation effect uses CMA expected returns as the return proxy. "
            "Selection and interaction effects require holding-level historical returns "
            "and will be enabled in a future phase."
        ),
        "computed_at": "",
        "portfolio_return_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "active_return_pct": 0.0,
        "portfolio_return_display": "0.00%",
        "benchmark_return_display": "0.00%",
        "active_return_display": "+0.00%",
        "allocation_effect_pct": 0.0,
        "selection_effect_pct": 0.0,
        "interaction_effect_pct": 0.0,
        "allocation_effect_display": "+0.00%",
        "selection_effect_display": "+0.00%",
        "interaction_effect_display": "+0.00%",
        "waterfall": [],
        "rows": [],
        "summary": "",
        "top_contributor": None,
        "top_detractor": None,
        "history": [],
    }


def _brinson_decomposition(
    segments: list[dict[str, Any]],
    r_benchmark_total: float,
) -> list[dict[str, Any]]:
    """
    For each segment compute allocation, selection, and interaction effects
    using the Brinson-Hood-Beebower model.

    In v1, R_p[i] == R_b[i] (same CMA for both portfolio and benchmark within each class),
    so selection and interaction effects are zero for all segments.
    """
    rows: list[dict[str, Any]] = []
    for seg in segments:
        ac = seg["asset_class"]
        w_p = seg["w_portfolio"]
        w_b = seg["w_benchmark"]
        r_i = seg["r_expected"]

        alloc_effect = (w_p - w_b) * (r_i - r_benchmark_total)
        sel_effect = w_b * (r_i - r_i)
        inter_effect = (w_p - w_b) * (r_i - r_i)
        total_effect = alloc_effect + sel_effect + inter_effect

        overweight = w_p > w_b
        above_benchmark = r_i > r_benchmark_total

        if overweight and above_benchmark:
            contribution_label = "Overweight in a high-returning asset class: positive allocation effect."
        elif overweight and not above_benchmark:
            contribution_label = "Overweight in a low-returning asset class: negative allocation effect."
        elif not overweight and above_benchmark:
            contribution_label = "Underweight in a high-returning asset class: negative allocation effect."
        else:
            contribution_label = "Underweight in a low-returning asset class: neutral."

        w_diff = w_p - w_b
        rows.append({
            "asset_class": ac,
            "label": seg.get("label") or _label_from_ac(ac),
            "role": seg.get("role") or "Not provided",
            "w_portfolio_pct": round(w_p * 100.0, 4),
            "w_benchmark_pct": round(w_b * 100.0, 4),
            "weight_diff_pct": round(w_diff * 100.0, 4),
            "r_expected_pct": round(r_i, 4),
            "r_benchmark_total_pct": round(r_benchmark_total, 4),
            "allocation_effect_pct": round(alloc_effect, 6),
            "selection_effect_pct": round(sel_effect, 6),
            "interaction_effect_pct": round(inter_effect, 6),
            "total_effect_pct": round(total_effect, 6),
            "overweight": overweight,
            "contribution_label": contribution_label,
            "w_portfolio_display": _fmt_pct(w_p * 100.0),
            "w_benchmark_display": _fmt_pct(w_b * 100.0),
            "weight_diff_display": _fmt_signed_pct(w_diff * 100.0),
            "r_expected_display": _fmt_pct(r_i),
            "allocation_effect_display": _fmt_signed_pct(alloc_effect),
            "total_effect_display": _fmt_signed_pct(total_effect),
        })
    return rows


def _build_attribution_rows(
    actual_allocation: list[dict[str, Any]],
    target_allocation: list[dict[str, Any]],
    cma_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Join actual weights, target weights, and CMA expected returns.
    Normalise weights to sum to 1.0 before computing effects.
    Returns (segments, excluded_classes).
    """
    cma_by_ac: dict[str, dict] = {
        str(e.get("asset_class") or "").strip().lower(): e for e in cma_entries
    }
    actual_by_ac: dict[str, float] = {}
    for row in actual_allocation:
        ac = str(row.get("asset_class") or "").strip().lower()
        pct = row.get("actual_pct")
        if ac and pct is not None:
            try:
                actual_by_ac[ac] = float(pct)
            except (TypeError, ValueError):
                pass

    target_by_ac: dict[str, float] = {}
    for row in target_allocation:
        ac = str(row.get("asset_class") or "").strip().lower()
        pct = row.get("target_weight_pct") or row.get("target_pct")
        if ac and pct is not None:
            try:
                target_by_ac[ac] = float(pct)
            except (TypeError, ValueError):
                pass

    all_classes = set(actual_by_ac.keys()) | set(target_by_ac.keys())

    included: list[dict[str, Any]] = []
    excluded: list[str] = []

    for ac in sorted(all_classes):
        has_actual = ac in actual_by_ac
        has_cma = ac in cma_by_ac
        if not (has_actual and has_cma):
            excluded.append(ac)
            continue
        included.append({
            "asset_class": ac,
            "w_portfolio_raw": actual_by_ac.get(ac, 0.0),
            "w_benchmark_raw": target_by_ac.get(ac, 0.0),
            "r_expected": float(cma_by_ac[ac].get("expected_return_pct") or 0.0),
        })

    total_portfolio = sum(item["w_portfolio_raw"] for item in included)
    total_benchmark = sum(item["w_benchmark_raw"] for item in included)

    segments: list[dict[str, Any]] = []
    for item in included:
        ac = item["asset_class"]
        w_p = (item["w_portfolio_raw"] / total_portfolio) if total_portfolio > 0 else 0.0
        w_b = (item["w_benchmark_raw"] / total_benchmark) if total_benchmark > 0 else 0.0

        role_row = next(
            (r for r in target_allocation if str(r.get("asset_class") or "").strip().lower() == ac),
            {},
        )
        segments.append({
            "asset_class": ac,
            "label": role_row.get("label") or _label_from_ac(ac),
            "role": role_row.get("role") or "Not provided",
            "w_portfolio": w_p,
            "w_benchmark": w_b,
            "r_expected": item["r_expected"],
        })

    return segments, excluded


def compute_attribution(
    profile_name: str,
    actual_allocation: list[dict[str, Any]],
    target_allocation: list[dict[str, Any]],
    cma_analytics: dict[str, Any],
    policy: dict[str, Any] | None,
    persist: bool = False,
) -> dict[str, Any]:
    """Main entry point. Returns the attribution block."""
    if not cma_analytics.get("available"):
        return _unavailable_block(
            "CMA assumptions are not configured. Add expected returns in the CMA tab to enable attribution."
        )

    if not actual_allocation:
        return _unavailable_block(
            "No actual allocation data available. Add holdings with weights to enable attribution."
        )

    cma_entries = cma_analytics.get("entries", [])
    if not cma_entries:
        return _unavailable_block(
            "CMA entries list is empty. Configure CMA assumptions to enable attribution."
        )

    segments, excluded = _build_attribution_rows(actual_allocation, target_allocation, cma_entries)

    if not segments:
        return _unavailable_block(
            "No asset classes could be matched between actual allocation and CMA entries. "
            "Ensure asset class names match between the Allocation and CMA tabs."
        )

    r_benchmark_total = sum(seg["w_benchmark"] * seg["r_expected"] for seg in segments)
    r_portfolio_total = sum(seg["w_portfolio"] * seg["r_expected"] for seg in segments)
    active_return = r_portfolio_total - r_benchmark_total

    rows = _brinson_decomposition(segments, r_benchmark_total)
    rows.sort(key=lambda r: abs(r["allocation_effect_pct"]), reverse=True)

    total_allocation_effect = sum(r["allocation_effect_pct"] for r in rows)
    total_selection_effect = sum(r["selection_effect_pct"] for r in rows)
    total_interaction_effect = sum(r["interaction_effect_pct"] for r in rows)

    waterfall = _build_waterfall(r_benchmark_total, total_allocation_effect, total_selection_effect, total_interaction_effect, r_portfolio_total)

    positive_rows = [r for r in rows if r["allocation_effect_pct"] > 0]
    negative_rows = [r for r in rows if r["allocation_effect_pct"] < 0]
    top_contributor = positive_rows[0]["asset_class"] if positive_rows else None
    top_detractor = negative_rows[0]["asset_class"] if negative_rows else None

    summary = _build_summary(rows, active_return, total_allocation_effect)

    exclusion_error: str | None = None
    if len(excluded) > 2:
        exclusion_error = (
            f"{len(excluded)} asset class(es) were excluded from attribution due to missing CMA or actual allocation data: "
            + ", ".join(excluded[:5])
            + ("..." if len(excluded) > 5 else ".")
        )

    computed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history = load_attribution_history(profile_name, limit=5)

    if persist:
        _persist_attribution(
            profile_name=profile_name,
            computed_at=computed_at,
            benchmark_return_pct=r_benchmark_total,
            portfolio_return_pct=r_portfolio_total,
            active_return_pct=active_return,
            allocation_effect_pct=total_allocation_effect,
            selection_effect_pct=total_selection_effect,
            interaction_effect_pct=total_interaction_effect,
            rows=rows,
        )
        history = load_attribution_history(profile_name, limit=5)

    return {
        "available": True,
        "error": exclusion_error,
        "method": "brinson_cma",
        "method_label": "Brinson Attribution (CMA-based)",
        "method_note": (
            "Allocation effect uses CMA expected returns as the return proxy. "
            "Selection and interaction effects require holding-level historical returns "
            "and will be enabled in a future phase."
        ),
        "computed_at": computed_at,
        "portfolio_return_pct": round(r_portfolio_total, 4),
        "benchmark_return_pct": round(r_benchmark_total, 4),
        "active_return_pct": round(active_return, 4),
        "portfolio_return_display": _fmt_pct(r_portfolio_total),
        "benchmark_return_display": _fmt_pct(r_benchmark_total),
        "active_return_display": _fmt_signed_pct(active_return),
        "allocation_effect_pct": round(total_allocation_effect, 6),
        "selection_effect_pct": round(total_selection_effect, 6),
        "interaction_effect_pct": round(total_interaction_effect, 6),
        "allocation_effect_display": _fmt_signed_pct(total_allocation_effect),
        "selection_effect_display": _fmt_signed_pct(total_selection_effect),
        "interaction_effect_display": _fmt_signed_pct(total_interaction_effect),
        "waterfall": waterfall,
        "rows": rows,
        "summary": summary,
        "top_contributor": top_contributor,
        "top_detractor": top_detractor,
        "history": history,
    }


def load_attribution_history(profile_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Load recent AttributionSnapshot records for a profile."""
    with get_session() as session:
        rows = (
            session.query(AttributionSnapshot)
            .filter(
                AttributionSnapshot.profile_name == profile_name,
                AttributionSnapshot.active.is_(True),
            )
            .order_by(AttributionSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        result = []
        for row in rows:
            result.append({
                "computed_at": row.computed_at.strftime("%Y-%m-%d %H:%M") if row.computed_at else "",
                "active_return_pct": round(row.active_return_pct or 0.0, 4),
                "allocation_effect_pct": round(row.allocation_effect_pct or 0.0, 6),
                "active_return_display": _fmt_signed_pct(row.active_return_pct or 0.0),
            })
        return result


def _build_waterfall(
    benchmark_return: float,
    allocation_effect: float,
    selection_effect: float,
    interaction_effect: float,
    portfolio_return: float,
) -> list[dict[str, Any]]:
    cumulative = benchmark_return
    waterfall = [
        {
            "label": "Benchmark Return",
            "value_pct": round(benchmark_return, 4),
            "cumulative_pct": round(benchmark_return, 4),
            "type": "base",
        }
    ]
    for label, value in [
        ("Allocation Effect", allocation_effect),
        ("Selection Effect", selection_effect),
        ("Interaction Effect", interaction_effect),
    ]:
        cumulative += value
        if value > 1e-9:
            effect_type = "positive"
        elif value < -1e-9:
            effect_type = "negative"
        else:
            effect_type = "neutral"
        waterfall.append({
            "label": label,
            "value_pct": round(value, 6),
            "cumulative_pct": round(cumulative, 4),
            "type": effect_type,
        })
    waterfall.append({
        "label": "Portfolio Return",
        "value_pct": round(portfolio_return, 4),
        "cumulative_pct": round(portfolio_return, 4),
        "type": "total",
    })
    return waterfall


def _build_summary(
    rows: list[dict[str, Any]],
    active_return: float,
    total_allocation_effect: float,
) -> str:
    if not rows:
        return "No attribution data available."

    direction = "positive" if active_return >= 0 else "negative"
    active_display = _fmt_signed_pct(active_return)

    top_rows = rows[:2]
    top_labels = []
    for row in top_rows:
        overweight_text = "overweight" if row["overweight"] else "underweight"
        top_labels.append(
            f"{row['label']} ({overweight_text}, allocation effect {_fmt_signed_pct(row['allocation_effect_pct'])})"
        )

    if top_labels:
        top_text = "; ".join(top_labels)
        return (
            f"Active return is {active_display}, driven primarily by allocation decisions. "
            f"The largest contributors to the allocation effect are: {top_text}. "
            f"Total allocation effect is {_fmt_signed_pct(total_allocation_effect)}."
        )
    return (
        f"Active return is {active_display}. "
        f"Total allocation effect is {_fmt_signed_pct(total_allocation_effect)}."
    )


def _persist_attribution(
    profile_name: str,
    computed_at: str,
    benchmark_return_pct: float,
    portfolio_return_pct: float,
    active_return_pct: float,
    allocation_effect_pct: float,
    selection_effect_pct: float,
    interaction_effect_pct: float,
    rows: list[dict[str, Any]],
) -> None:
    now = datetime.now(timezone.utc)
    try:
        dt = datetime.strptime(computed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        dt = now

    with get_session() as session:
        snap = AttributionSnapshot(
            profile_name=profile_name,
            computed_at=dt,
            method="brinson_cma",
            benchmark_return_pct=round(benchmark_return_pct, 6),
            portfolio_return_pct=round(portfolio_return_pct, 6),
            active_return_pct=round(active_return_pct, 6),
            allocation_effect_pct=round(allocation_effect_pct, 6),
            selection_effect_pct=round(selection_effect_pct, 6),
            interaction_effect_pct=round(interaction_effect_pct, 6),
            sector_attribution_json="[]",
            asset_class_attribution_json=json.dumps(rows),
            active=True,
            created_at=now,
        )
        session.add(snap)
        session.commit()
