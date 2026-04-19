"""Scenario helpers for simulation lab."""

from __future__ import annotations

from typing import Any

from app.simulation.engines import deterministic_scenario_impacts


def scenario_pack(
    *,
    weights_by_symbol: dict[str, float],
    symbol_to_asset_class: dict[str, str],
) -> list[dict[str, Any]]:
    rows = deterministic_scenario_impacts(
        weights_by_symbol=weights_by_symbol,
        symbol_to_asset_class=symbol_to_asset_class,
    )
    for row in rows:
        row["action"] = _action_for_impact(float(row.get("impact_pct") or 0.0))
    return rows


def _action_for_impact(impact_pct: float) -> str:
    magnitude = abs(impact_pct)
    if magnitude >= 8:
        return "rebalance candidate"
    if magnitude >= 4:
        return "monitor"
    if magnitude >= 2:
        return "coverage boost"
    return "no action"

