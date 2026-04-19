"""Simulation lab orchestration service for Phase 5.8."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from app.allocation.service import _infer_asset_class
from app.logger import get_logger
from app.settings import Settings
from app.simulation.assumptions import (
    apply_macro_overrides,
    cma_parameters_for_symbols,
    estimate_historical_parameters,
)
from app.simulation.engines import (
    run_block_bootstrap_paths,
    run_filtered_historical_paths,
    run_historical_paths,
    run_monte_carlo_paths,
)
from app.simulation.metrics import build_sensitivity_payload, summarize_paths
from app.simulation.models import FREQUENCY_TO_ANNUAL_PERIODS, HORIZON_PRESETS, SIMULATION_COUNT_PRESETS, SimulationConfig
from app.simulation.repository import (
    create_simulation_run,
    get_simulation_preset,
    get_simulation_run,
    list_simulation_presets,
    list_simulation_runs,
    save_simulation_preset,
)
from app.simulation.scenarios import scenario_pack

logger = get_logger("simulation")


def simulation_method_options() -> list[dict[str, str]]:
    return [
        {"key": "historical", "label": "Historical Simulation"},
        {"key": "filtered_historical", "label": "Filtered Historical"},
        {"key": "monte_carlo", "label": "Monte Carlo"},
        {"key": "bootstrap", "label": "Block Bootstrap"},
    ]


def default_simulation_config(holdings: list[dict[str, Any]], benchmark_symbol: str = "ACWI") -> dict[str, Any]:
    return {
        "name": "",
        "mode": "portfolio",
        "methods": ["monte_carlo", "historical"],
        "frequency": "monthly",
        "horizon_periods": 60,
        "simulation_count": 2500,
        "assumption_source": "historical",
        "benchmark_symbol": benchmark_symbol,
        "start_value": 100.0,
        "holdings": [
            {
                "symbol": str(row.get("symbol") or "").upper(),
                "weight_pct": float(row.get("weight_pct") or 0.0),
            }
            for row in holdings
            if row.get("symbol")
        ],
        "macro_overrides": {
            "growth_shock": 0.0,
            "inflation_shock": 0.0,
            "rates_shock": 0.0,
            "volatility_regime": 0.0,
            "correlation_stress": 0.0,
        },
    }


def parse_simulation_config(
    *,
    payload: dict[str, Any],
    fallback_holdings: list[dict[str, Any]],
    fallback_benchmark_symbol: str,
) -> SimulationConfig:
    mode = str(payload.get("mode") or "portfolio").strip().lower()
    methods = [str(item).strip().lower() for item in (payload.get("methods") or ["monte_carlo"]) if str(item).strip()]
    if not methods:
        methods = ["monte_carlo"]
    frequency = str(payload.get("frequency") or "monthly").strip().lower()
    if frequency not in FREQUENCY_TO_ANNUAL_PERIODS:
        frequency = "monthly"

    horizon_preset = str(payload.get("horizon_preset") or "").strip().lower()
    if horizon_preset in HORIZON_PRESETS:
        horizon_periods, preset_frequency = HORIZON_PRESETS[horizon_preset]
        if str(payload.get("frequency") or "").strip() == "":
            frequency = preset_frequency
    else:
        horizon_periods = int(payload.get("horizon_periods") or 60)
    horizon_periods = max(1, min(horizon_periods, 600))

    sim_preset = str(payload.get("simulation_count_preset") or "").strip().lower()
    if sim_preset in SIMULATION_COUNT_PRESETS:
        simulation_count = SIMULATION_COUNT_PRESETS[sim_preset]
    else:
        simulation_count = int(payload.get("simulation_count") or 2500)
    simulation_count = max(100, min(simulation_count, 100000))

    assumption_source = str(payload.get("assumption_source") or "historical").strip().lower()
    if assumption_source not in {"historical", "cma", "manual"}:
        assumption_source = "historical"

    benchmark_symbol = str(payload.get("benchmark_symbol") or fallback_benchmark_symbol or "ACWI").strip().upper()

    raw_holdings = payload.get("holdings")
    holdings_source = raw_holdings if isinstance(raw_holdings, list) and raw_holdings else fallback_holdings
    holdings: list[dict[str, Any]] = []
    for row in holdings_source:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        weight_pct = _float_or_none(row.get("weight_pct"))
        if weight_pct is None:
            continue
        holdings.append({"symbol": symbol, "weight_pct": max(0.0, float(weight_pct))})
    if mode == "stock" and holdings:
        holdings = [max(holdings, key=lambda item: item["weight_pct"])]
    holdings = _normalize_holdings(holdings)

    macro_overrides = {
        "growth_shock": _float_or_none((payload.get("macro_overrides") or {}).get("growth_shock")) or _float_or_none(payload.get("growth_shock")) or 0.0,
        "inflation_shock": _float_or_none((payload.get("macro_overrides") or {}).get("inflation_shock")) or _float_or_none(payload.get("inflation_shock")) or 0.0,
        "rates_shock": _float_or_none((payload.get("macro_overrides") or {}).get("rates_shock")) or _float_or_none(payload.get("rates_shock")) or 0.0,
        "volatility_regime": _float_or_none((payload.get("macro_overrides") or {}).get("volatility_regime")) or _float_or_none(payload.get("volatility_regime")) or 0.0,
        "correlation_stress": _float_or_none((payload.get("macro_overrides") or {}).get("correlation_stress")) or _float_or_none(payload.get("correlation_stress")) or 0.0,
    }
    start_value = _float_or_none(payload.get("start_value")) or 100.0
    return SimulationConfig(
        mode=mode,
        methods=methods,
        frequency=frequency,
        horizon_periods=horizon_periods,
        simulation_count=simulation_count,
        assumption_source=assumption_source,
        benchmark_symbol=benchmark_symbol,
        holdings=holdings,
        macro_overrides=macro_overrides,
        start_value=max(1.0, start_value),
    )


def run_simulation(
    *,
    profile_name: str,
    settings: Settings,
    config: SimulationConfig,
    persist: bool = True,
    seed: int = 42,
) -> dict[str, Any]:
    symbols = [row["symbol"] for row in config.holdings]
    weights = np.array([row["weight_pct"] / 100.0 for row in config.holdings], dtype=float)
    symbol_to_asset_class = {symbol: _infer_asset_class(symbol) for symbol in symbols}

    lookback_days = max(400, config.horizon_periods * 6)
    returns_matrix, benchmark_returns = _fetch_returns_matrix(
        symbols=symbols,
        benchmark_symbol=config.benchmark_symbol,
        lookback_days=lookback_days,
        frequency=config.frequency,
    )

    mu_hist, cov_hist = estimate_historical_parameters(returns_matrix)
    if config.assumption_source == "cma":
        mu, cov = cma_parameters_for_symbols(
            profile_name=profile_name,
            symbols=symbols,
            frequency=config.frequency,
            symbol_to_asset_class=symbol_to_asset_class,
        )
        if mu.size == 0:
            mu, cov = mu_hist, cov_hist
    else:
        mu, cov = mu_hist, cov_hist

    if config.assumption_source in {"manual", "cma"}:
        mu, cov = apply_macro_overrides(mu=mu, cov=cov, macro_overrides=config.macro_overrides)

    method_payloads: dict[str, dict[str, Any]] = {}
    primary_paths: np.ndarray | None = None
    benchmark_paths: np.ndarray | None = None
    for idx, method in enumerate(config.methods):
        method_seed = seed + idx * 17
        paths = _run_method(
            method=method,
            returns_matrix=returns_matrix,
            weights=weights,
            mu=mu,
            cov=cov,
            horizon_periods=config.horizon_periods,
            simulation_count=config.simulation_count,
            seed=method_seed,
            volatility_regime=config.macro_overrides.get("volatility_regime", 0.0),
        )
        if primary_paths is None:
            primary_paths = paths
        summary, metrics, charts = summarize_paths(
            paths=paths,
            start_value=config.start_value,
            benchmark_paths=None,
        )
        method_payloads[method] = {
            "summary": summary,
            "metrics": metrics,
            "charts": charts,
        }

    if primary_paths is None:
        primary_paths = np.ones((config.simulation_count, config.horizon_periods + 1), dtype=float)

    if benchmark_returns.size > 0:
        benchmark_paths = _run_benchmark_paths(
            benchmark_returns=benchmark_returns,
            horizon_periods=config.horizon_periods,
            simulation_count=config.simulation_count,
            seed=seed + 101,
        )

    summary, metrics, charts = summarize_paths(
        paths=primary_paths,
        start_value=config.start_value,
        benchmark_paths=benchmark_paths,
    )

    scenarios = scenario_pack(
        weights_by_symbol={row["symbol"]: row["weight_pct"] / 100.0 for row in config.holdings},
        symbol_to_asset_class=symbol_to_asset_class,
    )
    sensitivity = _build_sensitivity(
        base_mu=mu,
        base_cov=cov,
        weights=weights,
        config=config,
        seed=seed + 220,
    )
    charts["sensitivity"] = build_sensitivity_payload(values=sensitivity)

    result = {
        "profile": profile_name,
        "config": asdict(config),
        "summary": summary,
        "metrics": metrics,
        "charts": charts,
        "methods": method_payloads,
        "scenarios": scenarios,
    }
    if persist:
        create_simulation_run(
            profile_name=profile_name,
            config=asdict(config),
            summary=summary,
            charts=charts,
            metrics=metrics,
            scenarios=scenarios,
            status="completed",
        )
    return result


def load_simulation_context(
    *,
    profile_name: str,
    fallback_holdings: list[dict[str, Any]],
    fallback_benchmark_symbol: str = "ACWI",
) -> dict[str, Any]:
    defaults = default_simulation_config(
        fallback_holdings,
        benchmark_symbol=fallback_benchmark_symbol,
    )
    runs = list_simulation_runs(profile_name, limit=12)
    presets = list_simulation_presets(profile_name)
    latest_run = get_simulation_run(profile_name, runs[0]["run_id"]) if runs else None
    return {
        "defaults": defaults,
        "method_options": simulation_method_options(),
        "runs": runs,
        "presets": presets,
        "latest_run": latest_run,
        "simulation_count_presets": SIMULATION_COUNT_PRESETS,
        "horizon_presets": HORIZON_PRESETS,
    }


def save_preset_from_config(
    *,
    profile_name: str,
    preset_name: str,
    description: str,
    config_payload: dict[str, Any],
) -> dict[str, Any]:
    return save_simulation_preset(
        profile_name=profile_name,
        preset_name=preset_name,
        description=description,
        config=config_payload,
    )


def load_preset_config(profile_name: str, preset_name: str) -> dict[str, Any] | None:
    row = get_simulation_preset(profile_name, preset_name)
    if not row:
        return None
    return row.get("config") if isinstance(row.get("config"), dict) else None


def fetch_run(profile_name: str, run_id: int) -> dict[str, Any] | None:
    return get_simulation_run(profile_name, run_id)


def _run_method(
    *,
    method: str,
    returns_matrix: np.ndarray,
    weights: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    seed: int,
    volatility_regime: float,
) -> np.ndarray:
    normalized = (method or "").strip().lower()
    if normalized == "historical":
        return run_historical_paths(
            returns_matrix=returns_matrix,
            weights=weights,
            horizon_periods=horizon_periods,
            simulation_count=simulation_count,
            seed=seed,
        )
    if normalized == "filtered_historical":
        return run_filtered_historical_paths(
            returns_matrix=returns_matrix,
            weights=weights,
            horizon_periods=horizon_periods,
            simulation_count=simulation_count,
            target_vol_scale=1.0 + float(volatility_regime) * 0.1,
            seed=seed,
        )
    if normalized == "bootstrap":
        return run_block_bootstrap_paths(
            returns_matrix=returns_matrix,
            weights=weights,
            horizon_periods=horizon_periods,
            simulation_count=simulation_count,
            block_size=5,
            seed=seed,
        )
    return run_monte_carlo_paths(
        mu=mu,
        cov=cov,
        weights=weights,
        horizon_periods=horizon_periods,
        simulation_count=simulation_count,
        seed=seed,
    )


def _run_benchmark_paths(
    *,
    benchmark_returns: np.ndarray,
    horizon_periods: int,
    simulation_count: int,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if benchmark_returns.size == 0:
        return np.ones((simulation_count, horizon_periods + 1), dtype=float)
    n_obs = benchmark_returns.shape[0]
    idx = rng.integers(0, n_obs, size=(simulation_count, horizon_periods))
    sampled = benchmark_returns[idx]
    paths = np.cumprod(1.0 + sampled, axis=1)
    return np.concatenate([np.ones((simulation_count, 1), dtype=float), paths], axis=1)


def _build_sensitivity(
    *,
    base_mu: np.ndarray,
    base_cov: np.ndarray,
    weights: np.ndarray,
    config: SimulationConfig,
    seed: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base_paths = run_monte_carlo_paths(
        mu=base_mu,
        cov=base_cov,
        weights=weights,
        horizon_periods=config.horizon_periods,
        simulation_count=min(3000, config.simulation_count),
        seed=seed,
    )
    base_median = float(np.median(base_paths[:, -1] * config.start_value))
    for label, growth_shift in [("Growth -2", -2.0), ("Growth -1", -1.0), ("Base", 0.0), ("Growth +1", 1.0), ("Growth +2", 2.0)]:
        mu = base_mu + growth_shift * 0.0015
        paths = run_monte_carlo_paths(
            mu=mu,
            cov=base_cov,
            weights=weights,
            horizon_periods=config.horizon_periods,
            simulation_count=min(2500, config.simulation_count),
            seed=seed + int((growth_shift + 3) * 7),
        )
        median = float(np.median(paths[:, -1] * config.start_value))
        delta = ((median / base_median) - 1.0) * 100.0 if base_median else 0.0
        rows.append(
            {
                "label": label,
                "median_terminal_value": median,
                "delta_pct": delta,
            }
        )
    return rows


def _fetch_returns_matrix(
    *,
    symbols: list[str],
    benchmark_symbol: str,
    lookback_days: int,
    frequency: str,
) -> tuple[np.ndarray, np.ndarray]:
    if not symbols:
        return np.zeros((0, 0), dtype=float), np.zeros((0,), dtype=float)
    step = {"daily": "1d", "weekly": "1wk", "monthly": "1mo"}.get(frequency, "1mo")
    try:
        import pandas as pd
        import yfinance as yf
    except Exception:
        logger.warning("yfinance/pandas unavailable; falling back to synthetic returns.")
        return _synthetic_returns(symbols), np.zeros((0,), dtype=float)

    all_symbols = list(dict.fromkeys(symbols + ([benchmark_symbol] if benchmark_symbol else [])))
    period = "max" if lookback_days > 750 else "3y"
    data = yf.download(
        tickers=all_symbols,
        period=period,
        interval=step,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
    )
    if data is None or len(data) == 0:
        return _synthetic_returns(symbols), np.zeros((0,), dtype=float)

    close_df = None
    if isinstance(data.columns, np.ndarray) or getattr(data.columns, "nlevels", 1) > 1:
        parts = []
        for symbol in all_symbols:
            if (symbol, "Close") in data.columns:
                series = data[(symbol, "Close")].rename(symbol)
                parts.append(series)
        if parts:
            close_df = pd.concat(parts, axis=1)
    elif "Close" in data.columns and len(all_symbols) == 1:
        close_df = data[["Close"]].rename(columns={"Close": all_symbols[0]})

    if close_df is None or close_df.empty:
        return _synthetic_returns(symbols), np.zeros((0,), dtype=float)

    close_df = close_df.dropna(how="all").tail(max(120, lookback_days))
    returns_df = close_df.pct_change().dropna(how="all")
    if returns_df.empty:
        return _synthetic_returns(symbols), np.zeros((0,), dtype=float)

    returns_df = returns_df.fillna(0.0)
    matrix = returns_df[[sym for sym in symbols if sym in returns_df.columns]].to_numpy(dtype=float)
    if matrix.size == 0:
        matrix = _synthetic_returns(symbols)
    benchmark_returns = (
        returns_df[benchmark_symbol].to_numpy(dtype=float)
        if benchmark_symbol in returns_df.columns
        else np.zeros((0,), dtype=float)
    )
    return matrix, benchmark_returns


def _synthetic_returns(symbols: list[str], obs: int = 600) -> np.ndarray:
    rng = np.random.default_rng(1234)
    n = max(1, len(symbols))
    base = rng.normal(loc=0.003 / 12.0, scale=0.05 / np.sqrt(12.0), size=(obs, n))
    return base


def _normalize_holdings(holdings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(row.get("weight_pct") or 0.0) for row in holdings)
    if total <= 0:
        if not holdings:
            return []
        eq = 100.0 / len(holdings)
        return [{"symbol": row["symbol"], "weight_pct": eq} for row in holdings]
    return [
        {
            "symbol": row["symbol"],
            "weight_pct": float(row.get("weight_pct") or 0.0) / total * 100.0,
        }
        for row in holdings
    ]


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

