"""Validation report rendering helpers."""

from __future__ import annotations

import json
from typing import Any


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)


def report_to_text(report: dict[str, Any]) -> str:
    preset = report.get("preset") or "custom"
    profile = report.get("profile") or "default_user"
    checks = report.get("checks") or {}
    invariants = report.get("invariants") or {}
    summary = report.get("summary") or {}

    lines = [
        f"Validation Report · preset={preset} · profile={profile}",
        f"Status: {report.get('status', 'unknown')}",
        f"Checks: {checks.get('passed', 0)}/{checks.get('total', 0)} passed",
        f"Invariants: {invariants.get('passed', 0)}/{invariants.get('total', 0)} passed",
        "",
        "Key Summary",
        f"- Holdings: {summary.get('holdings_count', 0)}",
        f"- Total weight: {summary.get('holdings_total_weight_pct', 0.0):.1f}%",
        f"- Top 5 concentration: {summary.get('top5_concentration_pct', 0.0):.1f}%",
        f"- Policy breaches: {summary.get('policy_breaches', 0)}",
        f"- Rebalance trades: {summary.get('rebalance_trades', 0)}",
        f"- CMA available: {summary.get('cma_available', False)}",
        f"- Attribution available: {summary.get('attribution_available', False)}",
    ]

    failed_checks = [item for item in report.get("failed_checks", []) if item]
    if failed_checks:
        lines.append("")
        lines.append("Failed Checks")
        for item in failed_checks:
            lines.append(f"- [{item.get('code', 'unknown')}] {item.get('message', '')}")

    failed_invariants = [item for item in report.get("failed_invariants", []) if item]
    if failed_invariants:
        lines.append("")
        lines.append("Failed Invariants")
        for item in failed_invariants:
            lines.append(f"- [{item.get('code', 'unknown')}] {item.get('message', '')}")

    return "\n".join(lines).strip()

