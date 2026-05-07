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
    def _canon(key: str) -> dict | None:
        return dict((briefing.canonical_prices or {}).get(key) or {}) or None

    def _quote_move(token: str) -> float | None:
        canon_key = token.upper().strip()
        if canon_key in {"WTI", "BRENT", "GOLD"}:
            canon = _canon(canon_key)
            if canon:
                return float(canon.get("change_percent")) if canon.get("change_percent") is not None else None
        for q in briefing.market_setup.macro_quotes + briefing.market_setup.index_quotes:
            text = f"{q.display_name} {q.symbol}".upper()
            if token in text:
                if q.change_percent is None:
                    return None
                return float(q.change_percent)
        return None

    def _quote_level(token: str) -> float | None:
        canon_key = token.upper().strip()
        if canon_key in {"VIX", "US10Y", "US2Y"}:
            canon = _canon(canon_key)
            if canon and canon.get("value") is not None:
                return float(canon.get("value"))
        for q in briefing.market_setup.macro_quotes + briefing.market_setup.index_quotes:
            text = f"{q.display_name} {q.symbol}".upper()
            if token in text:
                if q.current_price is None:
                    return None
                return float(q.current_price)
        return None

    sector_rows = list(briefing.market_setup.market_breadth or [])
    sector_up = sum(1 for row in sector_rows if float(row.change_percent or 0.0) > 0.0)
    breadth_up_pct = (sector_up / len(sector_rows) * 100.0) if sector_rows else None

    index_quotes = briefing.market_setup.index_quotes or []
    us = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("S&P", "NASDAQ", "DOW", "RUSSELL"))]
    eu = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))]
    asia = [float(q.change_percent or 0.0) for q in index_quotes if any(k in (q.display_name or q.symbol or "").upper() for k in ("NIKKEI", "HANG SENG", "HSI"))]
    total_contrib = None
    for asset in briefing.chart_assets:
        if asset.key == "pnl_attribution_waterfall":
            break
    bundle = briefing.morning_chart_bundle or {}
    charts = {str(row.get("chart_key")): row for row in (bundle.get("charts") or [])}
    pnl = charts.get("pnl_attribution_waterfall") or {}
    total_meta = ((pnl.get("meta") or {}).get("total_contribution"))
    total_contrib = float(total_meta) if total_meta is not None else None
    watchlist_moves = [float(q.change_percent or 0.0) for q in (briefing.watchlist_quotes or [])]
    leader = max(watchlist_moves) if watchlist_moves else None
    laggard = min(watchlist_moves) if watchlist_moves else None
    regime_label = str(briefing.session_quality_label or "").strip().lower()
    regime_code = float(_REGIME_CODES.get(regime_label, 0))

    out: dict[str, float] = {
        "vix_level": _quote_level("VIX"),
        "wti_pct": _quote_move("WTI"),
        "brent_pct": _quote_move("BRENT"),
        "gold_pct": _quote_move("GOLD"),
        "us10y": _quote_level("US10Y") or _quote_level("10Y US TREASURY YIELD"),
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
    return {k: float(v) for k, v in out.items() if v is not None}


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
            value = metrics.get(key)
            if value is None:
                continue
            session.add(
                MarketSnapshot(
                    symbol=symbol,
                    display_name=profile_name,
                    price=float(value),
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

    magnitudes: list[float] = []

    def _delta(name: str, key: str, suffix: str, scale: float = 1.0, is_rate: bool = False) -> None:
        cur_raw = current.get(key)
        prv_raw = previous.get(key)
        if cur_raw is None and prv_raw is None:
            return
        if cur_raw is None:
            # Suppress unavailable noise for non-primary series.
            if key in {"vix_level", "wti_pct", "us10y", "breadth_up_pct", "us_avg_pct"}:
                lines.append(f"{name}: unavailable")
            return
        if prv_raw is None:
            cur = float(cur_raw)
            lines.append(f"{name}: {cur:.2f}{suffix} (newly available)")
            return
        cur = float(cur_raw)
        prv = float(prv_raw)
        d = (cur - prv) * scale
        if abs(d) < 0.005 and abs(cur) < 0.005 and abs(prv) < 0.005:
            # Avoid noisy fake precision lines such as +0.00% -> +0.00%.
            return

        if is_rate:
            bp = round(d * 100)
            abs_bp = abs(bp)
            if abs_bp < 1:
                qualifier = "flat"
            elif abs_bp < 3:
                qualifier = "little changed"
            else:
                qualifier = f"{bp:+d} bp"
            lines.append(f"{name}: {prv:.2f}% -> {cur:.2f}% ({qualifier})")
            magnitudes.append(abs_bp / 100)
            return

        abs_d = abs(d)
        if abs_d < 0.05:
            qualifier = "flat"
        elif abs_d < 0.25:
            qualifier = f"little changed ({d:+.2f}{suffix})"
        elif abs_d < 0.75:
            qualifier = f"{'higher' if d > 0 else 'lower'} ({d:+.2f}{suffix})"
        else:
            qualifier = f"{'sharply higher' if d > 0 else 'sharply lower'} ({d:+.2f}{suffix})"

        magnitudes.append(abs_d)
        lines.append(f"{name}: {prv:.2f}{suffix} -> {cur:.2f}{suffix} ({qualifier})")

    _delta("VIX", "vix_level", "")
    _delta("WTI", "wti_pct", "%")
    _delta("Brent", "brent_pct", "%")
    _delta("Gold", "gold_pct", "%")
    _delta("US 10Y", "us10y", "%", is_rate=True)
    _delta("Sector breadth up", "breadth_up_pct", "%")
    _delta("US avg", "us_avg_pct", "%")
    _delta("Europe avg", "eu_avg_pct", "%")
    # Asia often remains unchanged during Europe/US sessions; only include on material delta.
    before_lines = len(lines)
    _delta("Asia avg", "asia_avg_pct", "%")
    if len(lines) > before_lines:
        last = lines[-1]
        if "(flat)" in last or "(little changed" in last:
            lines.pop()
    _delta("Portfolio contribution", "portfolio_contrib_pct", "%")
    if "watchlist_leader_pct" in current and "watchlist_laggard_pct" in current:
        cur_leader = float(current.get("watchlist_leader_pct"))
        cur_laggard = float(current.get("watchlist_laggard_pct"))
        prv_leader = float(previous.get("watchlist_leader_pct", cur_leader))
        prv_laggard = float(previous.get("watchlist_laggard_pct", cur_laggard))
        spread_delta = (cur_leader - cur_laggard) - (prv_leader - prv_laggard)
        magnitudes.append(abs(spread_delta))
        lines.append(
            f"Watchlist leadership spread: {(cur_leader - cur_laggard):+.2f}pp "
            f"({spread_delta:+.2f}pp)"
        )
    if magnitudes and max(magnitudes) < 0.05:
        return ["Little changed since prior session: cross-asset signals stable."]
    return lines[:8]
