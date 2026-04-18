"""Parameter sweep framework for Phase 5.7A validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.settings import Settings
from app.validation.presets import get_preset
from app.validation.runner import apply_preset_payload, validate_profile_state


SUPPORTED_DIMENSIONS = {
    "top_holding_pct",
    "cash_weight_pct",
    "equity_expected_return_pct",
}


def run_parameter_sweep(
    *,
    settings: Settings,
    profile_name: str,
    preset_name: str,
    dimension: str,
    values: list[float],
) -> dict[str, Any]:
    normalized_dimension = (dimension or "").strip().lower()
    if normalized_dimension not in SUPPORTED_DIMENSIONS:
        supported = ", ".join(sorted(SUPPORTED_DIMENSIONS))
        raise ValueError(f"Unsupported dimension '{dimension}'. Supported: {supported}")

    preset = get_preset(preset_name)
    cases: list[dict[str, Any]] = []
    for value in values:
        adjusted = _adjust_preset_for_dimension(preset, normalized_dimension, float(value))
        report = _validate_adjusted_case(
            settings=settings,
            profile_name=profile_name,
            preset_name=preset_name,
            dimension=normalized_dimension,
            value=float(value),
            adjusted_preset=adjusted,
        )
        cases.append(report)

    monotonic = _evaluate_monotonicity(normalized_dimension, cases)
    return {
        "profile": profile_name,
        "preset": preset_name,
        "dimension": normalized_dimension,
        "values": values,
        "cases": cases,
        "monotonic_check": monotonic,
        "status": "pass" if monotonic["passed"] else "fail",
    }


def _validate_adjusted_case(
    *,
    settings: Settings,
    profile_name: str,
    preset_name: str,
    dimension: str,
    value: float,
    adjusted_preset: dict[str, Any],
) -> dict[str, Any]:
    top_symbol = str((adjusted_preset.get("holdings") or [{}])[0].get("symbol") or "").upper()
    temp_name = f"{preset_name}:{dimension}:{value:.3f}"
    apply_preset_payload(
        profile_name=profile_name,
        preset_name=temp_name,
        preset=deepcopy(adjusted_preset),
        include_risk_refresh=False,
    )

    report = validate_profile_state(
        settings=settings,
        profile_name=profile_name,
        preset_name=temp_name,
        expectations={},
    )
    summary = report["summary"]
    return {
        "value": value,
        "status": report["status"],
        "largest_position_pct": float(
            ((report["state"].get("analysis", {}).get("kpis", {}).get("largest_position") or {}).get("weight_pct") or 0.0)
        ),
        "top_symbol": top_symbol,
        "top_symbol_weight_pct": _holding_weight(report["state"], top_symbol),
        "cash_liquidity_actual_pct": _actual_pct(report["state"], "cash_liquidity"),
        "expected_return_pct": float(
            (report["state"].get("analysis", {}).get("cma_analytics", {}).get("actual", {}).get("expected_return_pct") or 0.0)
        ),
        "policy_breaches": int(summary.get("policy_breaches") or 0),
    }


def _adjust_preset_for_dimension(preset: dict[str, Any], dimension: str, value: float) -> dict[str, Any]:
    adjusted = deepcopy(preset)
    holdings = adjusted.get("holdings", [])
    if not holdings:
        return adjusted

    if dimension == "top_holding_pct":
        target = max(0.0, min(95.0, value))
        original_top = float(holdings[0].get("weight_pct") or 0.0)
        tail_total = sum(float(item.get("weight_pct") or 0.0) for item in holdings[1:])
        holdings[0]["weight_pct"] = round(target, 2)
        if tail_total <= 0:
            return adjusted
        remaining = max(0.0, 100.0 - target)
        scale = remaining / tail_total
        for item in holdings[1:]:
            item["weight_pct"] = round(float(item.get("weight_pct") or 0.0) * scale, 2)
        _normalize_holding_total(holdings)
        if original_top == target:
            return adjusted

    elif dimension == "cash_weight_pct":
        target_cash = max(0.0, min(95.0, value))
        cash_idx = next((idx for idx, row in enumerate(holdings) if str(row.get("symbol", "")).upper() in {"CASH", "SGOV", "BIL", "SHV"}), None)
        if cash_idx is None:
            holdings.append({"symbol": "CASH", "weight_pct": 0.0, "bucket": "core"})
            cash_idx = len(holdings) - 1
        current_cash = float(holdings[cash_idx].get("weight_pct") or 0.0)
        delta = target_cash - current_cash
        holdings[cash_idx]["weight_pct"] = round(target_cash, 2)
        non_cash_indices = [idx for idx in range(len(holdings)) if idx != cash_idx]
        non_cash_total = sum(float(holdings[idx].get("weight_pct") or 0.0) for idx in non_cash_indices)
        if non_cash_total > 0:
            for idx in non_cash_indices:
                weight = float(holdings[idx].get("weight_pct") or 0.0)
                holdings[idx]["weight_pct"] = round(max(0.0, weight - (weight / non_cash_total) * delta), 2)
        _normalize_holding_total(holdings)

    elif dimension == "equity_expected_return_pct":
        entries = adjusted.get("cma_entries", [])
        for row in entries:
            if str(row.get("asset_class") or "").strip().lower() == "equities":
                row["expected_return_pct"] = round(value, 2)
                break

    return adjusted


def _evaluate_monotonicity(dimension: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cases) < 2:
        return {"passed": True, "message": "Not enough points for monotonicity check."}
    sorted_cases = sorted(cases, key=lambda item: float(item["value"]))

    if dimension == "top_holding_pct":
        series = [item["top_symbol_weight_pct"] for item in sorted_cases]
        passed = _is_non_decreasing(series)
        return {"passed": passed, "metric": "top_symbol_weight_pct", "series": series}
    if dimension == "cash_weight_pct":
        series = [item["cash_liquidity_actual_pct"] for item in sorted_cases]
        passed = _is_non_decreasing(series)
        return {"passed": passed, "metric": "cash_liquidity_actual_pct", "series": series}
    if dimension == "equity_expected_return_pct":
        series = [item["expected_return_pct"] for item in sorted_cases]
        passed = _is_non_decreasing(series)
        return {"passed": passed, "metric": "expected_return_pct", "series": series}
    return {"passed": True, "message": "No monotonic check configured."}


def _is_non_decreasing(values: list[float]) -> bool:
    return all(values[i] <= values[i + 1] + 1e-6 for i in range(len(values) - 1))


def _actual_pct(state: dict[str, Any], asset_class: str) -> float:
    rows = state.get("allocation", {}).get("actual", [])
    for row in rows:
        if str(row.get("asset_class") or "").strip().lower() == asset_class:
            return float(row.get("actual_pct") or 0.0)
    return 0.0


def _holding_weight(state: dict[str, Any], symbol: str) -> float:
    normalized = (symbol or "").strip().upper()
    for row in state.get("holdings", []):
        if str(row.get("symbol") or "").strip().upper() == normalized:
            return float(row.get("weight_pct") or 0.0)
    return 0.0


def _normalize_holding_total(holdings: list[dict[str, Any]]) -> None:
    total = sum(float(item.get("weight_pct") or 0.0) for item in holdings)
    if total <= 0:
        return
    for item in holdings:
        item["weight_pct"] = round(float(item.get("weight_pct") or 0.0) / total * 100.0, 2)
