"""Canonical portfolio presets for validation and simulation runs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CANONICAL_ASSET_CLASSES = [
    "equities",
    "high_quality_bonds",
    "credit",
    "alternatives",
    "real_assets",
    "gold",
    "cash_liquidity",
]

_BASE_POLICY = {
    "investor_type": "family_office",
    "base_currency": "EUR",
    "investment_horizon_years": 10,
    "liquidity_need_percent": 5.0,
    "target_return_percent": 8.0,
    "max_volatility_percent": 17.0,
    "max_drawdown_percent": 30.0,
    "single_name_limit_percent": 15.0,
    "max_equity_percent": 75.0,
    "min_liquid_assets_percent": 5.0,
    "benchmark_policy": "Global policy blend",
    "rebalancing_policy": "threshold",
    "prohibited_assets": [],
    "governance_review_frequency": "quarterly",
    "notes": "Validation preset baseline",
}

_BASE_ALLOCATION = [
    {"asset_class": "equities", "role": "growth", "target_weight_pct": 60.0, "min_weight_pct": 50.0, "max_weight_pct": 70.0},
    {"asset_class": "high_quality_bonds", "role": "income", "target_weight_pct": 20.0, "min_weight_pct": 10.0, "max_weight_pct": 30.0},
    {"asset_class": "credit", "role": "income", "target_weight_pct": 5.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
    {"asset_class": "alternatives", "role": "diversifier", "target_weight_pct": 5.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
    {"asset_class": "real_assets", "role": "hedge", "target_weight_pct": 5.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
    {"asset_class": "gold", "role": "hedge", "target_weight_pct": 2.0, "min_weight_pct": 0.0, "max_weight_pct": 5.0},
    {"asset_class": "cash_liquidity", "role": "liquidity", "target_weight_pct": 3.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
]

_BASE_CMA = [
    {"asset_class": "equities", "expected_return_pct": 9.0, "expected_volatility_pct": 18.0, "notes": "Global equities BB"},
    {"asset_class": "high_quality_bonds", "expected_return_pct": 3.8, "expected_volatility_pct": 6.0, "notes": "Core sovereigns"},
    {"asset_class": "credit", "expected_return_pct": 5.3, "expected_volatility_pct": 9.5, "notes": "IG/HY blend"},
    {"asset_class": "alternatives", "expected_return_pct": 6.2, "expected_volatility_pct": 11.0, "notes": "Diversifier sleeve"},
    {"asset_class": "real_assets", "expected_return_pct": 6.0, "expected_volatility_pct": 13.0, "notes": "REIT/infra"},
    {"asset_class": "gold", "expected_return_pct": 4.7, "expected_volatility_pct": 15.0, "notes": "Inflation hedge"},
    {"asset_class": "cash_liquidity", "expected_return_pct": 2.5, "expected_volatility_pct": 1.0, "notes": "Cash/T-bill"},
]

_BASE_CORRELATIONS = [
    {"asset_class_a": "equities", "asset_class_b": "high_quality_bonds", "correlation": 0.15},
    {"asset_class_a": "equities", "asset_class_b": "credit", "correlation": 0.55},
    {"asset_class_a": "equities", "asset_class_b": "alternatives", "correlation": 0.45},
    {"asset_class_a": "equities", "asset_class_b": "real_assets", "correlation": 0.58},
    {"asset_class_a": "equities", "asset_class_b": "gold", "correlation": 0.1},
    {"asset_class_a": "high_quality_bonds", "asset_class_b": "credit", "correlation": 0.35},
    {"asset_class_a": "high_quality_bonds", "asset_class_b": "gold", "correlation": 0.05},
    {"asset_class_a": "credit", "asset_class_b": "real_assets", "correlation": 0.4},
    {"asset_class_a": "alternatives", "asset_class_b": "real_assets", "correlation": 0.3},
    {"asset_class_a": "gold", "asset_class_b": "cash_liquidity", "correlation": 0.0},
]

_BASE_REBALANCING = {
    "method": "drift_threshold",
    "drift_threshold_pct": 5.0,
    "frequency": "quarterly",
    "portfolio_value": 1_000_000,
    "transaction_cost_bps": 10.0,
    "min_trade_pct": 0.5,
    "tax_aware": False,
    "notes": "",
}

PRESETS: dict[str, dict[str, Any]] = {
    "concentrated_ai_growth": {
        "title": "Concentrated AI Growth",
        "description": "Concentrated US growth portfolio for concentration, semis, and policy-cap tests.",
        "holdings": [
            {"symbol": "NVDA", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "MSFT", "weight_pct": 15.0, "bucket": "core"},
            {"symbol": "META", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "AMZN", "weight_pct": 11.0, "bucket": "core"},
            {"symbol": "PLTR", "weight_pct": 9.0, "bucket": "satellite"},
            {"symbol": "ASML", "weight_pct": 8.0, "bucket": "core"},
            {"symbol": "AAPL", "weight_pct": 8.0, "bucket": "core"},
            {"symbol": "GOOGL", "weight_pct": 7.0, "bucket": "core"},
            {"symbol": "TSLA", "weight_pct": 5.0, "bucket": "satellite"},
            {"symbol": "CASH", "weight_pct": 5.0, "bucket": "core"},
        ],
        "expectations": {
            "policy_breaches_min": 1,
            "largest_position_min_pct": 19.0,
            "cma_available": True,
            "attribution_available": True,
        },
    },
    "balanced_60_40": {
        "title": "Balanced 60/40",
        "description": "Balanced multi-asset profile used as a stable baseline for regression checks.",
        "holdings": [
            {"symbol": "ACWI", "weight_pct": 30.0, "bucket": "core"},
            {"symbol": "SPY", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "QQQ", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "BND", "weight_pct": 18.0, "bucket": "core"},
            {"symbol": "IEF", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "LQD", "weight_pct": 5.0, "bucket": "satellite"},
            {"symbol": "GLD", "weight_pct": 3.0, "bucket": "satellite"},
            {"symbol": "CASH", "weight_pct": 2.0, "bucket": "core"},
        ],
        "expectations": {
            "policy_breaches_max": 1,
            "rebalance_trades_max": 3,
            "cma_available": True,
        },
    },
    "global_multi_asset": {
        "title": "Global Multi-Asset",
        "description": "Global sleeve mix across equities, bonds, credit, commodities, and liquidity.",
        "holdings": [
            {"symbol": "ACWI", "weight_pct": 28.0, "bucket": "core"},
            {"symbol": "IEFA", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "EEM", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "BND", "weight_pct": 18.0, "bucket": "core"},
            {"symbol": "VGIT", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "HYG", "weight_pct": 6.0, "bucket": "satellite"},
            {"symbol": "VNQ", "weight_pct": 6.0, "bucket": "satellite"},
            {"symbol": "GLD", "weight_pct": 4.0, "bucket": "satellite"},
            {"symbol": "DBMF", "weight_pct": 4.0, "bucket": "satellite"},
            {"symbol": "SGOV", "weight_pct": 4.0, "bucket": "core"},
        ],
        "expectations": {
            "cma_available": True,
            "attribution_available": True,
        },
    },
    "healthcare_concentrated": {
        "title": "Healthcare Concentrated",
        "description": "Sector-concentrated healthcare setup for stress and concentration diagnostics.",
        "holdings": [
            {"symbol": "LLY", "weight_pct": 25.0, "bucket": "core"},
            {"symbol": "NVO", "weight_pct": 15.0, "bucket": "core"},
            {"symbol": "JNJ", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "PFE", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "XBI", "weight_pct": 10.0, "bucket": "satellite"},
            {"symbol": "XLV", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "SGOV", "weight_pct": 10.0, "bucket": "core"},
        ],
        "expectations": {
            "largest_position_min_pct": 20.0,
            "policy_breaches_min": 1,
            "cma_available": True,
        },
    },
    "europe_tilt": {
        "title": "Europe Tilt",
        "description": "Europe-heavy setup for region and benchmark-relative diagnostics.",
        "holdings": [
            {"symbol": "ASML", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "SAP", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "SHEL", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "NVO", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "SAN", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "EFA", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "SPY", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "SGOV", "weight_pct": 10.0, "bucket": "core"},
        ],
        "expectations": {
            "cma_available": True,
            "attribution_available": True,
        },
    },
    "cash_heavy_tactical": {
        "title": "Cash-Heavy Tactical",
        "description": "High-liquidity tactical posture used for policy and readiness checks.",
        "holdings": [
            {"symbol": "SPY", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "QQQ", "weight_pct": 10.0, "bucket": "satellite"},
            {"symbol": "BND", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "SGOV", "weight_pct": 35.0, "bucket": "core"},
            {"symbol": "CASH", "weight_pct": 15.0, "bucket": "core"},
        ],
        "expectations": {
            "policy_breaches_max": 0,
            "rebalance_trades_max": 2,
            "cma_available": True,
        },
    },
    "allocation_drift_case": {
        "title": "Allocation Drift Case",
        "description": "Intentionally drifted portfolio expected to trigger a non-empty rebalance proposal.",
        "holdings": [
            {"symbol": "NVDA", "weight_pct": 18.0, "bucket": "core"},
            {"symbol": "MSFT", "weight_pct": 17.0, "bucket": "core"},
            {"symbol": "AAPL", "weight_pct": 15.0, "bucket": "core"},
            {"symbol": "AMZN", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "GOOGL", "weight_pct": 8.0, "bucket": "core"},
            {"symbol": "META", "weight_pct": 7.0, "bucket": "core"},
            {"symbol": "BND", "weight_pct": 8.0, "bucket": "core"},
            {"symbol": "SGOV", "weight_pct": 5.0, "bucket": "core"},
            {"symbol": "GLD", "weight_pct": 4.0, "bucket": "satellite"},
            {"symbol": "LQD", "weight_pct": 8.0, "bucket": "satellite"},
        ],
        "allocation": [
            {"asset_class": "equities", "role": "growth", "target_weight_pct": 50.0, "min_weight_pct": 45.0, "max_weight_pct": 55.0},
            {"asset_class": "high_quality_bonds", "role": "income", "target_weight_pct": 20.0, "min_weight_pct": 15.0, "max_weight_pct": 25.0},
            {"asset_class": "credit", "role": "income", "target_weight_pct": 10.0, "min_weight_pct": 5.0, "max_weight_pct": 15.0},
            {"asset_class": "alternatives", "role": "diversifier", "target_weight_pct": 5.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
            {"asset_class": "real_assets", "role": "hedge", "target_weight_pct": 5.0, "min_weight_pct": 0.0, "max_weight_pct": 10.0},
            {"asset_class": "gold", "role": "hedge", "target_weight_pct": 5.0, "min_weight_pct": 2.0, "max_weight_pct": 8.0},
            {"asset_class": "cash_liquidity", "role": "liquidity", "target_weight_pct": 5.0, "min_weight_pct": 2.0, "max_weight_pct": 10.0},
        ],
        "expectations": {
            "rebalance_trades_min": 1,
            "policy_breaches_min": 1,
            "cma_available": True,
        },
    },
    "single_name_breach_case": {
        "title": "Single-Name Breach Case",
        "description": "Single-name concentration breach against policy cap.",
        "holdings": [
            {"symbol": "NVDA", "weight_pct": 35.0, "bucket": "core"},
            {"symbol": "MSFT", "weight_pct": 15.0, "bucket": "core"},
            {"symbol": "AAPL", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "AMZN", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "SPY", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "BND", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "SGOV", "weight_pct": 10.0, "bucket": "core"},
        ],
        "policy": {
            "single_name_limit_percent": 12.0,
            "max_equity_percent": 80.0,
            "min_liquid_assets_percent": 5.0,
        },
        "expectations": {
            "policy_breaches_min": 1,
            "largest_position_min_pct": 30.0,
        },
    },
    "policy_incomplete_case": {
        "title": "Policy Incomplete Case",
        "description": "Missing policy and incomplete allocation inputs to verify readiness and unavailable states.",
        "holdings": [
            {"symbol": "SPY", "weight_pct": 50.0, "bucket": "core"},
            {"symbol": "BND", "weight_pct": 30.0, "bucket": "core"},
            {"symbol": "CASH", "weight_pct": 20.0, "bucket": "core"},
        ],
        "policy": {},
        "allocation": [
            {"asset_class": "equities", "role": "growth", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "high_quality_bonds", "role": "income", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "credit", "role": "income", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "alternatives", "role": "diversifier", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "real_assets", "role": "hedge", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "gold", "role": "hedge", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
            {"asset_class": "cash_liquidity", "role": "liquidity", "target_weight_pct": None, "min_weight_pct": None, "max_weight_pct": None},
        ],
        "cma_entries": [],
        "cma_correlations": [],
        "expectations": {
            "policy_configured": False,
            "allocation_configured": False,
            "cma_available": False,
            "attribution_available": False,
        },
    },
    "benchmark_mismatch_case": {
        "title": "Benchmark Mismatch Case",
        "description": "Europe and defensives mix against US tech benchmark to stress active metrics.",
        "holdings": [
            {"symbol": "SHEL", "weight_pct": 12.0, "bucket": "core"},
            {"symbol": "SAN", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "ASML", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "NVO", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "BND", "weight_pct": 20.0, "bucket": "core"},
            {"symbol": "VGIT", "weight_pct": 10.0, "bucket": "core"},
            {"symbol": "GLD", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "VNQ", "weight_pct": 8.0, "bucket": "satellite"},
            {"symbol": "SGOV", "weight_pct": 14.0, "bucket": "core"},
        ],
        "benchmark": {
            "benchmark_type": "market_index",
            "name": "Nasdaq 100",
            "base_symbol": "QQQ",
            "components": [],
            "notes": "Intentional mismatch test",
        },
        "expectations": {
            "cma_available": True,
            "attribution_available": True,
        },
    },
}


def _with_defaults(preset: dict[str, Any]) -> dict[str, Any]:
    resolved = deepcopy(preset)
    resolved["policy"] = {**_BASE_POLICY, **resolved.get("policy", {})}
    resolved["allocation"] = deepcopy(resolved.get("allocation") or _BASE_ALLOCATION)
    resolved["benchmark"] = {
        "benchmark_type": "market_index",
        "name": "Global Equity Benchmark",
        "base_symbol": "ACWI",
        "components": [],
        "notes": "",
        **(resolved.get("benchmark") or {}),
    }
    resolved["cma_entries"] = deepcopy(
        resolved["cma_entries"] if "cma_entries" in resolved else _BASE_CMA
    )
    resolved["cma_correlations"] = deepcopy(
        resolved["cma_correlations"] if "cma_correlations" in resolved else _BASE_CORRELATIONS
    )
    resolved["rebalancing_config"] = {**_BASE_REBALANCING, **(resolved.get("rebalancing_config") or {})}
    resolved.setdefault("expectations", {})
    resolved["holdings"] = deepcopy(resolved.get("holdings") or [])
    return resolved


def list_preset_names() -> list[str]:
    return sorted(PRESETS.keys())


def list_preset_summaries() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for name in list_preset_names():
        preset = PRESETS[name]
        items.append(
            {
                "name": name,
                "title": str(preset.get("title") or name.replace("_", " ").title()),
                "description": str(preset.get("description") or ""),
            }
        )
    return items


def get_preset(name: str) -> dict[str, Any]:
    normalized = (name or "").strip().lower()
    if normalized not in PRESETS:
        options = ", ".join(list_preset_names())
        raise ValueError(f"Unknown preset '{name}'. Available presets: {options}")
    return _with_defaults(PRESETS[normalized])

