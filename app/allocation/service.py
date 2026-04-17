"""Strategic asset allocation persistence and actual-allocation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import StrategicAllocationTarget
from app.db.session import get_session
from app.personalization.user_profile import UserProfile

ASSET_CLASS_CATALOG: list[dict[str, str]] = [
    {"key": "equities", "label": "Equities", "default_role": "growth"},
    {"key": "high_quality_bonds", "label": "High-Quality Bonds", "default_role": "income"},
    {"key": "credit", "label": "Credit", "default_role": "income"},
    {"key": "alternatives", "label": "Alternatives", "default_role": "diversifier"},
    {"key": "real_assets", "label": "Real Assets", "default_role": "hedge"},
    {"key": "gold", "label": "Gold", "default_role": "hedge"},
    {"key": "cash_liquidity", "label": "Cash / Liquidity", "default_role": "liquidity"},
]

BENIGN_CASH_SYMBOLS = {"BIL", "SGOV", "SHV", "TBIL", "CASH", "USD", "EUR"}
BOND_SYMBOLS = {"IEF", "TLT", "AGG", "BND", "VGIT", "VGSH"}
CREDIT_SYMBOLS = {"LQD", "HYG", "JNK", "VCIT"}
GOLD_SYMBOLS = {"GLD", "IAU", "GLDM"}
REAL_ASSET_SYMBOLS = {"VNQ", "REET", "PAVE", "DBA", "DBC"}
ALTERNATIVE_SYMBOLS = {"BTAL", "DBMF", "KMLM"}


def allocation_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in ASSET_CLASS_CATALOG]


def default_allocation_targets() -> list[dict[str, Any]]:
    return [
        {
            "asset_class": item["key"],
            "label": item["label"],
            "role": item["default_role"],
            "target_weight_pct": None,
            "min_weight_pct": None,
            "max_weight_pct": None,
        }
        for item in ASSET_CLASS_CATALOG
    ]


def load_allocation_targets(profile_name: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = (
            session.query(StrategicAllocationTarget)
            .filter(
                StrategicAllocationTarget.profile_name == profile_name,
                StrategicAllocationTarget.active.is_(True),
            )
            .order_by(StrategicAllocationTarget.asset_class.asc())
            .all()
        )
        if not rows:
            return []
        return [_row_to_dict(row) for row in rows]


def save_allocation_targets(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    clean_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        asset_class = str(item.get("asset_class", "")).strip().lower()
        if not asset_class or asset_class in seen:
            continue
        seen.add(asset_class)
        clean_rows.append(
            {
                "asset_class": asset_class,
                "role": str(item.get("role", "")).strip().lower() or None,
                "target_weight_pct": _float_or_none(item.get("target_weight_pct")),
                "min_weight_pct": _float_or_none(item.get("min_weight_pct")),
                "max_weight_pct": _float_or_none(item.get("max_weight_pct")),
            }
        )

    with get_session() as session:
        (
            session.query(StrategicAllocationTarget)
            .filter(
                StrategicAllocationTarget.profile_name == profile_name,
                StrategicAllocationTarget.active.is_(True),
            )
            .update(
                {
                    StrategicAllocationTarget.active: False,
                    StrategicAllocationTarget.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        for item in clean_rows:
            session.add(
                StrategicAllocationTarget(
                    profile_name=profile_name,
                    asset_class=item["asset_class"],
                    role=item["role"],
                    target_weight_pct=item["target_weight_pct"],
                    min_weight_pct=item["min_weight_pct"],
                    max_weight_pct=item["max_weight_pct"],
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
    return merge_targets_with_catalog(clean_rows)


def merge_targets_with_catalog(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_key = {str(item["asset_class"]).strip().lower(): item for item in rows}
    merged: list[dict[str, Any]] = []
    for item in ASSET_CLASS_CATALOG:
        row = rows_by_key.get(item["key"], {})
        merged.append(
            {
                "asset_class": item["key"],
                "label": item["label"],
                "role": row.get("role") or item["default_role"],
                "target_weight_pct": row.get("target_weight_pct"),
                "min_weight_pct": row.get("min_weight_pct"),
                "max_weight_pct": row.get("max_weight_pct"),
            }
        )
    return merged


def build_actual_allocation(profile: UserProfile) -> list[dict[str, Any]]:
    weights: dict[str, float] = {item["key"]: 0.0 for item in ASSET_CLASS_CATALOG}
    total_weight = 0.0
    for holding in profile.portfolio_holdings:
        weight = float(holding.weight_pct) if holding.weight_pct is not None else 0.0
        total_weight += weight
        weights[_infer_asset_class(holding.symbol)] += weight
    residual = max(0.0, 100.0 - total_weight)
    if residual > 0:
        weights["cash_liquidity"] += residual

    rows: list[dict[str, Any]] = []
    for item in ASSET_CLASS_CATALOG:
        rows.append(
            {
                "asset_class": item["key"],
                "label": item["label"],
                "actual_pct": round(weights[item["key"]], 2),
                "role": item["default_role"],
            }
        )
    return rows


def _infer_asset_class(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if normalized in BENIGN_CASH_SYMBOLS:
        return "cash_liquidity"
    if normalized in BOND_SYMBOLS:
        return "high_quality_bonds"
    if normalized in CREDIT_SYMBOLS:
        return "credit"
    if normalized in GOLD_SYMBOLS:
        return "gold"
    if normalized in REAL_ASSET_SYMBOLS:
        return "real_assets"
    if normalized in ALTERNATIVE_SYMBOLS:
        return "alternatives"
    return "equities"


def _row_to_dict(row: StrategicAllocationTarget) -> dict[str, Any]:
    label = next((item["label"] for item in ASSET_CLASS_CATALOG if item["key"] == row.asset_class), row.asset_class)
    return {
        "asset_class": row.asset_class,
        "label": label,
        "role": row.role,
        "target_weight_pct": row.target_weight_pct,
        "min_weight_pct": row.min_weight_pct,
        "max_weight_pct": row.max_weight_pct,
    }


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    return float(value)
