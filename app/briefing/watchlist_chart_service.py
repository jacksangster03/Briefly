"""Deterministic watchlist chart specs for web explorer and static snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.data_sources.market_data import MarketDataService
from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.schemas.events import PricePoint
from app.settings import Settings

logger = get_logger("watchlist_chart_service")

_CACHE_LOCK = Lock()
_CACHE: dict[tuple, tuple[datetime, dict[str, Any]]] = {}

PERIOD_MAP: dict[str, tuple[str, str]] = {
    "1D": ("5d", "15m"),
    "5D": ("1mo", "1d"),
    "1M": ("3mo", "1d"),
    "3M": ("6mo", "1d"),
    "YTD": ("1y", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1d"),
    "MAX": ("max", "1d"),
}

MODE_SET = {"price", "rebased", "return", "relative"}
BENCHMARK_SET = {"none", "SPY", "QQQ", "ACWI", "XLK", "XLF", "XLE", "XLV", "XLI", "XLB", "XLRE", "XLU", "XLP", "XLY", "XLC"}


@dataclass
class _SeriesPayload:
    symbol: str
    name: str
    available: bool
    points: list[dict[str, Any]]
    warning: str | None = None


def build_watchlist_chart_spec(
    profile: UserProfile,
    period: str,
    mode: str,
    benchmark: str,
    symbols: list[str] | None,
    include_events: bool = False,
    *,
    market_data_service: MarketDataService | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    try:
        per = _normalize_period(period)
        mod = _normalize_mode(mode)
        bm = _normalize_benchmark(benchmark)
        resolved = _resolve_symbols(profile=profile, symbols=symbols, top_n=10)
        period_arg, interval_arg = PERIOD_MAP[per]
        key = (
            profile.name,
            tuple(resolved),
            per,
            period_arg,
            interval_arg,
            mod,
            bm,
            bool(include_events),
        )
        cached = _cache_get(key=key, now=now, ttl=_ttl_for_period(per))
        if cached is not None:
            return cached

        svc = market_data_service or MarketDataService(Settings())
        benchmark_history = _history_map(
            svc.get_price_history(bm, period=period_arg, interval=interval_arg) if bm != "none" else []
        )
        benchmark_first = _first_valid_value(benchmark_history)

        series_payloads: list[_SeriesPayload] = []
        warnings: list[str] = []
        for symbol in resolved:
            history = svc.get_price_history(symbol, period=period_arg, interval=interval_arg) or []
            hmap = _history_map(history)
            if not hmap:
                msg = f"{symbol}: insufficient price history."
                warnings.append(msg)
                series_payloads.append(_SeriesPayload(symbol=symbol, name=symbol, available=False, points=[], warning=msg))
                continue
            points = _build_points(
                history_map=hmap,
                mode=mod,
                benchmark_history=benchmark_history,
                benchmark_first=benchmark_first,
            )
            if not points:
                msg = f"{symbol}: insufficient aligned history."
                warnings.append(msg)
                series_payloads.append(_SeriesPayload(symbol=symbol, name=symbol, available=False, points=[], warning=msg))
                continue
            series_payloads.append(_SeriesPayload(symbol=symbol, name=symbol, available=True, points=points))

        available_series = [s for s in series_payloads if s.available and s.points]
        unavailable_count = len([s for s in series_payloads if not s.available])
        summary = _summary_block(available_series=available_series, benchmark=bm)
        summary["unavailable_symbols"] = unavailable_count
        freshness_label = _freshness_label(available_series=available_series, now=now)
        data_basis = {
            "label": freshness_label,
            "as_of": now.isoformat(),
            "available_count": len(available_series),
            "unavailable_count": unavailable_count,
            "notes": warnings[:8],
            "line": (
                f"Data basis: {freshness_label}, {len(available_series)}/{len(series_payloads)} symbols available, "
                "latest close used where intraday history unavailable."
            ),
        }
        payload = {
            "profile": profile.name,
            "controls": {
                "period": per,
                "mode": mod,
                "benchmark": bm,
                "symbols": resolved,
                "include_events": bool(include_events),
                "period_options": list(PERIOD_MAP.keys()),
                "mode_options": ["price", "rebased", "return", "relative"],
                "benchmark_options": sorted(BENCHMARK_SET),
            },
            "period": per,
            "mode": mod,
            "benchmark": bm,
            "symbols": resolved,
            "data_basis": data_basis,
            "series": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "available": item.available,
                    "points": item.points,
                    "warning": item.warning,
                }
                for item in series_payloads
            ],
            "summary": summary,
            "warnings": warnings[:8],
        }
        _cache_set(key=key, payload=payload, now=now)
        return payload
    except Exception as exc:
        logger.warning("watchlist chart spec failed: %s", _redact_exc(exc))
        return _unavailable_spec(profile=profile, period=period, mode=mode, benchmark=benchmark, reason="watchlist chart unavailable")


def build_watchlist_snapshot_spec(
    profile: UserProfile,
    period: str = "1M",
    mode: str = "rebased",
    top_n: int = 10,
    benchmark: str = "none",
    *,
    market_data_service: MarketDataService | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    symbols = _resolve_symbols(profile=profile, symbols=None, top_n=max(1, int(top_n)))
    payload = build_watchlist_chart_spec(
        profile=profile,
        period=period,
        mode=mode,
        benchmark=benchmark,
        symbols=symbols,
        include_events=False,
        market_data_service=market_data_service,
        generated_at=generated_at,
    )
    return {
        "chart_key": "watchlist_performance_snapshot",
        "variant": "watchlist_1m_rebased",
        "available": bool(payload.get("series")),
        "reason_if_hidden": None if payload.get("series") else "Watchlist Performance Snapshot unavailable: insufficient price history.",
        "title": "Watchlist Performance Snapshot",
        "caption": "1M rebased performance, top 10 watchlist names.",
        "series": payload.get("series") or [],
        "annotations": [],
        "meta": {
            "period": payload.get("period"),
            "mode": payload.get("mode"),
            "benchmark": payload.get("benchmark"),
            "data_basis": payload.get("data_basis", {}),
            "warnings": payload.get("warnings", []),
        },
        "email_dimensions": {"width": 1000, "height": 560},
    }


def _resolve_symbols(*, profile: UserProfile, symbols: list[str] | None, top_n: int) -> list[str]:
    if symbols:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            token = str(raw or "").strip().upper()
            if not token or token in seen:
                continue
            seen.add(token)
            cleaned.append(token)
        return cleaned[: max(1, top_n)]
    ordered = profile.watchlist_primary + profile.watchlist_secondary + profile.watchlist_monitor + profile.portfolio_symbols
    resolved: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        resolved.append(sym)
        if len(resolved) >= top_n:
            break
    return resolved


def _normalize_period(period: str) -> str:
    token = str(period or "").strip().upper()
    return token if token in PERIOD_MAP else "1M"


def _normalize_mode(mode: str) -> str:
    token = str(mode or "").strip().lower()
    return token if token in MODE_SET else "rebased"


def _normalize_benchmark(benchmark: str) -> str:
    token = str(benchmark or "").strip().upper()
    if token in {"", "NONE"}:
        return "none"
    return token if token in BENCHMARK_SET else "SPY"


def _history_map(points: list[PricePoint]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in points:
        if row.close is None:
            continue
        date_key = row.timestamp.date().isoformat()
        out[date_key] = float(row.close)
    return out


def _first_valid_value(history: dict[str, float]) -> float | None:
    if not history:
        return None
    for key in sorted(history.keys()):
        val = history.get(key)
        if val is not None and val > 0:
            return float(val)
    return None


def _build_points(
    *,
    history_map: dict[str, float],
    mode: str,
    benchmark_history: dict[str, float],
    benchmark_first: float | None,
) -> list[dict[str, Any]]:
    dates = sorted(history_map.keys())
    first = _first_valid_value(history_map)
    if first is None or first <= 0:
        return []
    points: list[dict[str, Any]] = []
    for date_key in dates:
        price = float(history_map[date_key])
        rebased = (price / first) * 100.0
        ret = ((price / first) - 1.0) * 100.0
        rel = None
        if benchmark_first is not None and date_key in benchmark_history and benchmark_first > 0:
            bench_ret = ((float(benchmark_history[date_key]) / benchmark_first) - 1.0) * 100.0
            rel = ret - bench_ret
        value = price
        if mode == "rebased":
            value = rebased
        elif mode == "return":
            value = ret
        elif mode == "relative":
            value = rel if rel is not None else 0.0
        points.append(
            {
                "date": date_key,
                "price": round(price, 4),
                "rebased_100": round(rebased, 4),
                "return_pct": round(ret, 4),
                "relative_vs_benchmark": round(float(rel), 4) if rel is not None else None,
                "value": round(float(value), 4),
            }
        )
    return points


def _summary_block(*, available_series: list[_SeriesPayload], benchmark: str) -> dict[str, Any]:
    if not available_series:
        return {
            "best_performer": None,
            "worst_performer": None,
            "dispersion": 0.0,
            "count_beating_benchmark": 0,
            "available_symbols": 0,
            "unavailable_symbols": 0,
        }
    perf_rows: list[tuple[str, float]] = []
    beating = 0
    for series in available_series:
        final = series.points[-1]
        perf = float(final.get("return_pct") or 0.0)
        perf_rows.append((series.symbol, perf))
        rel = final.get("relative_vs_benchmark")
        if benchmark != "none":
            if rel is not None and float(rel) > 0:
                beating += 1
        elif perf > 0:
            beating += 1
    perf_rows.sort(key=lambda row: row[1], reverse=True)
    best = perf_rows[0]
    worst = perf_rows[-1]
    dispersion = best[1] - worst[1]
    return {
        "best_performer": {"symbol": best[0], "return_pct": round(best[1], 4)},
        "worst_performer": {"symbol": worst[0], "return_pct": round(worst[1], 4)},
        "dispersion": round(dispersion, 4),
        "count_beating_benchmark": int(beating),
        "available_symbols": len(available_series),
        "unavailable_symbols": 0,
    }


def _freshness_label(*, available_series: list[_SeriesPayload], now: datetime) -> str:
    if not available_series:
        return "unavailable"
    latest_dates: list[datetime] = []
    for series in available_series:
        try:
            latest_dates.append(datetime.fromisoformat(series.points[-1]["date"]).replace(tzinfo=timezone.utc))
        except Exception:
            continue
    if not latest_dates:
        return "unavailable"
    latest = max(latest_dates)
    age = now - latest
    if age <= timedelta(minutes=15):
        return "near_real_time"
    if age <= timedelta(hours=8):
        return "live"
    if age <= timedelta(days=1, hours=8):
        return "prior_close"
    if age <= timedelta(days=3):
        return "stale"
    return "mixed"


def _ttl_for_period(period: str) -> int:
    if period == "1D":
        return 120
    if period == "5D":
        return 600
    return 3600


def _cache_get(*, key: tuple, now: datetime, ttl: int) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        ts, payload = hit
        if (now - ts).total_seconds() > ttl:
            _CACHE.pop(key, None)
            return None
        return payload


def _cache_set(*, key: tuple, payload: dict[str, Any], now: datetime) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (now, payload)


def _unavailable_spec(profile: UserProfile, period: str, mode: str, benchmark: str, reason: str) -> dict[str, Any]:
    return {
        "profile": profile.name,
        "controls": {"period": _normalize_period(period), "mode": _normalize_mode(mode), "benchmark": _normalize_benchmark(benchmark)},
        "period": _normalize_period(period),
        "mode": _normalize_mode(mode),
        "benchmark": _normalize_benchmark(benchmark),
        "symbols": [],
        "data_basis": {
            "label": "unavailable",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "available_count": 0,
            "unavailable_count": 0,
            "notes": [reason],
            "line": "Data basis: unavailable.",
        },
        "series": [],
        "summary": {
            "best_performer": None,
            "worst_performer": None,
            "dispersion": 0.0,
            "count_beating_benchmark": 0,
            "available_symbols": 0,
            "unavailable_symbols": 0,
        },
        "warnings": [reason],
    }


def _redact_exc(exc: Exception) -> str:
    text = str(exc)
    for token in ("api_key=", "apikey=", "token=", "access_key=", "key="):
        if token in text:
            head, _sep, tail = text.partition(token)
            text = f"{head}{token}***"
            if "&" in tail:
                text += tail[tail.find("&"):]
    return text[:240]
