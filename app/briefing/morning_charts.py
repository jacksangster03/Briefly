"""Deterministic morning chart contract + selection engine.

Phase 6.4 introduces a data-first chart pipeline where chart decisions are
computed from deterministic rules, then rendered for web/email separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.events import MacroDataPoint, PricePoint, QuoteData


US_INDEX_KEYS = ("spx", "s&p 500", "nasdaq", "dow", "russell")
EU_INDEX_KEYS = ("stoxx", "ftse", "dax", "cac", "ibex")
ASIA_INDEX_KEYS = ("nikkei", "hang seng")

OIL_KEYS = ("wti", "crude", "cl1:com")
GOLD_KEYS = ("gold", "gc1:com")
VIX_KEYS = ("vix",)


@dataclass
class ChartCandidate:
    chart_key: str
    category: str
    priority: float
    spec: dict[str, Any]
    reason: str


def build_morning_chart_bundle(
    *,
    briefing: MorningBriefing,
    profile: UserProfile,
    market_data_service: Any,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build deterministic chart specs + selected chart roles."""
    normalized = _normalize_inputs(briefing.market_setup.index_quotes, briefing.market_setup.macro_quotes, briefing.macro_context)
    regime_tags = _derive_regime_tags(normalized)
    candidates = _build_candidates(
        briefing=briefing,
        profile=profile,
        market_data_service=market_data_service,
        normalized=normalized,
        regime_tags=regime_tags,
    )
    selected = _select_candidates(candidates)
    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime_tags": regime_tags,
        "charts": [item.spec for item in candidates],
        "selected": selected,
        "summary": _bundle_summary(normalized, regime_tags),
    }
    return bundle, selected


def selected_chart_specs(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    charts = {str(row.get("chart_key")): row for row in (bundle.get("charts") or [])}
    selected = bundle.get("selected") or []
    specs: list[dict[str, Any]] = []
    for row in selected:
        key = str(row.get("chart_key") or "")
        spec = charts.get(key)
        if spec:
            specs.append(spec)
    return specs


def chart_summary_lines(bundle: dict[str, Any], *, limit: int = 4) -> list[str]:
    charts = {str(row.get("chart_key")): row for row in (bundle.get("charts") or [])}
    lines: list[str] = []
    for selected in (bundle.get("selected") or [])[: max(1, limit)]:
        key = str(selected.get("chart_key") or "")
        role = str(selected.get("role") or "support")
        spec = charts.get(key, {})
        if not spec:
            continue
        title = str(spec.get("title") or key.replace("_", " ").title())
        available = bool(spec.get("available"))
        if available:
            lines.append(f"{role.title()}: {title} ({spec.get('variant', 'default')})")
        else:
            reason = str(spec.get("reason_if_hidden") or "unavailable")
            lines.append(f"{role.title()}: {title} unavailable ({reason})")
    return lines


def _normalize_inputs(
    index_quotes: list[QuoteData],
    macro_quotes: list[QuoteData],
    macro_context: list[MacroDataPoint],
) -> dict[str, Any]:
    us = _avg_change(index_quotes, US_INDEX_KEYS)
    eu = _avg_change(index_quotes, EU_INDEX_KEYS)
    asia = _avg_change(index_quotes, ASIA_INDEX_KEYS)
    positives = [quote for quote in index_quotes if float(quote.change_percent or 0.0) > 0]
    breadth = len(positives) / max(1, len(index_quotes))
    vix = _find_quote(index_quotes + macro_quotes, VIX_KEYS)
    oil = _find_quote(macro_quotes, OIL_KEYS)
    gold = _find_quote(macro_quotes, GOLD_KEYS)
    ten_y = _find_macro(macro_context, "10Y")
    two_y = _find_macro(macro_context, "2Y")
    spread = _find_macro(macro_context, "SPREAD")
    return {
        "us_avg": us,
        "eu_avg": eu,
        "asia_avg": asia,
        "breadth": breadth,
        "vix_level": float(vix.current_price) if vix else None,
        "vix_delta_pct": float(vix.change_percent) if vix else None,
        "oil_delta_pct": float(oil.change_percent) if oil else None,
        "gold_delta_pct": float(gold.change_percent) if gold else None,
        "ten_y_change": float(ten_y.change) if ten_y and ten_y.change is not None else None,
        "two_y_change": float(two_y.change) if two_y and two_y.change is not None else None,
        "curve_change": float(spread.change) if spread and spread.change is not None else None,
    }


def _derive_regime_tags(metrics: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    breadth = float(metrics.get("breadth") or 0.0)
    us = float(metrics.get("us_avg") or 0.0)
    eu = float(metrics.get("eu_avg") or 0.0)
    vix_level = metrics.get("vix_level")
    vix_delta = metrics.get("vix_delta_pct")
    oil_delta = metrics.get("oil_delta_pct")
    ten_y = metrics.get("ten_y_change")
    curve_change = metrics.get("curve_change")

    if breadth >= 0.62 and us > 0 and eu > -0.2:
        tags.append("risk_on")
    if (vix_level is not None and vix_level >= 19.0 and (vix_delta or 0.0) > 3.0) or (breadth < 0.45 and us < 0):
        tags.append("defensive")
    if ten_y is not None and abs(float(ten_y)) >= 0.025:
        tags.append("rates_led")
    if oil_delta is not None and abs(float(oil_delta)) >= 3.5:
        tags.append("oil_shock")
    if abs(us - eu) >= 0.9:
        tags.append("regional_split")
    if abs(us - eu) >= 0.7 or abs(us - float(metrics.get("asia_avg") or 0.0)) >= 0.7:
        tags.append("breadth_divergence")
    if not tags:
        tags.append("mixed")
    return tags


def _build_candidates(
    *,
    briefing: MorningBriefing,
    profile: UserProfile,
    market_data_service: Any,
    normalized: dict[str, Any],
    regime_tags: list[str],
) -> list[ChartCandidate]:
    specs: list[ChartCandidate] = []

    global_relative = _global_relative_spec(
        index_quotes=briefing.market_setup.index_quotes,
        market_data_service=market_data_service,
    )
    specs.append(
        ChartCandidate(
            chart_key="global_relative_performance",
            category="hero",
            priority=_priority_global_relative(normalized, regime_tags),
            spec=global_relative,
            reason="Cross-region leadership and path divergence.",
        )
    )

    impulse = _cross_asset_impulse_spec(
        macro_quotes=briefing.market_setup.macro_quotes,
        macro_context=briefing.macro_context,
        market_data_service=market_data_service,
    )
    specs.append(
        ChartCandidate(
            chart_key="cross_asset_impulse_strip",
            category="support",
            priority=_priority_impulse(normalized, regime_tags),
            spec=impulse,
            reason="Rates, FX, and commodities impulse context.",
        )
    )

    holdings = _holdings_excess_spec(
        holdings_quotes=briefing.portfolio_quotes or briefing.watchlist_quotes,
        benchmark_reference=float(normalized.get("us_avg") or 0.0),
    )
    specs.append(
        ChartCandidate(
            chart_key="holdings_excess_performance",
            category="portfolio",
            priority=0.73 + (0.1 if briefing.portfolio_quotes else 0.0),
            spec=holdings,
            reason="Portfolio-linked winners and laggards versus broad market.",
        )
    )

    sector = _sector_quadrant_spec(briefing, profile)
    specs.append(
        ChartCandidate(
            chart_key="sector_exposure_quadrant",
            category="support",
            priority=0.66,
            spec=sector,
            reason="Exposure-weight context for sector winners/losers.",
        )
    )

    event = _event_linked_spec(briefing, market_data_service)
    specs.append(
        ChartCandidate(
            chart_key="event_linked_annotated_trend",
            category="event",
            priority=0.62 + (0.08 if "oil_shock" in regime_tags else 0.0),
            spec=event,
            reason="Top event-linked symbol trend with deterministic marker.",
        )
    )

    for item in specs:
        item.spec["priority"] = round(float(item.priority), 4)
    specs.sort(key=lambda row: row.priority, reverse=True)
    return specs


def _select_candidates(candidates: list[ChartCandidate]) -> list[dict[str, str]]:
    available = [item for item in candidates if bool(item.spec.get("available"))]
    ordered = available or candidates
    if not ordered:
        return []
    selected: list[dict[str, str]] = []
    hero = ordered[0]
    selected.append(
        {
            "chart_key": hero.chart_key,
            "role": "hero",
            "reason": hero.reason,
        }
    )

    supports = [item for item in ordered[1:] if item.chart_key != hero.chart_key][:2]
    for item in supports:
        selected.append(
            {
                "chart_key": item.chart_key,
                "role": "support",
                "reason": item.reason,
            }
        )

    portfolio = next((item for item in ordered if item.category == "portfolio" and item.chart_key not in {row["chart_key"] for row in selected}), None)
    if portfolio:
        selected.append(
            {
                "chart_key": portfolio.chart_key,
                "role": "optional_portfolio",
                "reason": portfolio.reason,
            }
        )

    event = next((item for item in ordered if item.category == "event" and item.chart_key not in {row["chart_key"] for row in selected}), None)
    if event:
        selected.append(
            {
                "chart_key": event.chart_key,
                "role": "optional_event",
                "reason": event.reason,
            }
        )

    return selected


def _bundle_summary(normalized: dict[str, Any], regime_tags: list[str]) -> str:
    breadth = float(normalized.get("breadth") or 0.0) * 100.0
    us = float(normalized.get("us_avg") or 0.0)
    eu = float(normalized.get("eu_avg") or 0.0)
    oil = normalized.get("oil_delta_pct")
    oil_text = f"{oil:+.2f}%" if oil is not None else "n/a"
    return (
        f"Regime tags: {', '.join(regime_tags)} | breadth {breadth:.0f}% positive | "
        f"US {us:+.2f}% vs Europe {eu:+.2f}% | WTI {oil_text}"
    )


def _global_relative_spec(
    *,
    index_quotes: list[QuoteData],
    market_data_service: Any,
) -> dict[str, Any]:
    selected = [quote for quote in index_quotes if "vix" not in (quote.display_name or quote.symbol).lower()][:8]
    series: list[dict[str, Any]] = []
    missing: list[str] = []
    for quote in selected:
        history = market_data_service.get_price_history(quote.symbol, period="3mo", interval="1d") or []
        rebased_5 = _rebased(history, points=5)
        rebased_20 = _rebased(history, points=20)
        if not rebased_5:
            missing.append(quote.display_name or quote.symbol)
            continue
        series.append(
            {
                "name": quote.display_name or quote.symbol,
                "symbol": quote.symbol,
                "x_5d": list(range(len(rebased_5))),
                "y_5d": rebased_5,
                "x_20d": list(range(len(rebased_20))) if rebased_20 else [],
                "y_20d": rebased_20 or [],
            }
        )
    available = bool(series)
    return {
        "chart_key": "global_relative_performance",
        "variant": "5d_rebased",
        "available": available,
        "reason_if_hidden": None if available else "History unavailable for global index comparison.",
        "title": "Global Equity Leadership",
        "caption": "Rebased path comparison highlights cross-region leadership and divergence.",
        "series": series,
        "annotations": [],
        "meta": {
            "default_window": "5d",
            "supported_windows": ["1d", "5d", "20d"],
            "missing": missing,
        },
        "email_dimensions": {"width": 8.6, "height": 4.8},
    }


def _cross_asset_impulse_spec(
    *,
    macro_quotes: list[QuoteData],
    macro_context: list[MacroDataPoint],
    market_data_service: Any,
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for quote in macro_quotes[:8]:
        label = quote.display_name or quote.symbol
        symbol = quote.symbol
        if "treasury yield" in label.lower() or symbol.upper() == "^TNX":
            impulse = float(quote.change or 0.0) * 100.0
            unit = "bps"
        else:
            impulse = float(quote.change_percent or 0.0)
            unit = "pct"
        history = market_data_service.get_price_history(symbol, period="1mo", interval="1d") or []
        spark = _pct_path(history, points=6)
        points.append(
            {
                "name": label,
                "symbol": symbol,
                "impulse": round(float(impulse), 4),
                "unit": unit,
                "spark": spark,
            }
        )
    spread = _find_macro(macro_context, "SPREAD")
    if spread and spread.change is not None:
        points.append(
            {
                "name": "10Y-2Y Curve",
                "symbol": "UST_SPREAD",
                "impulse": round(float(spread.change) * 100.0, 4),
                "unit": "bps",
                "spark": [],
            }
        )
    available = bool(points)
    return {
        "chart_key": "cross_asset_impulse_strip",
        "variant": "daily_impulse",
        "available": available,
        "reason_if_hidden": None if available else "Macro instruments unavailable for impulse strip.",
        "title": "Cross-Asset Impulses",
        "caption": "Centered impulse read across rates, commodities, and macro risk gauges.",
        "series": points,
        "annotations": [{"label": "zero_line", "value": 0}],
        "meta": {"impulse_units": {"pct": "percent", "bps": "basis points"}},
        "email_dimensions": {"width": 8.4, "height": 4.6},
    }


def _holdings_excess_spec(
    *,
    holdings_quotes: list[QuoteData],
    benchmark_reference: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for quote in sorted(holdings_quotes, key=lambda row: float(row.change_percent or 0.0), reverse=True)[:8]:
        change = float(quote.change_percent or 0.0)
        rows.append(
            {
                "name": quote.display_name or quote.symbol,
                "symbol": quote.symbol,
                "change_pct": round(change, 4),
                "excess_pct": round(change - benchmark_reference, 4),
            }
        )
    available = bool(rows)
    return {
        "chart_key": "holdings_excess_performance",
        "variant": "lollipop_excess",
        "available": available,
        "reason_if_hidden": None if available else "No holdings/watchlist quotes available.",
        "title": "Portfolio Movers vs Benchmark",
        "caption": "Ranked holding moves with excess return versus broad market reference.",
        "series": rows,
        "annotations": [],
        "meta": {"benchmark_reference_pct": round(benchmark_reference, 4)},
        "email_dimensions": {"width": 8.4, "height": 4.8},
    }


def _sector_quadrant_spec(briefing: MorningBriefing, profile: UserProfile) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    sector_weight_map = {key.lower(): value for key, value in profile.portfolio_sector_weights.items()}
    # Sector weights are sourced from profile context in chart builder and passed via snapshot rows.
    for snapshot in briefing.sector_scan[:10]:
        if snapshot.etf_quote is None:
            continue
        points.append(
            {
                "name": snapshot.display_name,
                "sector_key": snapshot.sector_key,
                "x_exposure": round(float(sector_weight_map.get(snapshot.sector_key.lower(), 0.0) * 100.0), 4),
                "y_change_pct": round(float(snapshot.etf_quote.change_percent or 0.0), 4),
            }
        )
    available = bool(points)
    return {
        "chart_key": "sector_exposure_quadrant",
        "variant": "exposure_vs_move",
        "available": available,
        "reason_if_hidden": None if available else "Sector exposure/performance inputs unavailable.",
        "title": "Sector Exposure vs Move",
        "caption": "Quadrant view of exposure concentration versus latest sector move.",
        "series": points,
        "annotations": [{"label": "origin", "x": 0, "y": 0}],
        "meta": {},
        "email_dimensions": {"width": 8.4, "height": 4.8},
    }


def _event_linked_spec(briefing: MorningBriefing, market_data_service: Any) -> dict[str, Any]:
    focus_symbol = _pick_focus_symbol(briefing)
    if not focus_symbol:
        return {
            "chart_key": "event_linked_annotated_trend",
            "variant": "30d",
            "available": False,
            "reason_if_hidden": "No portfolio-linked focus symbol identified.",
            "title": "Event-Linked Trend",
            "caption": "Annotated trend appears when a deterministic focus symbol is available.",
            "series": [],
            "annotations": [],
            "meta": {},
            "email_dimensions": {"width": 8.4, "height": 4.8},
        }

    history = market_data_service.get_price_history(focus_symbol, period="3mo", interval="1d") or []
    if len(history) < 4:
        return {
            "chart_key": "event_linked_annotated_trend",
            "variant": "30d",
            "available": False,
            "reason_if_hidden": f"Insufficient history for {focus_symbol}.",
            "title": f"{focus_symbol} Event-Linked Trend",
            "caption": "Trend unavailable due to limited history points.",
            "series": [],
            "annotations": [],
            "meta": {"symbol": focus_symbol},
            "email_dimensions": {"width": 8.4, "height": 4.8},
        }
    path = history[-30:]
    x = [idx for idx, _ in enumerate(path)]
    y = [round(float(point.close), 4) for point in path]
    marker = max(1, len(path) - 6)
    return {
        "chart_key": "event_linked_annotated_trend",
        "variant": "30d",
        "available": True,
        "reason_if_hidden": None,
        "title": f"{focus_symbol} Event-Linked Trend",
        "caption": "Annotated window for the most portfolio-relevant current catalyst symbol.",
        "series": [
            {
                "name": focus_symbol,
                "symbol": focus_symbol,
                "x": x,
                "y": y,
            }
        ],
        "annotations": [
            {"label": "event_window_start", "x": marker},
            {"label": "latest", "x": len(path) - 1},
        ],
        "meta": {"symbol": focus_symbol},
        "email_dimensions": {"width": 8.4, "height": 4.8},
    }


def _priority_global_relative(metrics: dict[str, Any], tags: list[str]) -> float:
    divergence = abs(float(metrics.get("us_avg") or 0.0) - float(metrics.get("eu_avg") or 0.0))
    breadth = abs((float(metrics.get("breadth") or 0.5) - 0.5) * 2.0)
    bonus = 0.1 if "regional_split" in tags else 0.0
    return 0.82 + min(0.18, divergence * 0.08 + breadth * 0.08 + bonus)


def _priority_impulse(metrics: dict[str, Any], tags: list[str]) -> float:
    rates = abs(float(metrics.get("ten_y_change") or 0.0)) * 12.0
    oil = abs(float(metrics.get("oil_delta_pct") or 0.0)) * 0.02
    bonus = 0.08 if "oil_shock" in tags or "rates_led" in tags else 0.0
    return 0.76 + min(0.2, rates + oil + bonus)


def _avg_change(quotes: list[QuoteData], keys: tuple[str, ...]) -> float:
    vals = [
        float(quote.change_percent or 0.0)
        for quote in quotes
        if any(key in (quote.display_name or quote.symbol).lower() for key in keys)
    ]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _find_quote(quotes: list[QuoteData], keys: tuple[str, ...]) -> QuoteData | None:
    for quote in quotes:
        text = (quote.display_name or quote.symbol).lower()
        if any(key in text for key in keys):
            return quote
    return None


def _find_macro(points: list[MacroDataPoint], key: str) -> MacroDataPoint | None:
    target = key.lower()
    for point in points:
        text = f"{point.series_id} {point.name}".lower()
        if target == "10y" and ("10y" in text or "dgs10" in text):
            return point
        if target == "2y" and ("2y" in text or "dgs2" in text):
            return point
        if target == "spread" and ("spread" in text or "t10y2y" in text):
            return point
    return None


def _rebased(history: list[PricePoint], *, points: int) -> list[float]:
    if len(history) < 2:
        return []
    data = history[-points:]
    base = float(data[0].close or 0.0)
    if base <= 0:
        return []
    return [round((float(point.close) / base) * 100.0, 4) for point in data]


def _pct_path(history: list[PricePoint], *, points: int) -> list[float]:
    if len(history) < 2:
        return []
    data = history[-points:]
    base = float(data[0].close or 0.0)
    if base <= 0:
        return []
    return [round(((float(point.close) / base) - 1.0) * 100.0, 4) for point in data]


def _pick_focus_symbol(briefing: MorningBriefing) -> str:
    portfolio_symbols = {row.symbol for row in briefing.portfolio_quotes if getattr(row, "symbol", None)}
    for events in (briefing.portfolio_focus, briefing.top_themes, briefing.watchlist_events):
        for event in events:
            for ticker in event.tickers:
                if ticker in portfolio_symbols:
                    return ticker
    if portfolio_symbols:
        return sorted(portfolio_symbols)[0]
    if briefing.watchlist_quotes:
        return briefing.watchlist_quotes[0].symbol
    return ""
