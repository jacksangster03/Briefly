"""Session-level materiality scoring for cadence-aware briefing sends."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.briefings import MorningBriefing


@dataclass(frozen=True)
class SessionMateriality:
    score: int
    reasons: list[str]
    decision: str


def compute_materiality(
    briefing: MorningBriefing,
    *,
    previous: dict[str, float] | None,
) -> SessionMateriality:
    prev = previous or {}
    score = 0
    reasons: list[str] = []

    def _add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{reason} ({points:+d})")

    current_quality = float(briefing.session_quality_score or 0.0)
    prev_quality = float(prev.get("session_quality", current_quality))
    if abs(current_quality - prev_quality) >= 0.25:
        _add(3, "Regime quality shifted")

    curr_vix = _quote_level(briefing, "VIX")
    prev_vix = float(prev.get("vix_level", curr_vix or 0.0))
    if curr_vix is not None:
        for threshold in (20.0, 25.0, 30.0):
            if prev_vix < threshold <= curr_vix:
                _add(3, f"VIX crossed {threshold:.0f}")
                break
        if abs(curr_vix - prev_vix) >= 1.2:
            _add(3, "VIX moved sharply")

    us_avg = _region_avg(briefing, ("S&P", "NASDAQ", "DOW", "RUSSELL"))
    prev_us_avg = float(prev.get("us_avg_pct", us_avg))
    if abs(us_avg - prev_us_avg) > 0.75:
        _add(2, "US benchmark move changed >0.75pt")

    curr_10y = _macro_level(briefing, "10Y")
    prev_10y = float(prev.get("us10y", curr_10y or 0.0))
    if curr_10y is not None and abs(curr_10y - prev_10y) >= 0.05:
        _add(2, "US 10Y moved >5bp")

    curr_wti = _quote_move(briefing, "WTI")
    prev_wti = float(prev.get("wti_pct", curr_wti))
    if abs(curr_wti - prev_wti) >= 2.0:
        _add(2, "WTI move changed >2%")
    curr_brent = _quote_move(briefing, "BRENT")
    prev_brent = float(prev.get("brent_pct", curr_brent))
    if abs(curr_brent - prev_brent) >= 2.0:
        _add(2, "Brent move changed >2%")

    eu_avg = _region_avg(briefing, ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
    asia_avg = _region_avg(briefing, ("NIKKEI", "HANG SENG", "HSI"))
    prev_eu = float(prev.get("eu_avg_pct", eu_avg))
    prev_asia = float(prev.get("asia_avg_pct", asia_avg))
    if _sign(us_avg) != _sign(prev_us_avg) or _sign(eu_avg) != _sign(prev_eu) or _sign(asia_avg) != _sign(prev_asia):
        _add(2, "Regional skew changed")

    curr_pnl = _portfolio_total_contribution(briefing)
    prev_pnl = float(prev.get("portfolio_contrib_pct", curr_pnl))
    if abs(curr_pnl - prev_pnl) >= 0.30:
        _add(2, "Portfolio contribution shifted >0.30%")

    if _max_abs_quote_move(briefing.watchlist_quotes) >= 3.0 or _max_abs_quote_move(briefing.portfolio_quotes) >= 3.0:
        _add(2, "Watchlist/holding moved >3%")

    if _has_tier1_macro_geo(briefing):
        _add(2, "Tier-1 macro/geo headline")

    if _has_watchlist_earnings_shock(briefing):
        _add(3, "Watchlist earnings/guidance shock")

    if score == 0:
        _add(-3, "No meaningful change")

    if score >= 8:
        decision = "breaking_alert"
    elif score >= 5:
        decision = "send_session"
    elif score >= 3:
        decision = "hold_for_next_session"
    else:
        decision = "suppress"
    return SessionMateriality(score=score, reasons=reasons, decision=decision)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _quote_level(briefing: MorningBriefing, token: str) -> float | None:
    wanted = token.upper()
    for q in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
        text = f"{q.display_name} {q.symbol}".upper()
        if wanted in text:
            return float(q.current_price or 0.0)
    return None


def _quote_move(briefing: MorningBriefing, token: str) -> float:
    wanted = token.upper()
    for q in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
        text = f"{q.display_name} {q.symbol}".upper()
        if wanted in text:
            return float(q.change_percent or 0.0)
    return 0.0


def _macro_level(briefing: MorningBriefing, token: str) -> float | None:
    wanted = token.upper()
    for point in briefing.macro_context:
        text = f"{point.name} {point.series_id}".upper()
        if wanted in text:
            return float(point.value)
    return None


def _region_avg(briefing: MorningBriefing, keys: tuple[str, ...]) -> float:
    values = [
        float(q.change_percent or 0.0)
        for q in briefing.market_setup.index_quotes
        if any(key in (q.display_name or q.symbol or "").upper() for key in keys)
    ]
    return sum(values) / len(values) if values else 0.0


def _portfolio_total_contribution(briefing: MorningBriefing) -> float:
    charts = {str(row.get("chart_key")): row for row in (briefing.morning_chart_bundle or {}).get("charts", [])}
    pnl = charts.get("pnl_attribution_waterfall") or {}
    return float((pnl.get("meta") or {}).get("total_contribution") or 0.0)


def _max_abs_quote_move(quotes: list) -> float:
    if not quotes:
        return 0.0
    return max(abs(float(getattr(q, "change_percent", 0.0) or 0.0)) for q in quotes)


def _has_tier1_macro_geo(briefing: MorningBriefing) -> bool:
    tier1_sources = ("reuters", "associated press", "ap", "bloomberg", "financial times", "ft", "wsj")
    geo_terms = ("hormuz", "iran", "israel", "ceasefire", "sanction", "tariff", "opec", "central bank", "fed", "ecb")
    for evt in briefing.global_news[:8]:
        source = str(evt.raw_data.get("source_name", evt.source or "")).lower()
        text = f"{evt.title} {evt.summary}".lower()
        if any(src in source for src in tier1_sources) and any(term in text for term in geo_terms):
            return True
    return False


def _has_watchlist_earnings_shock(briefing: MorningBriefing) -> bool:
    for evt in briefing.watchlist_events[:10]:
        text = f"{evt.title} {evt.summary}".lower()
        if evt.event_type in {"earnings", "guidance"} and any(tok in text for tok in ("guidance", "miss", "beat", "cuts outlook", "raises outlook")):
            return True
    return False
