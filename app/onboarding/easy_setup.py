"""Easy setup onboarding presets and deterministic mapping for Phase 5.9."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.allocation.service import allocation_catalog
from app.web.control_plane_service import (
    normalize_profile_name,
    save_allocation,
    save_benchmark,
    save_cma,
    save_cma_corr,
    save_policy,
    save_risk_config,
    save_rebalancing_config_for_profile,
)

_ASSET_CLASSES = [item["key"] for item in allocation_catalog()]


@dataclass
class EasySetupInputs:
    investor_type: str
    horizon_bucket: str
    risk_comfort: str
    base_currency: str
    home_region: str
    has_holdings_file: bool
    infer_from_holdings: bool
    starting_mix: str
    loss_averse: bool
    concentration_tolerant: bool
    auto_rebalancing: bool


def default_inputs() -> EasySetupInputs:
    return EasySetupInputs(
        investor_type="long_term_individual",
        horizon_bucket="7_15",
        risk_comfort="medium",
        base_currency="EUR",
        home_region="global",
        has_holdings_file=False,
        infer_from_holdings=False,
        starting_mix="balanced",
        loss_averse=False,
        concentration_tolerant=False,
        auto_rebalancing=True,
    )


def build_plan(inputs: EasySetupInputs, *, holdings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    selected_mix = _select_mix(inputs, holdings or [])
    policy = _build_policy_payload(inputs, selected_mix)
    allocation = _build_allocation_payload(inputs, selected_mix)
    benchmark = _build_benchmark_payload(inputs, selected_mix)
    cma_entries = _build_cma_entries(inputs, selected_mix)
    cma_corr = _build_cma_correlations(cma_entries)
    risk_prefs = _build_risk_preferences(inputs)
    rebalancing = _build_rebalancing_payload(inputs)

    return {
        "selected_mix": selected_mix,
        "policy": policy,
        "allocation": allocation,
        "benchmark": benchmark,
        "cma_entries": cma_entries,
        "cma_correlations": cma_corr,
        "risk_preferences": risk_prefs,
        "rebalancing": rebalancing,
        "summary": {
            "policy": _policy_summary(policy),
            "allocation": _allocation_summary(allocation),
            "benchmark": f"{benchmark.get('name') or benchmark.get('base_symbol')}",
            "cma": f"Preset assumptions for {inputs.home_region.upper()} / {inputs.horizon_bucket}",
            "risk": f"Lookback {risk_prefs['risk.lookback_days']}d · Rf {risk_prefs['risk.risk_free_rate_pct']:.1f}%",
            "rebalancing": (
                f"{'Auto' if inputs.auto_rebalancing else 'Manual'} · "
                f"{rebalancing['method']} · {rebalancing['drift_threshold_pct']:.1f}% drift"
            ),
        },
    }


def apply_plan(profile_name: str, plan: dict[str, Any]) -> dict[str, Any]:
    normalized_profile = normalize_profile_name(profile_name)
    policy = save_policy(normalized_profile, plan["policy"])
    allocation = save_allocation(normalized_profile, plan["allocation"])
    benchmark = save_benchmark(normalized_profile, plan["benchmark"])
    cma_entries = save_cma(normalized_profile, plan["cma_entries"])
    cma_correlations = save_cma_corr(normalized_profile, plan["cma_correlations"])
    rebalancing = save_rebalancing_config_for_profile(normalized_profile, plan["rebalancing"])
    save_risk_config(
        normalized_profile,
        lookback_days=int(plan["risk_preferences"]["risk.lookback_days"]),
        risk_free_rate_pct=float(plan["risk_preferences"]["risk.risk_free_rate_pct"]),
    )

    return {
        "policy": policy,
        "allocation_rows": allocation,
        "benchmark": benchmark,
        "cma_entries": cma_entries,
        "cma_correlations": cma_correlations,
        "rebalancing": rebalancing,
    }


def _select_mix(inputs: EasySetupInputs, holdings: list[dict[str, Any]]) -> str:
    if inputs.infer_from_holdings and holdings:
        equities_weight = 0.0
        total = 0.0
        bond_like = {"high_quality_bonds", "credit"}
        symbol_bond_map = {"BND", "AGG", "LQD", "HYG", "JNK", "IEF", "TLT"}
        for row in holdings:
            weight = float(row.get("weight_pct") or 0.0)
            total += max(weight, 0.0)
            symbol = str(row.get("symbol") or "").upper()
            sector = str(row.get("sector") or "").lower()
            if symbol in symbol_bond_map or "bond" in sector:
                continue
            equities_weight += max(weight, 0.0)
        ratio = equities_weight / total if total > 0 else 0.0
        if ratio >= 0.85:
            return "aggressive"
        if ratio >= 0.70:
            return "growth"
        if ratio >= 0.45:
            return "balanced"
        return "conservative"
    return inputs.starting_mix or "balanced"


def _build_policy_payload(inputs: EasySetupInputs, mix: str) -> dict[str, Any]:
    horizon_years = {"lt3": 2.0, "3_7": 5.0, "7_15": 10.0, "15p": 20.0}.get(inputs.horizon_bucket, 10.0)
    mix_targets = {
        "conservative": {"ret": 4.5, "vol": 10.0, "dd": 15.0, "equity_max": 45.0},
        "balanced": {"ret": 6.0, "vol": 14.0, "dd": 22.0, "equity_max": 70.0},
        "growth": {"ret": 7.0, "vol": 17.0, "dd": 28.0, "equity_max": 85.0},
        "aggressive": {"ret": 8.0, "vol": 20.0, "dd": 35.0, "equity_max": 95.0},
    }[mix]
    risk_adjust = {"low": -1.0, "medium": 0.0, "high": 1.0}.get(inputs.risk_comfort, 0.0)
    target_return = max(3.0, mix_targets["ret"] + 0.4 * risk_adjust)
    max_vol = max(8.0, mix_targets["vol"] + 1.5 * risk_adjust)
    max_dd = max(10.0, mix_targets["dd"] + 3.0 * risk_adjust)
    if inputs.loss_averse:
        max_vol = max(8.0, max_vol - 2.0)
        max_dd = max(10.0, max_dd - 5.0)
    single_name = 12.0 if inputs.concentration_tolerant else 8.0
    liquidity = 10.0 if inputs.loss_averse else 5.0
    governance = "quarterly" if horizon_years <= 7 else "semi_annual"

    investor_map = {
        "beginner": "individual",
        "long_term_individual": "individual",
        "conservative_income": "advisor",
        "aggressive_growth": "individual",
        "institutional_advanced": "institutional",
    }
    return {
        "investor_type": investor_map.get(inputs.investor_type, "individual"),
        "base_currency": (inputs.base_currency or "EUR").upper(),
        "investment_horizon_years": horizon_years,
        "liquidity_need_percent": liquidity,
        "target_return_percent": round(target_return, 1),
        "max_volatility_percent": round(max_vol, 1),
        "max_drawdown_percent": round(max_dd, 1),
        "single_name_limit_percent": single_name,
        "max_equity_percent": mix_targets["equity_max"],
        "min_liquid_assets_percent": liquidity,
        "benchmark_policy": f"{mix.title()} policy benchmark ({inputs.home_region.title()} focus)",
        "rebalancing_policy": "threshold" if inputs.auto_rebalancing else "calendar",
        "prohibited_assets": [],
        "governance_review_frequency": governance,
        "notes": "Auto-generated by Easy Setup (Phase 5.9).",
    }


def _build_allocation_payload(inputs: EasySetupInputs, mix: str) -> list[dict[str, Any]]:
    mixes: dict[str, dict[str, float]] = {
        "conservative": {
            "equities": 25.0,
            "high_quality_bonds": 45.0,
            "credit": 15.0,
            "alternatives": 5.0,
            "real_assets": 3.0,
            "gold": 2.0,
            "cash_liquidity": 5.0,
        },
        "balanced": {
            "equities": 55.0,
            "high_quality_bonds": 25.0,
            "credit": 8.0,
            "alternatives": 4.0,
            "real_assets": 4.0,
            "gold": 2.0,
            "cash_liquidity": 2.0,
        },
        "growth": {
            "equities": 72.0,
            "high_quality_bonds": 14.0,
            "credit": 5.0,
            "alternatives": 3.0,
            "real_assets": 3.0,
            "gold": 1.0,
            "cash_liquidity": 2.0,
        },
        "aggressive": {
            "equities": 85.0,
            "high_quality_bonds": 6.0,
            "credit": 3.0,
            "alternatives": 2.0,
            "real_assets": 2.0,
            "gold": 1.0,
            "cash_liquidity": 1.0,
        },
    }
    target = mixes[mix]
    rows: list[dict[str, Any]] = []
    for item in allocation_catalog():
        ac = item["key"]
        t = float(target.get(ac, 0.0))
        band = 5.0 if ac == "equities" else 3.0
        rows.append(
            {
                "asset_class": ac,
                "role": item["default_role"],
                "target_weight_pct": t,
                "min_weight_pct": max(0.0, t - band),
                "max_weight_pct": min(100.0, t + band),
            }
        )
    return rows


def _build_benchmark_payload(inputs: EasySetupInputs, mix: str) -> dict[str, Any]:
    region_symbol = {
        "us": "SPY",
        "uk": "VGK",
        "europe": "VGK",
        "global": "ACWI",
    }.get(inputs.home_region, "ACWI")
    name_prefix = {"conservative": "Conservative", "balanced": "Balanced", "growth": "Growth", "aggressive": "Aggressive"}[mix]
    benchmark_type = "policy_blend" if mix in {"conservative", "balanced"} else "market_index"
    return {
        "benchmark_type": benchmark_type,
        "name": f"{name_prefix} {inputs.home_region.title()} default benchmark",
        "base_symbol": region_symbol,
        "components": [],
        "notes": "Auto-generated by Easy Setup.",
    }


def _build_cma_entries(inputs: EasySetupInputs, mix: str) -> list[dict[str, Any]]:
    premium = {"conservative": -0.5, "balanced": 0.0, "growth": 0.5, "aggressive": 0.8}[mix]
    region_shift = {"us": 0.2, "uk": 0.1, "europe": 0.1, "global": 0.0}.get(inputs.home_region, 0.0)
    base = {
        "equities": (7.2, 17.0),
        "high_quality_bonds": (3.2, 6.5),
        "credit": (4.7, 9.0),
        "alternatives": (5.6, 11.0),
        "real_assets": (5.2, 13.0),
        "gold": (3.5, 15.0),
        "cash_liquidity": (2.2, 1.5),
    }
    rows: list[dict[str, Any]] = []
    for ac, (ret, vol) in base.items():
        ret_adj = ret + premium + region_shift * (1.0 if ac == "equities" else 0.5)
        if inputs.loss_averse and ac in {"equities", "real_assets"}:
            ret_adj -= 0.2
        rows.append(
            {
                "asset_class": ac,
                "expected_return_pct": round(ret_adj, 2),
                "expected_volatility_pct": vol,
                "notes": "Easy setup preset",
            }
        )
    return rows


def _build_cma_correlations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    asset_classes = [row["asset_class"] for row in entries]
    corr_defaults = {
        ("equities", "high_quality_bonds"): 0.15,
        ("equities", "credit"): 0.55,
        ("equities", "alternatives"): 0.45,
        ("equities", "real_assets"): 0.60,
        ("equities", "gold"): 0.05,
        ("equities", "cash_liquidity"): 0.00,
        ("high_quality_bonds", "credit"): 0.40,
        ("high_quality_bonds", "alternatives"): 0.20,
        ("high_quality_bonds", "real_assets"): 0.15,
        ("high_quality_bonds", "gold"): 0.10,
        ("high_quality_bonds", "cash_liquidity"): 0.30,
        ("credit", "alternatives"): 0.35,
        ("credit", "real_assets"): 0.30,
        ("credit", "gold"): 0.05,
        ("credit", "cash_liquidity"): 0.15,
        ("alternatives", "real_assets"): 0.35,
        ("alternatives", "gold"): 0.10,
        ("alternatives", "cash_liquidity"): 0.10,
        ("real_assets", "gold"): 0.20,
        ("real_assets", "cash_liquidity"): 0.05,
        ("gold", "cash_liquidity"): 0.05,
    }
    rows: list[dict[str, Any]] = []
    for i, ac_a in enumerate(asset_classes):
        for j, ac_b in enumerate(asset_classes):
            if j <= i:
                continue
            corr = corr_defaults.get((ac_a, ac_b), corr_defaults.get((ac_b, ac_a), 0.2))
            rows.append({"asset_class_a": ac_a, "asset_class_b": ac_b, "correlation": corr})
    return rows


def _build_risk_preferences(inputs: EasySetupInputs) -> dict[str, Any]:
    lookback = 756 if inputs.investor_type == "institutional_advanced" else 252
    rfr_map = {"EUR": 2.5, "USD": 4.0, "GBP": 3.5, "OTHER": 3.0}
    return {
        "risk.lookback_days": lookback,
        "risk.risk_free_rate_pct": float(rfr_map.get((inputs.base_currency or "EUR").upper(), 3.0)),
    }


def _build_rebalancing_payload(inputs: EasySetupInputs) -> dict[str, Any]:
    if inputs.auto_rebalancing:
        return {
            "method": "drift_threshold",
            "drift_threshold_pct": 5.0 if not inputs.loss_averse else 4.0,
            "frequency": "quarterly",
            "portfolio_value": None,
            "transaction_cost_bps": 10.0,
            "min_trade_pct": 0.5,
            "tax_aware": False,
            "notes": "Auto-generated by Easy Setup.",
        }
    return {
        "method": "calendar",
        "drift_threshold_pct": 7.0,
        "frequency": "annual",
        "portfolio_value": None,
        "transaction_cost_bps": 10.0,
        "min_trade_pct": 0.5,
        "tax_aware": False,
        "notes": "Manual follow-up requested during Easy Setup.",
    }


def _policy_summary(policy: dict[str, Any]) -> str:
    return (
        f"Target {policy['target_return_percent']:.1f}% · "
        f"Max vol {policy['max_volatility_percent']:.1f}% · "
        f"Max drawdown {policy['max_drawdown_percent']:.1f}%"
    )


def _allocation_summary(rows: list[dict[str, Any]]) -> str:
    top = sorted(rows, key=lambda r: float(r.get("target_weight_pct") or 0.0), reverse=True)[:2]
    return " + ".join(
        f"{item['asset_class'].replace('_', ' ').title()} {float(item.get('target_weight_pct') or 0.0):.0f}%"
        for item in top
    )
