"""QuantStats tearsheet generation for portfolio return series."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.benchmark.service import load_benchmark_config
from app.logger import get_logger
from app.portfolio.service import load_active_holdings
from app.risk.service import build_portfolio_returns, fetch_prices

logger = get_logger("tearsheet")


def build_tearsheet_html(profile_name: str, lookback_days: int = 252) -> str | None:
    """Build an inline HTML tearsheet for a profile. Returns None if unavailable."""
    series = _build_return_series(profile_name, lookback_days)
    if series is None:
        return None

    try:
        import pandas as pd
        import quantstats as qs
    except Exception:
        logger.warning("quantstats or pandas unavailable; tearsheet disabled")
        return None

    dates = series["dates"]
    port = series["portfolio_returns"]
    bench = series["benchmark_returns"]
    benchmark_symbol = series["benchmark_symbol"]
    title = f"{profile_name} vs {benchmark_symbol} ({lookback_days}d)"

    try:
        port_series = pd.Series(port, index=pd.to_datetime(dates), name="Portfolio")
        bench_series = pd.Series(bench, index=pd.to_datetime(dates), name=benchmark_symbol)
        html = qs.reports.html(
            returns=port_series,
            benchmark=bench_series,
            output=False,
            title=title,
        )
        return str(html) if html else None
    except Exception as exc:
        logger.warning("Failed to build tearsheet for %s: %s", profile_name, exc)
        return None


def _build_return_series(profile_name: str, lookback_days: int) -> dict[str, Any] | None:
    benchmark_cfg = load_benchmark_config(profile_name) or {}
    benchmark_symbol = str(benchmark_cfg.get("base_symbol") or "").upper()
    if not benchmark_symbol:
        return None

    holdings = [h for h in load_active_holdings(profile_name) if h.weight_pct is not None]
    if not holdings:
        return None

    weights = {str(h.symbol).upper(): float(h.weight_pct or 0.0) for h in holdings}
    symbols = list(weights.keys())
    if benchmark_symbol not in symbols:
        symbols.append(benchmark_symbol)

    prices = fetch_prices(symbols, lookback_days)
    dates, port_rets, bench_rets, _completeness = build_portfolio_returns(
        prices=prices,
        weights=weights,
        benchmark_symbol=benchmark_symbol,
    )
    if len(dates) < 20:
        return None

    return {
        "benchmark_symbol": benchmark_symbol,
        "dates": [d.isoformat() if isinstance(d, date) else str(d) for d in dates],
        "portfolio_returns": port_rets,
        "benchmark_returns": bench_rets,
    }
