"""Deterministic macro policy portfolio lens (Phase 1 heuristics)."""

from __future__ import annotations

from typing import Any

from app.personalization.user_profile import UserProfile


_GROWTH_TICKERS = {"QQQ", "NVDA", "MSFT", "AAPL", "AMD", "META", "GOOGL", "TSLA"}
_DURATION_BOND_TICKERS = {"TLT", "IEF", "BND", "AGG", "LQD", "VGIT", "VGLT"}
_BANK_FIN_TICKERS = {"XLF", "JPM", "BAC", "C", "WFC", "GS", "MS"}
_USD_FX_TICKERS = {"UUP", "DXY", "USDU", "FXE", "FXY", "FXB"}
_GOLD_COMMODITY_TICKERS = {"GLD", "IAU", "SLV", "GDX", "USO", "XLE", "UNG", "DBC"}


def build_macro_portfolio_lens(profile: UserProfile) -> dict[str, Any]:
    holdings = list(profile.portfolio_holdings or [])
    watch = {str(s).upper() for s in profile.all_watchlist_tickers}
    status = "ok" if holdings else "partial"
    exposure = {
        "growth_equities": _bucket("growth_equities", holdings, watch, _GROWTH_TICKERS),
        "duration_bonds": _bucket("duration_bonds", holdings, watch, _DURATION_BOND_TICKERS),
        "banks_financials": _bucket("banks_financials", holdings, watch, _BANK_FIN_TICKERS),
        "usd_fx": _bucket("usd_fx", holdings, watch, _USD_FX_TICKERS),
        "gold_commodities": _bucket("gold_commodities", holdings, watch, _GOLD_COMMODITY_TICKERS),
    }
    summary = _lens_summary(exposure)
    return {
        "status": status,
        "summary": summary,
        "buckets": exposure,
        "data_basis": "portfolio holdings + watchlist heuristic mapping",
    }


def _bucket(name: str, holdings, watch: set[str], symbols: set[str]) -> dict[str, Any]:
    held_weight = 0.0
    held_names: list[str] = []
    for row in holdings:
        sym = str(getattr(row, "symbol", "") or "").upper()
        if not sym or sym not in symbols:
            continue
        if getattr(row, "weight_pct", None) is not None:
            held_weight += float(row.weight_pct or 0.0)
        held_names.append(sym)
    held_names = sorted(set(held_names))
    watch_hits = sorted(sym for sym in watch if sym in symbols and sym not in held_names)
    intensity = _bucket_intensity(weight=held_weight, held_count=len(held_names), watch_count=len(watch_hits))
    return {
        "bucket": name,
        "intensity": intensity,
        "holding_weight_pct": round(held_weight, 2),
        "holdings": held_names[:8],
        "watchlist_hits": watch_hits[:8],
    }


def _bucket_intensity(*, weight: float, held_count: int, watch_count: int) -> str:
    if weight >= 15 or held_count >= 3:
        return "high"
    if weight >= 5 or held_count >= 1 or watch_count >= 2:
        return "medium"
    if watch_count >= 1:
        return "low"
    return "minimal"


def _lens_summary(exposure: dict[str, dict[str, Any]]) -> str:
    ranked = sorted(
        exposure.values(),
        key=lambda row: (
            {"high": 4, "medium": 3, "low": 2, "minimal": 1}.get(str(row.get("intensity")), 0),
            float(row.get("holding_weight_pct") or 0.0),
        ),
        reverse=True,
    )
    top = [row for row in ranked if str(row.get("intensity")) in {"high", "medium"}][:2]
    if not top:
        return "No dominant macro sleeve sensitivity detected; treat macro lens as broad-context only."
    parts = []
    for row in top:
        parts.append(f"{str(row.get('bucket')).replace('_', ' ')} ({row.get('intensity')})")
    return "Primary macro sensitivities: " + ", ".join(parts) + "."

