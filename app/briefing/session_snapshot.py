"""Persist and diff compact session snapshots for 'What Changed' context."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import MarketSnapshot
from app.db.session import get_session
from app.schemas.briefings import MorningBriefing

_METRIC_SYMBOLS = {
    "vix_level": "__VIX__",
    "wti_pct": "__WTI_PCT__",
    "brent_pct": "__BRENT_PCT__",
    "gold_pct": "__GOLD_PCT__",
    "us10y": "__US10Y__",
    "breadth_up_pct": "__BREADTH_UP_PCT__",
    "us_avg_pct": "__US_AVG_PCT__",
    "eu_avg_pct": "__EU_AVG_PCT__",
    "asia_avg_pct": "__ASIA_AVG_PCT__",
    "portfolio_contrib_pct": "__PORTF_CONTRIB_PCT__",
    "session_quality": "__SESSION_QUALITY__",
    "regime_code": "__REGIME_CODE__",
    "watchlist_leader_pct": "__WATCHLIST_LEADER_PCT__",
    "watchlist_laggard_pct": "__WATCHLIST_LAGGARD_PCT__",
}

_REGIME_CODES: dict[str, int] = {
    "severe stress": -2,
    "cautious": -1,
    "mixed": 0,
    "constructive": 1,
    "strong risk-on": 2,
}


def snapshot_metrics(briefing: MorningBriefing) -> dict[str, float]:
    def _quote_move(token: str) -> float:
        for q in briefing.market_setup.macro_quotes + briefing.market_setup.index_quotes:
            text = f"{q.display_name} {q.symbol}".upper()
            if token in text:
                return float(q.change_percent or 0.0)
        return 0.0

    def _quote_level(token: str) -> float:
        for q in briefing.market_setup.macro_quotes + briefing.market_setup.index_quotes:
            text = f"{q.display_name} {q.symbol}".upper()
            if token in text:
                return float(q.current_price or 0.0)
        return 0.0

    sector_rows = list(briefing.market_setup.market_breadth or [])
    sector_up = sum(1 for row in sector_rows if float(row.change_percent or 0.0) > 0.0)
    breadth_up_pct = (sector_up / len(sector_rows) * 100.0) if sector_rows else 0.0

    index_quotes = briefing.market_setup.index_quotes or []
    us = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("S&P", "NASDAQ", "DOW", "RUSSELL"))]
    eu = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))]
    asia = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("NIKKEI", "HANG SENG", "HSI"))]
    total_contrib = 0.0
    for asset in briefing.chart_assets:
        if asset.key == "pnl_attribution_waterfall":
            break
    bundle = briefing.morning_chart_bundle or {}
    charts = {str(row.get("chart_key")): row for row in (bundle.get("charts") or [])}
    pnl = charts.get("pnl_attribution_waterfall") or {}
    total_contrib = float(((pnl.get("meta") or {}).get("total_contribution") or 0.0))
    watchlist_moves = [float(q.change_percent or 0.0) for q in (briefing.watchlist_quotes or [])]
    leader = max(watchlist_moves) if watchlist_moves else 0.0
    laggard = min(watchlist_moves) if watchlist_moves else 0.0
    regime_label = str(briefing.session_quality_label or "").strip().lower()
    regime_code = float(_REGIME_CODES.get(regime_label, 0))

    return {
        "vix_level": _quote_level("VIX"),
        "wti_pct": _quote_move("WTI"),
        "brent_pct": _quote_move("BRENT"),
        "gold_pct": _quote_move("GOLD"),
        "us10y": _quote_level("10Y US TREASURY YIELD"),
        "breadth_up_pct": breadth_up_pct,
        "us_avg_pct": (sum(us) / len(us)) if us else 0.0,
        "eu_avg_pct": (sum(eu) / len(eu)) if eu else 0.0,
        "asia_avg_pct": (sum(asia) / len(asia)) if asia else 0.0,
        "portfolio_contrib_pct": total_contrib,
        "session_quality": float(briefing.session_quality_score or 0.0),
        "regime_code": regime_code,
        "watchlist_leader_pct": leader,
        "watchlist_laggard_pct": laggard,
    }


def load_previous_snapshot(*, profile_name: str, session_key: str, before: datetime | None = None) -> tuple[datetime | None, dict[str, float]]:
    cutoff = before or datetime.now(timezone.utc)
    comparable_keys = _comparable_session_keys(session_key)
    with get_session() as session:
        previous_ts = (
            session.query(MarketSnapshot.timestamp)
            .filter(
                MarketSnapshot.snapshot_type.in_([f"briefing:{key}" for key in comparable_keys]),
                MarketSnapshot.display_name == profile_name,
                MarketSnapshot.timestamp < cutoff,
            )
            .order_by(MarketSnapshot.timestamp.desc(), MarketSnapshot.id.desc())
            .limit(1)
            .scalar()
        )
        if previous_ts is None:
            return None, {}
        rows = (
            session.query(MarketSnapshot)
            .filter(
                MarketSnapshot.snapshot_type.in_([f"briefing:{key}" for key in comparable_keys]),
                MarketSnapshot.display_name == profile_name,
                MarketSnapshot.timestamp == previous_ts,
            )
            .all()
        )
    metrics: dict[str, float] = {}
    reverse_map = {v: k for k, v in _METRIC_SYMBOLS.items()}
    for row in rows:
        key = reverse_map.get(str(row.symbol))
        if key:
            metrics[key] = float(row.price or 0.0)
    return previous_ts, metrics


def persist_snapshot(*, profile_name: str, session_key: str, generated_at: datetime, metrics: dict[str, float]) -> None:
    with get_session() as session:
        for key, symbol in _METRIC_SYMBOLS.items():
            if key not in metrics:
                continue
            session.add(
                MarketSnapshot(
                    symbol=symbol,
                    display_name=profile_name,
                    price=float(metrics.get(key) or 0.0),
                    snapshot_type=f"briefing:{session_key}",
                    timestamp=generated_at,
                )
            )


def _comparable_session_keys(session_key: str) -> tuple[str, ...]:
    key = (session_key or "morning").strip().lower()
    if key == "morning":
        return ("closing_wrap", "morning")
    if key == "europe_midday":
        return ("morning", "europe_midday")
    if key == "us_pre_open":
        return ("europe_midday", "morning", "us_pre_open")
    if key == "us_intraday_risk":
        return ("us_pre_open", "europe_midday", "morning", "us_intraday_risk")
    if key == "into_close":
        return ("us_intraday_risk", "us_pre_open", "morning", "into_close")
    if key == "closing_wrap":
        return ("into_close", "us_intraday_risk", "us_pre_open", "morning", "closing_wrap")
    return (key, "into_close", "us_intraday_risk", "us_pre_open", "europe_midday", "morning", "closing_wrap")


def build_what_changed_lines(*, previous: dict[str, float], current: dict[str, float]) -> list[str]:
    if not previous:
        return ["No prior comparable snapshot available."]
    lines: list[str] = []

    prev_regime = int(round(float(previous.get("regime_code", 0.0))))
    cur_regime = int(round(float(current.get("regime_code", prev_regime))))
    if prev_regime != cur_regime:
        prev_label = next((name for name, code in _REGIME_CODES.items() if code == prev_regime), "mixed")
        cur_label = next((name for name, code in _REGIME_CODES.items() if code == cur_regime), "mixed")
        lines.append(f"Regime: {prev_label.title()} -> {cur_label.title()}")

    def _delta(name: str, key: str, suffix: str, scale: float = 1.0) -> None:
        cur = float(current.get(key, 0.0))
        prv = float(previous.get(key, 0.0))
        d = (cur - prv) * scale
        lines.append(f"{name}: {cur:.2f}{suffix} ({d:+.2f}{suffix})")

    _delta("VIX", "vix_level", "")
    _delta("WTI", "wti_pct", "%")
    _delta("Brent", "brent_pct", "%")
    _delta("Gold", "gold_pct", "%")
    _delta("US 10Y", "us10y", "%")
    _delta("Sector breadth up", "breadth_up_pct", "%")
    _delta("US avg", "us_avg_pct", "%")
    _delta("Europe avg", "eu_avg_pct", "%")
    _delta("Asia avg", "asia_avg_pct", "%")
    _delta("Portfolio contribution", "portfolio_contrib_pct", "%")
    if "watchlist_leader_pct" in current or "watchlist_laggard_pct" in current:
        cur_leader = float(current.get("watchlist_leader_pct", 0.0))
        cur_laggard = float(current.get("watchlist_laggard_pct", 0.0))
        prv_leader = float(previous.get("watchlist_leader_pct", cur_leader))
        prv_laggard = float(previous.get("watchlist_laggard_pct", cur_laggard))
        lines.append(
            f"Watchlist leadership spread: {(cur_leader - cur_laggard):+.2f}pp "
            f"({(cur_leader - cur_laggard) - (prv_leader - prv_laggard):+.2f}pp)"
        )
    return lines[:8]
