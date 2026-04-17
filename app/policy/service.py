"""Persistence helpers for investor policy state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import InvestorPolicy
from app.db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_investor_policy() -> dict[str, Any]:
    return {
        "investor_type": "",
        "base_currency": "EUR",
        "investment_horizon_years": None,
        "liquidity_need_percent": None,
        "target_return_percent": None,
        "max_volatility_percent": None,
        "max_drawdown_percent": None,
        "single_name_limit_percent": None,
        "max_equity_percent": None,
        "min_liquid_assets_percent": None,
        "benchmark_policy": "",
        "rebalancing_policy": "",
        "prohibited_assets": [],
        "governance_review_frequency": "",
        "notes": "",
    }


def load_investor_policy(profile_name: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = (
            session.query(InvestorPolicy)
            .filter(
                InvestorPolicy.profile_name == profile_name,
                InvestorPolicy.active.is_(True),
            )
            .order_by(InvestorPolicy.updated_at.desc())
            .first()
        )
        if row is None:
            return None
        return _row_to_dict(row)


def save_investor_policy(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean = default_investor_policy()
    clean.update(payload or {})
    prohibited_assets = clean.get("prohibited_assets") or []
    if isinstance(prohibited_assets, str):
        prohibited_assets = [item.strip() for item in prohibited_assets.split(",") if item.strip()]

    now = _utcnow()
    with get_session() as session:
        row = (
            session.query(InvestorPolicy)
            .filter(InvestorPolicy.profile_name == profile_name)
            .order_by(InvestorPolicy.updated_at.desc())
            .first()
        )
        if row is None:
            row = InvestorPolicy(profile_name=profile_name, created_at=now)
            session.add(row)
        row.profile_name = profile_name
        row.investor_type = _string_or_none(clean.get("investor_type"))
        row.base_currency = _string_or_none(clean.get("base_currency"))
        row.investment_horizon_years = _float_or_none(clean.get("investment_horizon_years"))
        row.liquidity_need_percent = _float_or_none(clean.get("liquidity_need_percent"))
        row.target_return_percent = _float_or_none(clean.get("target_return_percent"))
        row.max_volatility_percent = _float_or_none(clean.get("max_volatility_percent"))
        row.max_drawdown_percent = _float_or_none(clean.get("max_drawdown_percent"))
        row.single_name_limit_percent = _float_or_none(clean.get("single_name_limit_percent"))
        row.max_equity_percent = _float_or_none(clean.get("max_equity_percent"))
        row.min_liquid_assets_percent = _float_or_none(clean.get("min_liquid_assets_percent"))
        row.benchmark_policy = _string_or_none(clean.get("benchmark_policy"))
        row.rebalancing_policy = _string_or_none(clean.get("rebalancing_policy"))
        row.prohibited_assets_json = prohibited_assets
        row.governance_review_frequency = _string_or_none(clean.get("governance_review_frequency"))
        row.notes = _string_or_none(clean.get("notes"))
        row.active = True
        row.updated_at = now
        session.flush()
        return _row_to_dict(row)


def _row_to_dict(row: InvestorPolicy) -> dict[str, Any]:
    return {
        "investor_type": row.investor_type or "",
        "base_currency": row.base_currency or "EUR",
        "investment_horizon_years": row.investment_horizon_years,
        "liquidity_need_percent": row.liquidity_need_percent,
        "target_return_percent": row.target_return_percent,
        "max_volatility_percent": row.max_volatility_percent,
        "max_drawdown_percent": row.max_drawdown_percent,
        "single_name_limit_percent": row.single_name_limit_percent,
        "max_equity_percent": row.max_equity_percent,
        "min_liquid_assets_percent": row.min_liquid_assets_percent,
        "benchmark_policy": row.benchmark_policy or "",
        "rebalancing_policy": row.rebalancing_policy or "",
        "prohibited_assets": row.prohibited_assets_json or [],
        "governance_review_frequency": row.governance_review_frequency or "",
        "notes": row.notes or "",
    }


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    return float(value)


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
