"""Randomized fuzz validation for portfolio state robustness."""

from __future__ import annotations

import random
from typing import Any

from app.settings import Settings
from app.validation.presets import CANONICAL_ASSET_CLASSES, get_preset
from app.validation.runner import apply_preset_payload, validate_profile_state

_FUZZ_SYMBOLS = [
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "ASML", "PLTR", "JPM", "GS",
    "XOM", "SHEL", "LLY", "NVO", "LMT", "SHOP", "SPY", "QQQ", "ACWI", "EFA",
    "BND", "IEF", "VGIT", "LQD", "HYG", "VNQ", "GLD", "DBMF", "SGOV", "CASH",
]


def run_fuzz_validation(
    *,
    settings: Settings,
    profile_name: str,
    cases: int = 25,
    seed: int = 42,
) -> dict[str, Any]:
    rng = random.Random(seed)
    case_reports: list[dict[str, Any]] = []
    failed = 0

    for idx in range(max(1, int(cases))):
        preset = _build_fuzz_preset(rng, case_index=idx)
        name = f"fuzz_case_{idx + 1:03d}"
        apply_preset_payload(
            profile_name=profile_name,
            preset_name=name,
            preset=preset,
            include_risk_refresh=False,
        )
        report = validate_profile_state(
            settings=settings,
            profile_name=profile_name,
            preset_name=name,
            expectations={},
        )
        if report["status"] != "pass":
            failed += 1
        case_reports.append(
            {
                "case": name,
                "status": report["status"],
                "failed_invariants": len(report.get("failed_invariants") or []),
                "policy_breaches": int(report.get("summary", {}).get("policy_breaches") or 0),
                "rebalance_trades": int(report.get("summary", {}).get("rebalance_trades") or 0),
            }
        )

    return {
        "profile": profile_name,
        "seed": seed,
        "cases": case_reports,
        "summary": {
            "total_cases": len(case_reports),
            "failed_cases": failed,
            "passed_cases": len(case_reports) - failed,
            "pass_rate_pct": round(((len(case_reports) - failed) / len(case_reports) * 100.0), 1) if case_reports else 100.0,
        },
        "status": "pass" if failed == 0 else "fail",
    }


def _build_fuzz_preset(rng: random.Random, *, case_index: int) -> dict[str, Any]:
    base = get_preset("balanced_60_40")
    holding_count = rng.randint(8, 14)
    symbols = rng.sample(_FUZZ_SYMBOLS, holding_count)
    weights = _normalized_random_weights(rng, holding_count)
    buckets = ["core", "satellite", "watch"]

    holdings = []
    for symbol, weight in zip(symbols, weights):
        holdings.append(
            {
                "symbol": symbol,
                "weight_pct": round(weight, 2),
                "bucket": buckets[rng.randrange(len(buckets))],
            }
        )

    policy = dict(base["policy"])
    policy["single_name_limit_percent"] = round(rng.uniform(8.0, 22.0), 1)
    policy["max_equity_percent"] = round(rng.uniform(55.0, 90.0), 1)
    policy["min_liquid_assets_percent"] = round(rng.uniform(0.0, 20.0), 1)
    policy["target_return_percent"] = round(rng.uniform(4.0, 11.0), 1)
    policy["max_volatility_percent"] = round(rng.uniform(10.0, 25.0), 1)
    policy["max_drawdown_percent"] = round(rng.uniform(15.0, 40.0), 1)

    cma_entries = []
    for ac in CANONICAL_ASSET_CLASSES:
        ret = rng.uniform(2.0, 11.0)
        vol = rng.uniform(1.0, 22.0)
        cma_entries.append(
            {
                "asset_class": ac,
                "expected_return_pct": round(ret, 2),
                "expected_volatility_pct": round(vol, 2),
                "notes": f"fuzz-{case_index + 1}",
            }
        )

    cma_corr = []
    for i in range(len(CANONICAL_ASSET_CLASSES)):
        for j in range(i + 1, len(CANONICAL_ASSET_CLASSES)):
            cma_corr.append(
                {
                    "asset_class_a": CANONICAL_ASSET_CLASSES[i],
                    "asset_class_b": CANONICAL_ASSET_CLASSES[j],
                    "correlation": round(rng.uniform(-0.25, 0.85), 2),
                }
            )

    return {
        **base,
        "holdings": holdings,
        "policy": policy,
        "cma_entries": cma_entries,
        "cma_correlations": cma_corr,
        "expectations": {},
    }


def _normalized_random_weights(rng: random.Random, n: int) -> list[float]:
    raw = [rng.random() for _ in range(n)]
    total = sum(raw) or 1.0
    weights = [value / total * 100.0 for value in raw]
    rounded = [round(value, 2) for value in weights]
    drift = round(100.0 - sum(rounded), 2)
    if rounded:
        rounded[0] = round(rounded[0] + drift, 2)
    return rounded

