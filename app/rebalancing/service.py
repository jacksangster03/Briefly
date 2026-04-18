"""Rebalancing & Implementation Engine for Phase 5.4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import RebalancingConfig, RebalanceProposal
from app.db.session import get_session
from app.logger import get_logger

logger = get_logger("rebalancing")

_ASSET_CLASS_LABELS: dict[str, str] = {
    "equities": "Equities",
    "high_quality_bonds": "High-Quality Bonds",
    "credit": "Credit",
    "alternatives": "Alternatives",
    "real_assets": "Real Assets",
    "gold": "Gold",
    "cash_liquidity": "Cash / Liquidity",
}

_BOND_SYMBOLS = {"IEF", "TLT", "AGG", "BND", "VGIT", "VGSH"}
_CREDIT_SYMBOLS = {"LQD", "HYG", "JNK", "VCIT"}
_GOLD_SYMBOLS = {"GLD", "IAU", "GLDM"}
_REAL_ASSET_SYMBOLS = {"VNQ", "REET", "PAVE", "DBA", "DBC"}
_ALTERNATIVE_SYMBOLS = {"BTAL", "DBMF", "KMLM"}
_CASH_SYMBOLS = {"BIL", "SGOV", "SHV", "TBIL", "CASH", "USD", "EUR"}


def _infer_asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s in _CASH_SYMBOLS:
        return "cash_liquidity"
    if s in _BOND_SYMBOLS:
        return "high_quality_bonds"
    if s in _CREDIT_SYMBOLS:
        return "credit"
    if s in _GOLD_SYMBOLS:
        return "gold"
    if s in _REAL_ASSET_SYMBOLS:
        return "real_assets"
    if s in _ALTERNATIVE_SYMBOLS:
        return "alternatives"
    return "equities"


_DEFAULT_CONFIG: dict[str, Any] = {
    "method": "drift_threshold",
    "drift_threshold_pct": 5.0,
    "frequency": "quarterly",
    "portfolio_value": None,
    "transaction_cost_bps": 10.0,
    "min_trade_pct": 0.5,
    "tax_aware": False,
    "notes": "",
}


def load_rebalancing_config(profile_name: str) -> dict[str, Any]:
    with get_session() as session:
        row = (
            session.query(RebalancingConfig)
            .filter_by(profile_name=profile_name, active=True)
            .order_by(RebalancingConfig.id.desc())
            .first()
        )
        if row is None:
            return dict(_DEFAULT_CONFIG)
        return {
            "method": row.method or "drift_threshold",
            "drift_threshold_pct": row.drift_threshold_pct if row.drift_threshold_pct is not None else 5.0,
            "frequency": row.frequency or "quarterly",
            "portfolio_value": row.portfolio_value,
            "transaction_cost_bps": row.transaction_cost_bps if row.transaction_cost_bps is not None else 10.0,
            "min_trade_pct": row.min_trade_pct if row.min_trade_pct is not None else 0.5,
            "tax_aware": bool(row.tax_aware),
            "notes": row.notes or "",
        }


def save_rebalancing_config(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    with get_session() as session:
        session.query(RebalancingConfig).filter_by(profile_name=profile_name).update({"active": False})
        row = RebalancingConfig(
            profile_name=profile_name,
            method=payload.get("method", "drift_threshold"),
            drift_threshold_pct=_safe_float(payload.get("drift_threshold_pct"), 5.0),
            frequency=payload.get("frequency", "quarterly"),
            portfolio_value=_safe_float(payload.get("portfolio_value"), None),
            transaction_cost_bps=_safe_float(payload.get("transaction_cost_bps"), 10.0),
            min_trade_pct=_safe_float(payload.get("min_trade_pct"), 0.5),
            tax_aware=bool(payload.get("tax_aware", False)),
            notes=str(payload.get("notes", "")).strip(),
            active=True,
        )
        session.add(row)
        session.commit()
    return load_rebalancing_config(profile_name)


def compute_rebalance_proposal(
    profile_name: str,
    allocation_drift_rows: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    config: dict[str, Any],
    trigger_type: str = "manual",
    persist: bool = False,
) -> dict[str, Any]:
    if not allocation_drift_rows:
        return _unavailable("No strategic allocation configured. Add targets in the Allocation tab.")

    configured = [r for r in allocation_drift_rows if r.get("status") != "unconfigured"]
    if not configured:
        return _unavailable("All asset classes are unconfigured. Add allocation targets in the Allocation tab.")

    trades = _build_trade_list(allocation_drift_rows, config, holdings)
    turnover = _compute_turnover(trades)
    cost = _estimate_cost(turnover, config.get("transaction_cost_bps", 10.0), config.get("portfolio_value"))
    status = _determine_status(trades, config)
    status_label, status_summary = _status_labels(status, trades)

    active_trades = [t for t in trades if not t["skipped"]]
    skipped_trades = [t for t in trades if t["skipped"]]
    high_priority = [t for t in active_trades if t["priority"] == "high"]

    history = load_proposal_history(profile_name, limit=5)

    proposal = {
        "available": True,
        "error": None,
        "status": status,
        "status_label": status_label,
        "status_summary": status_summary,
        "trigger_type": trigger_type,
        "config": {
            "method": config.get("method", "drift_threshold"),
            "drift_threshold_pct": config.get("drift_threshold_pct", 5.0),
            "frequency": config.get("frequency", "quarterly"),
            "portfolio_value": config.get("portfolio_value"),
            "transaction_cost_bps": config.get("transaction_cost_bps", 10.0),
            "min_trade_pct": config.get("min_trade_pct", 0.5),
            "tax_aware": config.get("tax_aware", False),
        },
        "trades": trades,
        "turnover": {
            "one_way_pct": round(turnover, 2),
            "one_way_display": f"{turnover:.1f}%",
            "trades_count": len(active_trades),
            "skipped_count": len(skipped_trades),
            "high_priority_count": len(high_priority),
        },
        "cost": cost,
        "history": history,
    }

    if persist:
        _persist_proposal(profile_name, proposal, config, trigger_type, turnover, cost)

    return proposal


def load_proposal_history(profile_name: str, limit: int = 10) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = (
            session.query(RebalanceProposal)
            .filter_by(profile_name=profile_name)
            .order_by(RebalanceProposal.id.desc())
            .limit(limit)
            .all()
        )
        result = []
        for row in rows:
            trades_data = json.loads(row.trades_json or "[]")
            active_trades = [t for t in trades_data if not t.get("skipped", False)]
            result.append({
                "proposed_at": row.proposed_at.strftime("%Y-%m-%d %H:%M") if row.proposed_at else "",
                "status": row.status or "",
                "turnover_pct": round(row.turnover_pct or 0.0, 1),
                "turnover_display": f"{(row.turnover_pct or 0.0):.1f}%",
                "trigger_type": row.trigger_type or "manual",
                "trades_count": len(active_trades),
            })
        return result


def _build_trade_list(
    drift_rows: list[dict[str, Any]],
    config: dict[str, Any],
    holdings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    min_trade = config.get("min_trade_pct", 0.5)
    portfolio_value = config.get("portfolio_value")
    holding_map = _build_holding_map(holdings)
    trades = []

    for row in drift_rows:
        status = row.get("status", "unconfigured")
        asset_class = row.get("asset_class", "")
        label = _ASSET_CLASS_LABELS.get(asset_class, asset_class.replace("_", " ").title())
        actual = row.get("actual_pct")
        target = row.get("target_pct")
        min_pct = row.get("min_pct")
        max_pct = row.get("max_pct")
        drift = row.get("drift_pct")

        if status == "unconfigured":
            trades.append(_skipped_trade(asset_class, label, actual, target, min_pct, max_pct, drift, "No target configured"))
            continue

        if status == "within_band":
            trades.append(_hold_trade(asset_class, label, actual, target, min_pct, max_pct, drift))
            continue

        if status == "above_band":
            trim_to = max_pct if max_pct is not None else target
            actual_val = actual if actual is not None else 0.0
            trade_pct = max(0.0, actual_val - (trim_to or 0.0))
            direction = "sell"
            trade_dir = -trade_pct
        elif status == "below_band":
            add_to = min_pct if min_pct is not None else target
            actual_val = actual if actual is not None else 0.0
            trade_pct = max(0.0, (add_to or 0.0) - actual_val)
            direction = "buy"
            trade_dir = trade_pct
        else:
            trades.append(_hold_trade(asset_class, label, actual, target, min_pct, max_pct, drift))
            continue

        if trade_pct < min_trade:
            trades.append(_skipped_trade(
                asset_class, label, actual, target, min_pct, max_pct, drift,
                f"Trade size {trade_pct:.1f}pp is below minimum {min_trade:.1f}pp"
            ))
            continue

        priority = "high"
        holding_list = _holding_drill_down(asset_class, direction, holding_map)
        trade_value_display = _trade_value_display(trade_pct, portfolio_value)

        trades.append({
            "asset_class": asset_class,
            "label": label,
            "direction": direction,
            "current_pct": actual,
            "target_pct": target,
            "min_pct": min_pct,
            "max_pct": max_pct,
            "trade_pct": round(trade_pct, 2),
            "trade_direction_pct": round(trade_dir, 2),
            "drift_pct": drift,
            "priority": priority,
            "status": status,
            "skipped": False,
            "skip_reason": None,
            "current_display": _fmt_pct(actual),
            "target_display": _fmt_pct(target),
            "trade_display": f"+{trade_pct:.1f}pp" if direction == "buy" else f"-{trade_pct:.1f}pp",
            "trade_value_display": trade_value_display,
            "holdings": holding_list,
        })

    return trades


def _compute_turnover(trades: list[dict[str, Any]]) -> float:
    active = [t for t in trades if not t.get("skipped", True)]
    gross = sum(abs(t.get("trade_direction_pct", 0.0)) for t in active)
    return gross / 2.0


def _estimate_cost(turnover_pct: float, cost_bps: float, portfolio_value: float | None) -> dict[str, Any]:
    two_sided_turnover = turnover_pct / 100.0 * 2.0
    cost_bps_total = two_sided_turnover * cost_bps
    cost_pct = cost_bps_total / 100.0

    if portfolio_value and portfolio_value > 0:
        cost_value = portfolio_value * cost_pct / 100.0
        cost_value_display = f"~{cost_value:,.0f}"
    else:
        cost_value = None
        cost_value_display = None

    return {
        "estimated_cost_bps": round(cost_bps_total, 2),
        "estimated_cost_pct": round(cost_pct, 4),
        "estimated_cost_value": cost_value,
        "estimated_cost_display": cost_value_display or f"~{cost_bps_total:.1f} bps",
    }


def _determine_status(trades: list[dict[str, Any]], config: dict[str, Any]) -> str:
    active = [t for t in trades if not t.get("skipped", True)]
    if not active:
        configured = [t for t in trades if t.get("status") != "unconfigured" and t.get("direction") != "hold"]
        return "within_tolerance" if configured or trades else "no_allocation"
    priorities = {t.get("priority") for t in active}
    if "high" in priorities:
        return "rebalance_needed"
    if "medium" in priorities:
        return "monitoring"
    return "within_tolerance"


def _status_labels(status: str, trades: list[dict[str, Any]]) -> tuple[str, str]:
    active = [t for t in trades if not t.get("skipped", True)]
    high = [t for t in active if t.get("priority") == "high"]
    if status == "rebalance_needed":
        names = ", ".join(t["label"] for t in high[:3])
        return "Rebalance Required", f"{len(high)} asset class{'es' if len(high) != 1 else ''} outside policy bands: {names}."
    if status == "monitoring":
        return "Monitoring", "Drift approaching threshold. No immediate action required."
    if status == "within_tolerance":
        return "Within Tolerance", "All asset classes are within their configured bands. No rebalancing needed."
    return "No Allocation", "Configure strategic allocation targets to generate rebalance proposals."


def _hold_trade(asset_class: str, label: str, actual: float | None, target: float | None,
                min_pct: float | None, max_pct: float | None, drift: float | None) -> dict[str, Any]:
    return {
        "asset_class": asset_class,
        "label": label,
        "direction": "hold",
        "current_pct": actual,
        "target_pct": target,
        "min_pct": min_pct,
        "max_pct": max_pct,
        "trade_pct": 0.0,
        "trade_direction_pct": 0.0,
        "drift_pct": drift,
        "priority": "low",
        "status": "within_band",
        "skipped": True,
        "skip_reason": "Within band",
        "current_display": _fmt_pct(actual),
        "target_display": _fmt_pct(target),
        "trade_display": "—",
        "trade_value_display": None,
        "holdings": [],
    }


def _skipped_trade(asset_class: str, label: str, actual: float | None, target: float | None,
                   min_pct: float | None, max_pct: float | None, drift: float | None,
                   reason: str) -> dict[str, Any]:
    return {
        "asset_class": asset_class,
        "label": label,
        "direction": "hold",
        "current_pct": actual,
        "target_pct": target,
        "min_pct": min_pct,
        "max_pct": max_pct,
        "trade_pct": 0.0,
        "trade_direction_pct": 0.0,
        "drift_pct": drift,
        "priority": "low",
        "status": "unconfigured" if target is None else "skipped",
        "skipped": True,
        "skip_reason": reason,
        "current_display": _fmt_pct(actual),
        "target_display": _fmt_pct(target),
        "trade_display": "—",
        "trade_value_display": None,
        "holdings": [],
    }


def _build_holding_map(holdings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for h in holdings:
        symbol = h.get("symbol", "")
        ac = _infer_asset_class(symbol)
        result.setdefault(ac, []).append(h)
    for ac in result:
        result[ac].sort(key=lambda x: x.get("weight_pct") or 0.0, reverse=True)
    return result


def _holding_drill_down(asset_class: str, direction: str, holding_map: dict[str, list]) -> list[dict[str, Any]]:
    holdings = holding_map.get(asset_class, [])
    action = "trim" if direction == "sell" else "add"
    result = []
    for h in holdings[:6]:
        result.append({
            "symbol": h.get("symbol", ""),
            "weight_pct": h.get("weight_pct"),
            "action": action,
            "weight_display": _fmt_pct(h.get("weight_pct")),
        })
    return result


def _trade_value_display(trade_pct: float, portfolio_value: float | None) -> str | None:
    if not portfolio_value or portfolio_value <= 0:
        return None
    value = portfolio_value * trade_pct / 100.0
    return f"~{value:,.0f}"


def _persist_proposal(
    profile_name: str,
    proposal: dict[str, Any],
    config: dict[str, Any],
    trigger_type: str,
    turnover_pct: float,
    cost: dict[str, Any],
) -> None:
    with get_session() as session:
        row = RebalanceProposal(
            profile_name=profile_name,
            proposed_at=datetime.now(timezone.utc),
            status=proposal.get("status", ""),
            trigger_type=trigger_type,
            turnover_pct=round(turnover_pct, 4),
            estimated_cost_bps=cost.get("estimated_cost_bps"),
            portfolio_value=config.get("portfolio_value"),
            trades_json=json.dumps(proposal.get("trades", [])),
            config_snapshot_json=json.dumps(config),
        )
        session.add(row)
        session.commit()


def _unavailable(error: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "status": "no_allocation",
        "status_label": "Not Configured",
        "status_summary": error,
        "trigger_type": "manual",
        "config": dict(_DEFAULT_CONFIG),
        "trades": [],
        "turnover": {"one_way_pct": 0.0, "one_way_display": "0.0%", "trades_count": 0, "skipped_count": 0, "high_priority_count": 0},
        "cost": {"estimated_cost_bps": 0.0, "estimated_cost_pct": 0.0, "estimated_cost_value": None, "estimated_cost_display": "—"},
        "history": [],
    }


def _safe_float(value: Any, default: float | None) -> float | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"
