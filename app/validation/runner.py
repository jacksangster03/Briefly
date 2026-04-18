"""Preset application and validation runner for Phase 5.7A."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.allocation.service import save_allocation_targets
from app.benchmark.service import save_benchmark_config
from app.cma.service import save_cma_correlations, save_cma_entries
from app.policy.service import save_investor_policy
from app.portfolio.service import replace_holdings_snapshot
from app.rebalancing.service import save_rebalancing_config
from app.schemas.portfolio import PortfolioHolding
from app.settings import Settings
from app.validation.invariants import (
    evaluate_expectations,
    evaluate_state_invariants,
    summarize_check_results,
)
from app.validation.presets import get_preset
from app.web.control_plane_service import build_profile_state, refresh_risk_for_profile


def apply_preset(
    *,
    profile_name: str,
    preset_name: str,
    include_risk_refresh: bool = False,
) -> dict[str, Any]:
    """Apply a canonical preset to a profile."""
    preset = get_preset(preset_name)
    return apply_preset_payload(
        profile_name=profile_name,
        preset_name=preset_name,
        preset=preset,
        include_risk_refresh=include_risk_refresh,
    )


def apply_preset_payload(
    *,
    profile_name: str,
    preset_name: str,
    preset: dict[str, Any],
    include_risk_refresh: bool = False,
) -> dict[str, Any]:
    """Apply a pre-resolved preset payload to a profile."""
    normalized_profile = _normalize_profile(profile_name)

    holdings_payload = preset.get("holdings") or []
    holdings: list[PortfolioHolding] = []
    for row in holdings_payload:
        holdings.append(
            PortfolioHolding(
                profile_name=normalized_profile,
                symbol=str(row.get("symbol") or "").strip().upper(),
                weight_pct=_float_or_none(row.get("weight_pct")),
                bucket=str(row.get("bucket") or "").strip().lower() or None,
                sector_override=str(row.get("sector_override") or "").strip().lower() or None,
                as_of_date=date.today(),
            )
        )

    imported_count = replace_holdings_snapshot(
        profile_name=normalized_profile,
        holdings=holdings,
        as_of_date=date.today(),
    )

    save_investor_policy(normalized_profile, preset.get("policy") or {})
    save_allocation_targets(normalized_profile, preset.get("allocation") or [])
    save_benchmark_config(normalized_profile, preset.get("benchmark") or {})
    save_cma_entries(normalized_profile, preset.get("cma_entries") or [])
    save_cma_correlations(normalized_profile, preset.get("cma_correlations") or [])
    save_rebalancing_config(normalized_profile, preset.get("rebalancing_config") or {})

    if include_risk_refresh:
        refresh_risk_for_profile(normalized_profile)

    result = {
        "profile": normalized_profile,
        "preset": preset_name,
        "holdings_imported": imported_count,
    }
    return result


def validate_profile_state(
    *,
    settings: Settings,
    profile_name: str,
    preset_name: str | None = None,
    expectations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build state and evaluate invariant + expectation checks."""
    state = build_profile_state(settings, _normalize_profile(profile_name))
    failed_invariants = evaluate_state_invariants(state)
    expectation_checks = evaluate_expectations(state=state, expectations=expectations or {})
    check_summary = summarize_check_results(expectation_checks)
    invariant_checks = [
        {"code": item["code"], "passed": False, "message": item["message"]}
        for item in failed_invariants
    ]
    invariant_summary = summarize_check_results(
        invariant_checks if invariant_checks else [{"code": "none", "passed": True, "message": "No invariant failures"}]
    )
    if not failed_invariants:
        invariant_summary = {"total": 0, "passed": 0, "failed": 0, "pass_rate_pct": 100.0}

    report = {
        "status": "pass" if (check_summary["failed"] == 0 and not failed_invariants) else "fail",
        "profile": _normalize_profile(profile_name),
        "preset": preset_name,
        "checks": check_summary,
        "invariants": invariant_summary,
        "failed_checks": [item for item in expectation_checks if not item.get("passed")],
        "failed_invariants": failed_invariants,
        "summary": _build_summary(state),
        "state": state,
    }
    return report


def run_preset_validation(
    *,
    settings: Settings,
    preset_name: str,
    profile_name: str,
    include_risk_refresh: bool = False,
) -> dict[str, Any]:
    """Apply preset then run full validation report."""
    preset = get_preset(preset_name)
    apply_result = apply_preset(
        profile_name=profile_name,
        preset_name=preset_name,
        include_risk_refresh=include_risk_refresh,
    )
    report = validate_profile_state(
        settings=settings,
        profile_name=profile_name,
        preset_name=preset_name,
        expectations=preset.get("expectations") or {},
    )
    report["apply_result"] = apply_result
    return report


def _build_summary(state: dict[str, Any]) -> dict[str, Any]:
    analysis = state.get("analysis", {})
    totals = analysis.get("holdings_totals", {})
    policy_fit = analysis.get("policy_fit", {})
    rebalance = analysis.get("rebalance_proposal", {})
    attribution = analysis.get("attribution", {})
    cma = analysis.get("cma_analytics", {})
    risk = analysis.get("risk_analytics", {})
    benchmark = analysis.get("benchmark_summary", {})
    timing = analysis.get("timing", {})
    kpis = analysis.get("kpis", {})

    return {
        "holdings_count": len(state.get("holdings", [])),
        "holdings_weighted_count": int(totals.get("weighted_positions") or 0),
        "holdings_total_weight_pct": float(totals.get("total_weight_pct") or 0.0),
        "top5_concentration_pct": float(kpis.get("top5_concentration_pct") or 0.0),
        "policy_breaches": len(policy_fit.get("breaches") or []),
        "policy_status": policy_fit.get("status"),
        "allocation_status": (analysis.get("allocation_drift") or {}).get("status"),
        "benchmark_name": benchmark.get("name"),
        "risk_available": bool(risk.get("available")),
        "cma_available": bool(cma.get("available")),
        "attribution_available": bool(attribution.get("available")),
        "rebalance_status": rebalance.get("status"),
        "rebalance_trades": int((rebalance.get("turnover") or {}).get("trades_count") or 0),
        "next_morning_send_local": timing.get("next_morning_send_local"),
    }


def _normalize_profile(profile_name: str) -> str:
    return (profile_name or "default_user").strip() or "default_user"


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
