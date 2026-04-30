"""Risk and benchmark analytics service for Phase 5.2."""

from __future__ import annotations

import datetime
import json
import math
from typing import Any

from app.db.models import BenchmarkPriceCache, RiskMetricsSnapshot
from app.db.session import get_session
from app.logger import get_logger
from app.risk.advanced_metrics import (
    classify_alpha,
    classify_beta,
    classify_r_squared,
    compute_advanced_metrics,
)

logger = get_logger("risk")

_LOOKBACK_TO_PERIOD = {
    63: "3mo",
    126: "6mo",
    252: "1y",
    504: "2y",
    756: "max",
}

_LOOKBACK_TO_LABEL = {
    63: "3 months",
    126: "6 months",
    252: "1 year",
    504: "2 years",
    756: "3 years",
}

_MIN_DATA_POINTS = 20


def _lookback_label(lookback_days: int) -> str:
    return _LOOKBACK_TO_LABEL.get(lookback_days, f"{lookback_days} days")


def _yf_period(lookback_days: int) -> str:
    return _LOOKBACK_TO_PERIOD.get(lookback_days, "1y")


def _unavailable_block(
    lookback_days: int,
    error: str,
    benchmark_name: str = "",
    benchmark_symbol: str = "",
) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "lookback_days": lookback_days,
        "lookback_label": _lookback_label(lookback_days),
        "computed_at": "",
        "data_completeness_pct": 0.0,
        "benchmark_name": benchmark_name,
        "benchmark_symbol": benchmark_symbol,
        "total_return_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "active_return_pct": 0.0,
        "total_return_display": "",
        "benchmark_return_display": "",
        "active_return_display": "",
        "volatility_pct": 0.0,
        "benchmark_volatility_pct": 0.0,
        "volatility_display": "",
        "benchmark_volatility_display": "",
        "max_drawdown_pct": 0.0,
        "benchmark_max_drawdown_pct": 0.0,
        "max_drawdown_display": "",
        "benchmark_max_drawdown_display": "",
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "tracking_error_pct": 0.0,
        "information_ratio": 0.0,
        "sharpe_display": "",
        "sortino_display": "",
        "tracking_error_display": "",
        "information_ratio_display": "",
        "risk_free_rate_pct": 0.0,
        "sharpe_label": "",
        "ir_label": "",
        "risk_status": "",
        "risk_summary": "",
        "rolling": {"30d": [], "90d": []},
    }


def _fmt_pct(value: float, digits: int = 2) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def _fmt_ratio(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _cumprod(returns: list[float]) -> list[float]:
    result = []
    running = 1.0
    for r in returns:
        running *= (1 + r)
        result.append(running)
    return result


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(variance)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _fetch_prices(
    symbols: list[str],
    lookback_days: int,
) -> dict[str, dict[datetime.date, float]]:
    """Fetch daily close prices from yfinance, using BenchmarkPriceCache first."""
    period = _yf_period(lookback_days)
    result: dict[str, dict[datetime.date, float]] = {}

    with get_session() as session:
        for symbol in symbols:
            cached_rows = (
                session.query(BenchmarkPriceCache)
                .filter(BenchmarkPriceCache.symbol == symbol)
                .order_by(BenchmarkPriceCache.date.asc())
                .all()
            )
            if cached_rows:
                result[symbol] = {row.date: row.close_price for row in cached_rows}

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; cannot fetch price history")
        return result

    cutoff = datetime.date.today() - datetime.timedelta(days=lookback_days + 10)
    for symbol in symbols:
        existing = result.get(symbol, {})
        latest_cached = max(existing.keys()) if existing else None
        if latest_cached and latest_cached >= datetime.date.today() - datetime.timedelta(days=2):
            continue
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval="1d", auto_adjust=True)
            if hist is None or hist.empty:
                continue
            prices: dict[datetime.date, float] = {}
            for idx, row in hist.iterrows():
                ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                dt = ts.date() if hasattr(ts, "date") else ts
                close = float(row.get("Close", 0.0) or 0.0)
                if close > 0 and dt >= cutoff:
                    prices[dt] = close

            if prices:
                result[symbol] = prices
                with get_session() as session:
                    for dt, close in prices.items():
                        existing_row = (
                            session.query(BenchmarkPriceCache)
                            .filter(
                                BenchmarkPriceCache.symbol == symbol,
                                BenchmarkPriceCache.date == dt,
                            )
                            .first()
                        )
                        if existing_row is None:
                            session.add(BenchmarkPriceCache(symbol=symbol, date=dt, close_price=close))
        except Exception as exc:
            logger.warning("Failed to fetch price history for %s: %s", symbol, exc)

    return result


def _build_portfolio_returns(
    prices: dict[str, dict[datetime.date, float]],
    weights: dict[str, float],
    benchmark_symbol: str,
) -> tuple[list[datetime.date], list[float], list[float], float]:
    """Build aligned daily returns for portfolio and benchmark.

    Returns (dates, port_rets, bench_rets, completeness_pct).
    """
    bench_prices = prices.get(benchmark_symbol, {})
    if not bench_prices:
        return [], [], [], 0.0

    holding_symbols = [s for s in weights if s != benchmark_symbol and s in prices]
    total_requested = len(weights)
    total_available = len(holding_symbols)
    completeness = (total_available / total_requested * 100.0) if total_requested > 0 else 0.0

    all_dates = sorted(bench_prices.keys())
    if len(all_dates) < 2:
        return [], [], [], 0.0

    total_weight = sum(weights.get(s, 0.0) for s in holding_symbols)
    if total_weight <= 0:
        return [], [], [], 0.0

    norm_weights = {s: weights[s] / total_weight for s in holding_symbols}

    dates: list[datetime.date] = []
    port_rets: list[float] = []
    bench_rets: list[float] = []

    for i in range(1, len(all_dates)):
        today = all_dates[i]
        yesterday = all_dates[i - 1]
        bp_t = bench_prices.get(today)
        bp_tm1 = bench_prices.get(yesterday)
        if bp_t is None or bp_tm1 is None or bp_tm1 == 0:
            continue
        bench_r = (bp_t - bp_tm1) / bp_tm1

        port_r = 0.0
        for sym in holding_symbols:
            sym_prices = prices[sym]
            p_t = sym_prices.get(today)
            p_tm1 = sym_prices.get(yesterday)
            if p_t is None or p_tm1 is None or p_tm1 == 0:
                continue
            sym_r = (p_t - p_tm1) / p_tm1
            port_r += norm_weights[sym] * sym_r

        dates.append(today)
        port_rets.append(port_r)
        bench_rets.append(bench_r)

    return dates, port_rets, bench_rets, completeness


def _compute_metrics(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
    risk_free_rate_pct: float,
) -> dict[str, float]:
    """Compute all scalar risk metrics from daily return series."""
    n = len(portfolio_returns)
    if n < 2:
        return {}

    rf_daily = (1 + risk_free_rate_pct / 100) ** (1 / 252) - 1

    excess = [r - rf_daily for r in portfolio_returns]
    excess_mean = _mean(excess)
    excess_std = _std(excess)
    sharpe = (excess_mean / excess_std * math.sqrt(252)) if excess_std > 0 else float("nan")

    downside = [r for r in portfolio_returns if r < rf_daily]
    if downside:
        downside_std = math.sqrt(_mean([d ** 2 for d in downside])) * math.sqrt(252)
    else:
        downside_std = float("nan")
    annual_port = ((1 + _mean(portfolio_returns)) ** 252 - 1) * 100
    if not math.isnan(downside_std) and downside_std > 0:
        sortino = (annual_port - risk_free_rate_pct) / (downside_std * 100)
    else:
        sortino = float("nan")

    def _max_drawdown(rets: list[float]) -> float:
        cum = _cumprod(rets)
        running_max = cum[0]
        max_dd = 0.0
        for c in cum:
            running_max = max(running_max, c)
            dd = (c - running_max) / running_max
            max_dd = min(max_dd, dd)
        return max_dd * 100

    port_dd = _max_drawdown(portfolio_returns)
    bench_dd = _max_drawdown(benchmark_returns)

    vol_pct = _std(portfolio_returns) * math.sqrt(252) * 100
    bench_vol_pct = _std(benchmark_returns) * math.sqrt(252) * 100

    total_return_pct = (_cumprod(portfolio_returns)[-1] - 1) * 100
    bench_total_return_pct = (_cumprod(benchmark_returns)[-1] - 1) * 100

    active_daily = [p - b for p, b in zip(portfolio_returns, benchmark_returns)]
    te_pct = _std(active_daily) * math.sqrt(252) * 100

    annual_bench = ((1 + _mean(benchmark_returns)) ** 252 - 1) * 100
    active_return_annual = annual_port - annual_bench
    ir = (active_return_annual / te_pct) if te_pct > 0 else float("nan")

    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": port_dd,
        "benchmark_max_drawdown_pct": bench_dd,
        "volatility_pct": vol_pct,
        "benchmark_volatility_pct": bench_vol_pct,
        "total_return_pct": total_return_pct,
        "benchmark_return_pct": bench_total_return_pct,
        "active_return_pct": active_return_annual,
        "tracking_error_pct": te_pct,
        "information_ratio": ir,
        "annual_port_return": annual_port,
    }


def _rolling_returns(
    dates: list[datetime.date],
    port_rets: list[float],
    bench_rets: list[float],
    windows: list[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if windows is None:
        windows = [30, 90]
    result: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        key = f"{window}d"
        if len(dates) < window:
            result[key] = []
            continue
        slice_dates = dates[-window:]
        slice_port = port_rets[-window:]
        slice_bench = bench_rets[-window:]
        port_cum = _cumprod(slice_port)
        bench_cum = _cumprod(slice_bench)
        rows = []
        for i, dt in enumerate(slice_dates):
            rows.append({
                "date": dt.isoformat(),
                "portfolio_pct": round((port_cum[i] - 1) * 100, 4),
                "benchmark_pct": round((bench_cum[i] - 1) * 100, 4),
            })
        result[key] = rows
    return result


def _sharpe_label(sharpe: float) -> str:
    if math.isnan(sharpe):
        return "insufficient data"
    if sharpe >= 1.5:
        return "excellent"
    if sharpe >= 1.0:
        return "good"
    if sharpe >= 0.5:
        return "moderate"
    return "poor"


def _ir_label(ir: float) -> str:
    if math.isnan(ir):
        return "insufficient data"
    if ir >= 0.5:
        return "strong alpha"
    if ir >= 0.0:
        return "neutral"
    return "negative alpha"


def _risk_status(vol_pct: float, policy: dict | None) -> str:
    if not policy:
        return "low_risk"
    max_vol = policy.get("max_volatility_percent") or policy.get("max_volatility_pct")
    if max_vol is None:
        return "low_risk"
    try:
        max_vol = float(max_vol)
    except (TypeError, ValueError):
        return "low_risk"
    if vol_pct > max_vol:
        return "high_risk"
    if vol_pct > max_vol * 0.75:
        return "moderate"
    return "low_risk"


def _risk_summary(
    total_return_pct: float,
    vol_pct: float,
    sharpe: float,
    lookback_label: str,
) -> str:
    ret_text = f"+{total_return_pct:.1f}%" if total_return_pct > 0 else f"{total_return_pct:.1f}%"
    sharpe_text = _sharpe_label(sharpe) if not math.isnan(sharpe) else "unrated"
    return (
        f"Over the {lookback_label} lookback, the portfolio returned {ret_text} "
        f"with annualised volatility of {vol_pct:.1f}% and a {sharpe_text} Sharpe ratio."
    )


def _safe(value: float, digits: int = 4) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, digits)


def compute_risk_analytics(
    profile_name: str,
    holdings: list[dict],
    benchmark_config: dict | None,
    policy: dict | None,
    lookback_days: int = 252,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Main entry point. Returns risk_analytics block."""
    label = _lookback_label(lookback_days)

    if not force_refresh:
        cached = load_cached_risk_metrics(profile_name)
        if cached is not None:
            return cached

    if not benchmark_config:
        return _unavailable_block(lookback_days, "No benchmark configured.")

    benchmark_symbol = (benchmark_config.get("base_symbol") or "").strip().upper()
    benchmark_name = benchmark_config.get("name") or benchmark_symbol or "Benchmark"
    if not benchmark_symbol:
        return _unavailable_block(lookback_days, "Benchmark symbol not set.", benchmark_name, "")

    weighted_holdings = [h for h in holdings if h.get("weight_pct") is not None]
    if not weighted_holdings:
        return _unavailable_block(lookback_days, "No weighted holdings.", benchmark_name, benchmark_symbol)

    holding_symbols = list({str(h["symbol"]).upper() for h in weighted_holdings})
    all_symbols = holding_symbols + ([benchmark_symbol] if benchmark_symbol not in holding_symbols else [])

    prices = _fetch_prices(all_symbols, lookback_days)

    weights_raw = {}
    for h in weighted_holdings:
        sym = str(h["symbol"]).upper()
        weights_raw[sym] = float(h.get("weight_pct") or 0.0)

    dates, port_rets, bench_rets, completeness = _build_portfolio_returns(
        prices, weights_raw, benchmark_symbol
    )

    if len(dates) < _MIN_DATA_POINTS:
        return _unavailable_block(
            lookback_days,
            f"Insufficient price data: {len(dates)} days available, {_MIN_DATA_POINTS} required.",
            benchmark_name,
            benchmark_symbol,
        )

    risk_free_rate_pct = 4.5
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct)

    if not metrics:
        return _unavailable_block(lookback_days, "Could not compute metrics.", benchmark_name, benchmark_symbol)

    rolling = _rolling_returns(dates, port_rets, bench_rets)
    computed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    sharpe = metrics["sharpe_ratio"]
    ir = metrics["information_ratio"]
    vol_pct = metrics["volatility_pct"]
    total_ret = metrics["total_return_pct"]
    bench_ret = metrics["benchmark_return_pct"]
    active_ret = metrics["active_return_pct"]
    sortino = metrics["sortino_ratio"]
    te_pct = metrics["tracking_error_pct"]
    port_dd = metrics["max_drawdown_pct"]
    bench_dd = metrics["benchmark_max_drawdown_pct"]
    bench_vol = metrics["benchmark_volatility_pct"]

    block: dict[str, Any] = {
        "available": True,
        "error": None,
        "lookback_days": lookback_days,
        "lookback_label": label,
        "computed_at": computed_at,
        "data_completeness_pct": round(completeness, 1),
        "benchmark_name": benchmark_name,
        "benchmark_symbol": benchmark_symbol,
        "total_return_pct": _safe(total_ret),
        "benchmark_return_pct": _safe(bench_ret),
        "active_return_pct": _safe(active_ret),
        "total_return_display": _fmt_pct(total_ret) if not math.isnan(total_ret) else "",
        "benchmark_return_display": _fmt_pct(bench_ret) if not math.isnan(bench_ret) else "",
        "active_return_display": _fmt_pct(active_ret) if not math.isnan(active_ret) else "",
        "volatility_pct": _safe(vol_pct),
        "benchmark_volatility_pct": _safe(bench_vol),
        "volatility_display": f"{vol_pct:.2f}%" if not math.isnan(vol_pct) else "",
        "benchmark_volatility_display": f"{bench_vol:.2f}%" if not math.isnan(bench_vol) else "",
        "max_drawdown_pct": _safe(port_dd),
        "benchmark_max_drawdown_pct": _safe(bench_dd),
        "max_drawdown_display": _fmt_pct(port_dd) if not math.isnan(port_dd) else "",
        "benchmark_max_drawdown_display": _fmt_pct(bench_dd) if not math.isnan(bench_dd) else "",
        "sharpe_ratio": _safe(sharpe),
        "sortino_ratio": _safe(sortino),
        "tracking_error_pct": _safe(te_pct),
        "information_ratio": _safe(ir),
        "sharpe_display": _fmt_ratio(sharpe) if not math.isnan(sharpe) else "",
        "sortino_display": _fmt_ratio(sortino) if not math.isnan(sortino) else "",
        "tracking_error_display": f"{te_pct:.2f}%" if not math.isnan(te_pct) else "",
        "information_ratio_display": _fmt_ratio(ir) if not math.isnan(ir) else "",
        "risk_free_rate_pct": risk_free_rate_pct,
        "sharpe_label": _sharpe_label(sharpe),
        "ir_label": _ir_label(ir),
        "risk_status": _risk_status(vol_pct if not math.isnan(vol_pct) else 0.0, policy),
        "risk_summary": _risk_summary(
            total_ret if not math.isnan(total_ret) else 0.0,
            vol_pct if not math.isnan(vol_pct) else 0.0,
            sharpe,
            label,
        ),
        "rolling": rolling,
    }

    advanced = compute_advanced_metrics(port_rets, bench_rets, risk_free_rate_pct)
    if advanced.get("available"):
        disp = advanced.get("display", {})
        block["advanced_metrics"] = {
            "available": True,
            "n_observations": advanced["n_observations"],
            "n_months": advanced["n_months"],
            "bull_months": advanced["bull_months"],
            "bear_months": advanced["bear_months"],
            "avg_monthly_geom_pct": advanced["avg_monthly_geom_pct"],
            "avg_monthly_geom_display": disp["avg_monthly_geom"],
            "annualised_return_pct": advanced["annualised_return_pct"],
            "benchmark_annualised_return_pct": advanced["benchmark_annualised_return_pct"],
            "beta": advanced["beta"],
            "beta_display": disp["beta"],
            "beta_label": classify_beta(advanced["beta"]),
            "r_squared": advanced["r_squared"],
            "r_squared_display": disp["r_squared"],
            "r_squared_label": classify_r_squared(advanced["r_squared"]),
            "correlation": advanced["correlation"],
            "treynor_ratio": advanced["treynor_ratio"],
            "treynor_display": disp["treynor_ratio"],
            "jensens_alpha_pct": advanced["jensens_alpha_pct"],
            "jensens_alpha_display": disp["jensens_alpha"],
            "alpha_label": classify_alpha(advanced["jensens_alpha_pct"]),
            "capm_expected_return_pct": advanced["capm_expected_return_pct"],
            "capm_expected_return_display": disp["capm_expected_return"],
            "probability_of_loss_pct": advanced["probability_of_loss_pct"],
            "probability_of_loss_display": disp["probability_of_loss"],
            "average_loss_pct": advanced["average_loss_pct"],
            "average_loss_display": disp["average_loss"],
            "downside_risk_pct": advanced["downside_risk_pct"],
            "downside_risk_display": disp["downside_risk"],
            "probability_of_underperformance_pct": advanced["probability_of_underperformance_pct"],
            "probability_of_underperformance_display": disp["probability_of_underperformance"],
            "average_underperformance_pct": advanced["average_underperformance_pct"],
            "average_underperformance_display": disp["average_underperformance"],
            "probability_of_outperformance_pct": advanced["probability_of_outperformance_pct"],
            "probability_of_outperformance_display": disp["probability_of_outperformance"],
            "average_outperformance_pct": advanced["average_outperformance_pct"],
            "average_outperformance_display": disp["average_outperformance"],
            "average_bull_active_pct": advanced["average_bull_active_pct"],
            "average_bull_active_display": disp["average_bull_active"],
            "average_bear_active_pct": advanced["average_bear_active_pct"],
            "average_bear_active_display": disp["average_bear_active"],
        }
    else:
        block["advanced_metrics"] = {
            "available": False,
            "reason": advanced.get("reason", "Insufficient data."),
        }

    _persist_snapshot(
        profile_name=profile_name,
        block=block,
        lookback_days=lookback_days,
        rolling=rolling,
        risk_free_rate_pct=risk_free_rate_pct,
        data_completeness_pct=completeness,
    )

    return block


def _persist_snapshot(
    profile_name: str,
    block: dict[str, Any],
    lookback_days: int,
    rolling: dict,
    risk_free_rate_pct: float,
    data_completeness_pct: float,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    with get_session() as session:
        session.query(RiskMetricsSnapshot).filter(
            RiskMetricsSnapshot.profile_name == profile_name
        ).update({"active": False})

        snap = RiskMetricsSnapshot(
            profile_name=profile_name,
            computed_at=now,
            lookback_days=lookback_days,
            sharpe_ratio=block.get("sharpe_ratio"),
            sortino_ratio=block.get("sortino_ratio"),
            max_drawdown_pct=block.get("max_drawdown_pct"),
            benchmark_max_drawdown_pct=block.get("benchmark_max_drawdown_pct"),
            volatility_pct=block.get("volatility_pct"),
            benchmark_volatility_pct=block.get("benchmark_volatility_pct"),
            total_return_pct=block.get("total_return_pct"),
            benchmark_return_pct=block.get("benchmark_return_pct"),
            active_return_pct=block.get("active_return_pct"),
            tracking_error_pct=block.get("tracking_error_pct"),
            information_ratio=block.get("information_ratio"),
            risk_free_rate_pct=risk_free_rate_pct,
            data_completeness_pct=round(data_completeness_pct, 1),
            rolling_30d_json=json.dumps(rolling.get("30d", [])),
            rolling_90d_json=json.dumps(rolling.get("90d", [])),
            active=True,
        )
        session.add(snap)


def load_cached_risk_metrics(profile_name: str) -> dict | None:
    """Return today's active RiskMetricsSnapshot as a dict, or None."""
    today = datetime.date.today()
    with get_session() as session:
        row = (
            session.query(RiskMetricsSnapshot)
            .filter(
                RiskMetricsSnapshot.profile_name == profile_name,
                RiskMetricsSnapshot.active.is_(True),
            )
            .order_by(RiskMetricsSnapshot.computed_at.desc())
            .first()
        )
        if row is None:
            return None
        computed_date = row.computed_at.date() if row.computed_at else None
        if computed_date != today:
            return None

        rolling_30d = json.loads(row.rolling_30d_json or "[]")
        rolling_90d = json.loads(row.rolling_90d_json or "[]")

        sharpe = row.sharpe_ratio or 0.0
        ir = row.information_ratio or 0.0
        vol = row.volatility_pct or 0.0
        bench_vol = row.benchmark_volatility_pct or 0.0
        total_ret = row.total_return_pct or 0.0
        bench_ret = row.benchmark_return_pct or 0.0
        active_ret = row.active_return_pct or 0.0
        sortino = row.sortino_ratio or 0.0
        te = row.tracking_error_pct or 0.0
        port_dd = row.max_drawdown_pct or 0.0
        bench_dd = row.benchmark_max_drawdown_pct or 0.0
        rfr = row.risk_free_rate_pct or 4.5
        label = _lookback_label(row.lookback_days or 252)

        return {
            "available": True,
            "error": None,
            "lookback_days": row.lookback_days or 252,
            "lookback_label": label,
            "computed_at": row.computed_at.isoformat() if row.computed_at else "",
            "data_completeness_pct": row.data_completeness_pct or 0.0,
            "benchmark_name": "",
            "benchmark_symbol": "",
            "total_return_pct": total_ret,
            "benchmark_return_pct": bench_ret,
            "active_return_pct": active_ret,
            "total_return_display": _fmt_pct(total_ret),
            "benchmark_return_display": _fmt_pct(bench_ret),
            "active_return_display": _fmt_pct(active_ret),
            "volatility_pct": vol,
            "benchmark_volatility_pct": bench_vol,
            "volatility_display": f"{vol:.2f}%",
            "benchmark_volatility_display": f"{bench_vol:.2f}%",
            "max_drawdown_pct": port_dd,
            "benchmark_max_drawdown_pct": bench_dd,
            "max_drawdown_display": _fmt_pct(port_dd),
            "benchmark_max_drawdown_display": _fmt_pct(bench_dd),
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "tracking_error_pct": te,
            "information_ratio": ir,
            "sharpe_display": _fmt_ratio(sharpe),
            "sortino_display": _fmt_ratio(sortino),
            "tracking_error_display": f"{te:.2f}%",
            "information_ratio_display": _fmt_ratio(ir),
            "risk_free_rate_pct": rfr,
            "sharpe_label": _sharpe_label(sharpe),
            "ir_label": _ir_label(ir),
            "risk_status": "low_risk",
            "risk_summary": _risk_summary(total_ret, vol, sharpe, label),
            "rolling": {"30d": rolling_30d, "90d": rolling_90d},
        }


def invalidate_risk_cache(profile_name: str) -> None:
    """Set all RiskMetricsSnapshot.active=False and PortfolioReturnSeries.stale=True."""
    from app.db.models import PortfolioReturnSeries

    with get_session() as session:
        session.query(RiskMetricsSnapshot).filter(
            RiskMetricsSnapshot.profile_name == profile_name
        ).update({"active": False})
        session.query(PortfolioReturnSeries).filter(
            PortfolioReturnSeries.profile_name == profile_name
        ).update({"stale": True})
