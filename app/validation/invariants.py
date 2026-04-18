"""Invariant checks and golden expectation evaluation for Phase 5.7A."""

from __future__ import annotations

from typing import Any


def evaluate_state_invariants(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return invariant failures for a built profile state."""
    failures: list[dict[str, Any]] = []
    analysis = state.get("analysis", {})
    holdings_totals = analysis.get("holdings_totals", {})
    total_weight = holdings_totals.get("total_weight_pct")
    weighted_positions = int(holdings_totals.get("weighted_positions") or 0)
    weighted_count_from_rows = sum(
        1 for item in state.get("holdings", []) if item.get("weight_pct") is not None
    )

    if weighted_positions != weighted_count_from_rows:
        failures.append(
            {
                "code": "weighted_position_count_mismatch",
                "message": (
                    f"holdings_totals.weighted_positions={weighted_positions} but holdings rows imply "
                    f"{weighted_count_from_rows} weighted positions."
                ),
            }
        )

    if total_weight is not None and float(total_weight) < 0:
        failures.append(
            {
                "code": "negative_total_weight",
                "message": f"Total weighted holdings is negative ({total_weight}).",
            }
        )

    policy_fit = analysis.get("policy_fit", {})
    ui_readiness = analysis.get("ui_readiness", {})
    if not _is_policy_configured(ui_readiness):
        policy_status = str(policy_fit.get("status") or "").strip().lower()
        if policy_status == "strong":
            failures.append(
                {
                    "code": "policy_status_overconfident",
                    "message": "Policy status is strong while policy readiness is not configured.",
                }
            )

    allocation_drift = analysis.get("allocation_drift", {})
    drift_rows = allocation_drift.get("rows") or []
    if not _is_allocation_configured(ui_readiness):
        falsely_configured = [r for r in drift_rows if r.get("status") == "within_band"]
        if falsely_configured:
            failures.append(
                {
                    "code": "allocation_status_overconfident",
                    "message": "Allocation rows show within_band even though allocation readiness is unconfigured.",
                }
            )

    if "rebalance_proposal" in analysis:
        rp = analysis["rebalance_proposal"] or {}
        trades = rp.get("trades") or []
        one_way_pct = (rp.get("turnover") or {}).get("one_way_pct")
        if one_way_pct is not None and one_way_pct < 0:
            failures.append(
                {
                    "code": "negative_turnover",
                    "message": f"Rebalance turnover is negative ({one_way_pct}).",
                }
            )
        invalid_trade_pct = [
            t for t in trades if t.get("trade_pct") is not None and float(t.get("trade_pct")) < 0
        ]
        if invalid_trade_pct:
            failures.append(
                {
                    "code": "negative_trade_pct",
                    "message": "At least one rebalance trade has a negative trade_pct value.",
                }
            )

    return failures


def evaluate_expectations(
    *,
    state: dict[str, Any],
    expectations: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate preset golden expectations against the current state."""
    checks: list[dict[str, Any]] = []
    analysis = state.get("analysis", {})
    policy_fit = analysis.get("policy_fit", {})
    rebalance = analysis.get("rebalance_proposal", {})
    attribution = analysis.get("attribution", {})
    cma = analysis.get("cma_analytics", {})
    kpis = analysis.get("kpis", {})
    ui_readiness = analysis.get("ui_readiness", {})

    if "policy_breaches_min" in expectations:
        observed = len(policy_fit.get("breaches") or [])
        minimum = int(expectations["policy_breaches_min"])
        checks.append(
            _assert_check(
                code="policy_breaches_min",
                passed=observed >= minimum,
                message=f"Policy breaches {observed} >= {minimum}",
            )
        )

    if "policy_breaches_max" in expectations:
        observed = len(policy_fit.get("breaches") or [])
        maximum = int(expectations["policy_breaches_max"])
        checks.append(
            _assert_check(
                code="policy_breaches_max",
                passed=observed <= maximum,
                message=f"Policy breaches {observed} <= {maximum}",
            )
        )

    if "rebalance_trades_min" in expectations:
        observed = int((rebalance.get("turnover") or {}).get("trades_count") or 0)
        minimum = int(expectations["rebalance_trades_min"])
        checks.append(
            _assert_check(
                code="rebalance_trades_min",
                passed=observed >= minimum,
                message=f"Rebalance trades {observed} >= {minimum}",
            )
        )

    if "rebalance_trades_max" in expectations:
        observed = int((rebalance.get("turnover") or {}).get("trades_count") or 0)
        maximum = int(expectations["rebalance_trades_max"])
        checks.append(
            _assert_check(
                code="rebalance_trades_max",
                passed=observed <= maximum,
                message=f"Rebalance trades {observed} <= {maximum}",
            )
        )

    if "largest_position_min_pct" in expectations:
        observed = float((kpis.get("largest_position") or {}).get("weight_pct") or 0.0)
        minimum = float(expectations["largest_position_min_pct"])
        checks.append(
            _assert_check(
                code="largest_position_min_pct",
                passed=observed >= minimum,
                message=f"Largest position {observed:.2f}% >= {minimum:.2f}%",
            )
        )

    if "cma_available" in expectations:
        expected = bool(expectations["cma_available"])
        observed = bool(cma.get("available"))
        checks.append(
            _assert_check(
                code="cma_available",
                passed=observed is expected,
                message=f"CMA available is {observed}, expected {expected}",
            )
        )

    if "attribution_available" in expectations:
        expected = bool(expectations["attribution_available"])
        observed = bool(attribution.get("available"))
        checks.append(
            _assert_check(
                code="attribution_available",
                passed=observed is expected,
                message=f"Attribution available is {observed}, expected {expected}",
            )
        )

    if "policy_configured" in expectations:
        expected = bool(expectations["policy_configured"])
        observed = _is_policy_configured(ui_readiness)
        checks.append(
            _assert_check(
                code="policy_configured",
                passed=observed is expected,
                message=f"Policy configured is {observed}, expected {expected}",
            )
        )

    if "allocation_configured" in expectations:
        expected = bool(expectations["allocation_configured"])
        observed = _is_allocation_configured(ui_readiness)
        checks.append(
            _assert_check(
                code="allocation_configured",
                passed=observed is expected,
                message=f"Allocation configured is {observed}, expected {expected}",
            )
        )

    return checks


def summarize_check_results(checks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(checks)
    passed = sum(1 for check in checks if check.get("passed"))
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate_pct": round((passed / total * 100.0), 1) if total else 100.0,
    }


def _assert_check(*, code: str, passed: bool, message: str) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), "message": message}


def _is_policy_configured(ui_readiness: dict[str, Any]) -> bool:
    return bool((ui_readiness.get("policy") or {}).get("configured"))


def _is_allocation_configured(ui_readiness: dict[str, Any]) -> bool:
    return bool((ui_readiness.get("allocation") or {}).get("configured"))

