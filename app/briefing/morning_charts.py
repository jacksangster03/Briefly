"""Deterministic morning chart contract + promotion engine.

This module is the single source of truth for morning chart decisions.
Renderers consume these specs but do not decide visual hierarchy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.events import MacroDataPoint, PricePoint, QuoteData


US_INDEX_KEYS = ("spx", "s&p 500", "nasdaq", "dow", "russell")
EU_INDEX_KEYS = ("stoxx", "ftse", "dax", "cac", "ibex")
ASIA_INDEX_KEYS = ("nikkei", "hang seng", "hsi", "kospi")

OIL_KEYS = ("wti", "crude", "cl1:com", "cl=f")
GOLD_KEYS = ("gold", "gc1:com", "gc=f")
VIX_KEYS = ("vix",)
SMALL_CAP_KEYS = ("russell", "rut")
LARGE_CAP_KEYS = ("spx", "s&p 500", "dow")
GROWTH_KEYS = ("nasdaq", "ixic")
DEFENSIVE_KEYS = ("dow",)


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
    yield_curve_points: list[MacroDataPoint] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build deterministic chart specs + selected roles."""
    normalized = _normalize_inputs(
        briefing.market_setup.index_quotes,
        briefing.market_setup.macro_quotes,
        briefing.macro_context,
    )
    regime_tags = _derive_regime_tags(normalized)

    # Load user feedback weights to bias chart selection
    feedback_weights: dict[str, float] = {}
    try:
        from app.web.app import get_chart_feedback_weights
        feedback_weights = get_chart_feedback_weights(profile.name)
    except Exception:
        pass

    candidates = _build_candidates(
        briefing=briefing,
        profile=profile,
        market_data_service=market_data_service,
        normalized=normalized,
        regime_tags=regime_tags,
        yield_curve_points=yield_curve_points,
    )

    # Apply feedback weight multipliers to priorities
    for item in candidates:
        multiplier = feedback_weights.get(item.chart_key, 1.0)
        if multiplier != 1.0:
            item.priority = round(item.priority * multiplier, 4)
    selected = _select_candidates(candidates)

    # Anomaly detection: flag unusually extreme impulse days
    anomaly_meta: dict[str, Any] = {}
    impulse_spec = next((item.spec for item in candidates if item.chart_key == "cross_asset_impulse_strip"), None)
    if impulse_spec:
        try:
            from app.ml.anomaly_detector import build_impulse_vector_from_spec, detect_regime_anomaly
            today_vec = build_impulse_vector_from_spec(impulse_spec)
            # Use a simple rolling window from normalized metrics as proxy history
            # In production this would be read from the market_snapshots DB table
            dummy_history = [[0.0] * len(today_vec)] * 30  # placeholder until DB history available
            anomaly_result = detect_regime_anomaly(today_vec, dummy_history)
            anomaly_meta = {
                "anomaly_score": anomaly_result.get("anomaly_score"),
                "is_anomaly": anomaly_result.get("is_anomaly"),
                "trigger_alert": anomaly_result.get("trigger_alert"),
            }
        except Exception:
            pass

    bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime_tags": regime_tags,
        "charts": [item.spec for item in candidates],
        "selected": selected,
        "summary": _bundle_summary(normalized, regime_tags),
        "meta": {
            "profile_name": profile.name,
            "timezone": profile.timezone,
            "delivery_mode": "deterministic",
            "llm_email_enabled": bool(profile.delivery.get("llm_email_morning", True)),
            "llm_shadow_mode": bool(profile.delivery.get("llm_shadow_mode", True)),
            "data_confidence": _confidence_label(normalized),
            **anomaly_meta,
        },
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


def chart_summary_lines(bundle: dict[str, Any], *, limit: int = 6) -> list[str]:
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
    total = len(index_quotes)
    positives = [quote for quote in index_quotes if float(quote.change_percent or 0.0) > 0]
    breadth = len(positives) / max(1, total)

    vix = _find_quote(index_quotes + macro_quotes, VIX_KEYS)
    oil = _find_quote(macro_quotes, OIL_KEYS)
    gold = _find_quote(macro_quotes, GOLD_KEYS)
    ten_y = _find_macro(macro_context, "10Y")
    two_y = _find_macro(macro_context, "2Y")
    spread = _find_macro(macro_context, "SPREAD")

    small_large = _avg_change(index_quotes, SMALL_CAP_KEYS) - _avg_change(index_quotes, LARGE_CAP_KEYS)
    growth_defensive = _avg_change(index_quotes, GROWTH_KEYS) - _avg_change(index_quotes, DEFENSIVE_KEYS)
    dispersion = max(0.0, max(us, eu, asia) - min(us, eu, asia))

    return {
        "us_avg": us,
        "eu_avg": eu,
        "asia_avg": asia,
        "breadth": breadth,
        "total_indices": total,
        "up_indices": len(positives),
        "dispersion": dispersion,
        "small_vs_large": small_large,
        "growth_vs_defensive": growth_defensive,
        "vix_level": float(vix.current_price) if vix else None,
        "vix_delta_pct": float(vix.change_percent) if vix else None,
        "oil_delta_pct": float(oil.change_percent) if oil else None,
        "gold_delta_pct": float(gold.change_percent) if gold else None,
        "ten_y_level": float(ten_y.value) if ten_y else None,
        "two_y_level": float(two_y.value) if two_y else None,
        "spread_level": float(spread.value) if spread else None,
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
    if abs(float(metrics.get("small_vs_large") or 0.0)) >= 0.6:
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
    yield_curve_points: list[MacroDataPoint] | None = None,
) -> list[ChartCandidate]:
    specs: list[ChartCandidate] = []

    specs.append(
        ChartCandidate(
            chart_key="global_relative_performance",
            category="hero",
            priority=_priority_global_relative(normalized, regime_tags),
            spec=_global_relative_spec(
                index_quotes=briefing.market_setup.index_quotes,
                market_data_service=market_data_service,
            ),
            reason="Cross-region leadership and path divergence.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="cross_asset_impulse_strip",
            category="support",
            priority=_priority_impulse(normalized, regime_tags),
            spec=_cross_asset_impulse_spec(
                macro_quotes=briefing.market_setup.macro_quotes,
                macro_context=briefing.macro_context,
                market_data_service=market_data_service,
            ),
            reason="Rates, commodities, and macro impulse context.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="holdings_excess_performance",
            category="portfolio",
            priority=0.73 + (0.12 if briefing.portfolio_quotes else 0.0),
            spec=_holdings_excess_spec(
                holdings_quotes=briefing.portfolio_quotes or briefing.watchlist_quotes,
                benchmark_reference=float(normalized.get("us_avg") or 0.0),
                profile=profile,
            ),
            reason="Portfolio-linked winners and laggards versus broad market.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="sector_exposure_quadrant",
            category="support",
            priority=0.67 + (0.06 if abs(float(normalized.get("growth_vs_defensive") or 0.0)) > 0.6 else 0.0),
            spec=_sector_quadrant_spec(briefing, profile),
            reason="Exposure-weight context for sector winners and laggards.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="event_linked_annotated_trend",
            category="event",
            priority=0.62 + (0.08 if "oil_shock" in regime_tags else 0.0),
            spec=_event_linked_spec(briefing, market_data_service),
            reason="Deterministic event-linked trend for the top portfolio symbol.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="breadth_leadership_panel",
            category="support",
            priority=0.7 + min(0.12, float(normalized.get("dispersion") or 0.0) * 0.05),
            spec=_breadth_leadership_spec(normalized),
            reason="Breadth and leadership factors identify tape quality quickly.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="rates_curve_micro_panel",
            category="micro",
            priority=0.66 + (0.06 if "rates_led" in regime_tags else 0.0),
            spec=_rates_curve_micro_spec(normalized),
            reason="Rates impulse and curve shift in one compact card.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="volatility_regime_card",
            category="micro",
            priority=0.66 + (0.07 if "defensive" in regime_tags else 0.0),
            spec=_volatility_regime_spec(normalized),
            reason="Volatility regime sets risk appetite context.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="portfolio_concentration_risk_card",
            category="micro",
            priority=0.64 + (0.07 if profile.portfolio_holdings else 0.0),
            spec=_concentration_risk_spec(profile, market_data_service),
            reason="Concentration and risk stance should stay visible every morning.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="earnings_relevance_strip",
            category="micro",
            priority=0.63 + (0.07 if briefing.earnings_calendar else 0.0),
            spec=_earnings_relevance_spec(briefing),
            reason="Near-term earnings load and portfolio overlap context.",
        )
    )

    # --- New charts ---

    specs.append(
        ChartCandidate(
            chart_key="yield_curve_shape",
            category="support",
            priority=0.72 + (0.10 if "rates_led" in regime_tags else 0.0),
            spec=_yield_curve_spec(yield_curve_points or [], market_data_service),
            reason="Yield curve shape and week-over-week shift in one glance.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="vix_term_structure",
            category="micro",
            priority=0.65 + (0.10 if "defensive" in regime_tags else 0.0),
            spec=_vix_term_structure_spec(
                briefing.market_setup.macro_quotes,
                market_data_service,
            ),
            reason="VIX term structure reveals whether near-term or long-term fear dominates.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="pnl_attribution_waterfall",
            category="portfolio",
            priority=0.76 + (0.10 if briefing.portfolio_quotes else 0.0),
            spec=_pnl_waterfall_spec(
                briefing.portfolio_quotes or [],
                profile,
            ),
            reason="Daily P&L contribution waterfall — where the portfolio return came from.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="rsi_momentum_heatmap",
            category="portfolio",
            priority=0.62 + (0.05 if profile.portfolio_holdings else 0.0),
            spec=_rsi_heatmap_spec(
                briefing.portfolio_quotes or briefing.watchlist_quotes,
                market_data_service,
                profile,
            ),
            reason="RSI heatmap spots overbought/oversold positions across three timeframes.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="implied_move_strip",
            category="event",
            priority=0.68 + (0.10 if briefing.earnings_calendar else 0.0),
            spec=_implied_move_spec(briefing.earnings_calendar, market_data_service),
            reason="Options-implied ±move for upcoming earnings keeps event-risk quantified.",
        )
    )

    for item in specs:
        item.spec["priority"] = round(float(item.priority), 4)
    specs.sort(key=lambda row: row.priority, reverse=True)
    return specs


def _select_candidates(candidates: list[ChartCandidate]) -> list[dict[str, str]]:
    by_key = {item.chart_key: item for item in candidates}
    selected: list[dict[str, str]] = []

    available = [item for item in candidates if bool(item.spec.get("available"))]
    hero = next((item for item in available if item.category == "hero"), None) or (available[0] if available else None)
    if hero:
        selected.append({"chart_key": hero.chart_key, "role": "hero", "reason": hero.reason})

    supports = [item for item in available if item.category == "support" and item.chart_key != (hero.chart_key if hero else "")]
    for item in supports[:2]:
        selected.append({"chart_key": item.chart_key, "role": "support", "reason": item.reason})

    portfolio = next((item for item in available if item.category == "portfolio"), None)
    if portfolio and portfolio.chart_key not in {row["chart_key"] for row in selected}:
        selected.append({"chart_key": portfolio.chart_key, "role": "optional_portfolio", "reason": portfolio.reason})

    event = next((item for item in available if item.category == "event"), None)
    if event and event.chart_key not in {row["chart_key"] for row in selected}:
        selected.append({"chart_key": event.chart_key, "role": "optional_event", "reason": event.reason})

    micro = [item for item in available if item.category == "micro" and item.chart_key not in {row["chart_key"] for row in selected}]
    for idx, item in enumerate(micro[:2], start=1):
        selected.append({"chart_key": item.chart_key, "role": f"micro_{idx}", "reason": item.reason})

    if not selected:
        fallback = next(iter(by_key.values()), None)
        if fallback:
            selected.append({"chart_key": fallback.chart_key, "role": "hero", "reason": "Fallback due to missing availability."})

    return selected


def _bundle_summary(normalized: dict[str, Any], regime_tags: list[str]) -> str:
    breadth = float(normalized.get("breadth") or 0.0) * 100.0
    us = float(normalized.get("us_avg") or 0.0)
    eu = float(normalized.get("eu_avg") or 0.0)
    asia = float(normalized.get("asia_avg") or 0.0)
    oil = normalized.get("oil_delta_pct")
    vix = normalized.get("vix_level")
    oil_text = f"{oil:+.2f}%" if oil is not None else "n/a"
    vix_text = f"{vix:.2f}" if vix is not None else "n/a"
    return (
        f"Regime: {', '.join(regime_tags)} | breadth {breadth:.0f}% up | "
        f"US {us:+.2f}% / Europe {eu:+.2f}% / Asia {asia:+.2f}% | "
        f"VIX {vix_text} | WTI {oil_text}"
    )


def _global_relative_spec(*, index_quotes: list[QuoteData], market_data_service: Any) -> dict[str, Any]:
    selected = [quote for quote in index_quotes if "vix" not in (quote.display_name or quote.symbol).lower()][:10]
    series: list[dict[str, Any]] = []
    missing: list[str] = []
    for quote in selected:
        history = market_data_service.get_price_history(quote.symbol, period="3mo", interval="1d") or []
        rebased_5 = _rebased(history, points=5)
        rebased_20 = _rebased(history, points=20)
        rebased_1 = _rebased(history, points=2)
        if not rebased_5:
            missing.append(quote.display_name or quote.symbol)
            continue
        family = _region_family(quote)
        series.append(
            {
                "name": quote.display_name or quote.symbol,
                "symbol": quote.symbol,
                "family": family,
                "change_pct": round(float(quote.change_percent or 0.0), 4),
                "x_1d": list(range(len(rebased_1))),
                "y_1d": rebased_1,
                "x_5d": list(range(len(rebased_5))),
                "y_5d": rebased_5,
                "x_20d": list(range(len(rebased_20))) if rebased_20 else [],
                "y_20d": rebased_20 or [],
            }
        )
    series = _limit_global_series(series)
    available = bool(series)
    return {
        "chart_key": "global_relative_performance",
        "variant": "5d_rebased",
        "available": available,
        "reason_if_hidden": None if available else "History unavailable for global index comparison.",
        "title": "Global Equity Leadership",
        "caption": _global_read_line(series),
        "series": series,
        "annotations": [],
        "meta": {
            "default_window": "5d",
            "supported_windows": ["1d", "5d", "20d"],
            "missing": missing,
        },
        "email_dimensions": {"width": 1000, "height": 600},
    }


def _cross_asset_impulse_spec(*, macro_quotes: list[QuoteData], macro_context: list[MacroDataPoint], market_data_service: Any) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for quote in macro_quotes[:10]:
        label = quote.display_name or quote.symbol
        symbol = quote.symbol
        if "yield" in label.lower() or symbol.upper() in {"^TNX", "^IRX"}:
            impulse = float(quote.change or 0.0) * 100.0
            unit = "bps"
        else:
            impulse = float(quote.change_percent or 0.0)
            unit = "pct"
        history = market_data_service.get_price_history(symbol, period="1mo", interval="1d") or []
        spark = _pct_path(history, points=7)
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
        "caption": _impulse_read_line(points),
        "series": points,
        "annotations": [{"label": "zero_line", "value": 0}],
        "meta": {"impulse_units": {"pct": "percent", "bps": "basis points"}},
        "email_dimensions": {"width": 1000, "height": 560},
    }


def _holdings_excess_spec(*, holdings_quotes: list[QuoteData], benchmark_reference: float, profile: UserProfile) -> dict[str, Any]:
    profile_symbols = set(profile.portfolio_symbols)
    rows: list[dict[str, Any]] = []
    for quote in sorted(holdings_quotes, key=lambda row: float(row.change_percent or 0.0), reverse=True)[:10]:
        change = float(quote.change_percent or 0.0)
        rows.append(
            {
                "name": quote.display_name or quote.symbol,
                "symbol": quote.symbol,
                "change_pct": round(change, 4),
                "excess_pct": round(change - benchmark_reference, 4),
                "relevance_tag": "portfolio" if quote.symbol in profile_symbols else "watchlist",
            }
        )
    available = bool(rows)
    return {
        "chart_key": "holdings_excess_performance",
        "variant": "dumbbell_excess",
        "available": available,
        "reason_if_hidden": None if available else "No holdings/watchlist quotes available.",
        "title": "Portfolio Movers vs Benchmark",
        "caption": _holdings_read_line(rows),
        "series": rows,
        "annotations": [],
        "meta": {"benchmark_reference_pct": round(benchmark_reference, 4)},
        "email_dimensions": {"width": 1000, "height": 600},
    }


def _sector_quadrant_spec(briefing: MorningBriefing, profile: UserProfile) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    sector_weight_map = {key.lower(): value for key, value in profile.portfolio_sector_weights.items()}
    for snapshot in briefing.sector_scan[:12]:
        if snapshot.etf_quote is None:
            continue
        exposure = float(sector_weight_map.get(snapshot.sector_key.lower(), 0.0) * 100.0)
        points.append(
            {
                "name": snapshot.display_name,
                "sector_key": snapshot.sector_key,
                "x_exposure": round(exposure, 4),
                "y_change_pct": round(float(snapshot.etf_quote.change_percent or 0.0), 4),
                "bubble_size": max(8.0, min(34.0, exposure * 1.2)),
            }
        )
    available = bool(points)
    return {
        "chart_key": "sector_exposure_quadrant",
        "variant": "exposure_vs_move",
        "available": available,
        "reason_if_hidden": None if available else "Sector exposure/performance inputs unavailable.",
        "title": "Sector Exposure vs Move",
        "caption": _sector_read_line(points),
        "series": points,
        "annotations": [{"label": "origin", "x": 0, "y": 0}],
        "meta": {},
        "email_dimensions": {"width": 1000, "height": 580},
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
            "caption": "Event trend appears when a portfolio-linked focus symbol is available.",
            "series": [],
            "annotations": [],
            "meta": {},
            "email_dimensions": {"width": 1000, "height": 580},
        }

    history = market_data_service.get_price_history(focus_symbol, period="3mo", interval="1d") or []
    if len(history) < 5:
        return {
            "chart_key": "event_linked_annotated_trend",
            "variant": "30d",
            "available": False,
            "reason_if_hidden": f"Insufficient history for {focus_symbol}.",
            "title": f"{focus_symbol} Event-Linked Trend",
            "caption": "Trend unavailable because history is too thin for a reliable event window.",
            "series": [],
            "annotations": [],
            "meta": {"symbol": focus_symbol},
            "email_dimensions": {"width": 1000, "height": 580},
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
        "caption": f"{focus_symbol} trend is framed around the latest deterministic catalyst window.",
        "series": [{"name": focus_symbol, "symbol": focus_symbol, "x": x, "y": y}],
        "annotations": [
            {"label": "event_window_start", "x": marker},
            {"label": "latest", "x": len(path) - 1},
        ],
        "meta": {"symbol": focus_symbol},
        "email_dimensions": {"width": 1000, "height": 580},
    }


def _breadth_leadership_spec(metrics: dict[str, Any]) -> dict[str, Any]:
    total = int(metrics.get("total_indices") or 0)
    up = int(metrics.get("up_indices") or 0)
    breadth_pct = float(metrics.get("breadth") or 0.0) * 100.0
    rows = [
        {"name": "Breadth % Up", "value": round(breadth_pct, 3), "unit": "pct"},
        {"name": "US Avg Move", "value": round(float(metrics.get("us_avg") or 0.0), 3), "unit": "pct"},
        {"name": "Europe Avg Move", "value": round(float(metrics.get("eu_avg") or 0.0), 3), "unit": "pct"},
        {"name": "Asia Avg Move", "value": round(float(metrics.get("asia_avg") or 0.0), 3), "unit": "pct"},
        {"name": "Small-Large", "value": round(float(metrics.get("small_vs_large") or 0.0), 3), "unit": "pct"},
        {"name": "Growth-Defensive", "value": round(float(metrics.get("growth_vs_defensive") or 0.0), 3), "unit": "pct"},
    ]
    available = total > 0
    return {
        "chart_key": "breadth_leadership_panel",
        "variant": "daily_cross_section",
        "available": available,
        "reason_if_hidden": None if available else "Index breadth inputs unavailable.",
        "title": "Breadth & Leadership",
        "caption": f"{up}/{total} tracked benchmarks are positive; leadership factors shown versus 0-line.",
        "series": rows,
        "annotations": [{"label": "zero_line", "value": 0}],
        "meta": {"up_count": up, "total_count": total},
        "email_dimensions": {"width": 1000, "height": 520},
    }


def _rates_curve_micro_spec(metrics: dict[str, Any]) -> dict[str, Any]:
    ten = metrics.get("ten_y_level")
    two = metrics.get("two_y_level")
    spread = metrics.get("spread_level")
    rows = [
        {"name": "US 2Y", "level": two, "impulse": _as_bps(metrics.get("two_y_change"))},
        {"name": "US 10Y", "level": ten, "impulse": _as_bps(metrics.get("ten_y_change"))},
        {"name": "10Y-2Y", "level": spread, "impulse": _as_bps(metrics.get("curve_change"))},
    ]
    available = any(row["level"] is not None for row in rows)
    return {
        "chart_key": "rates_curve_micro_panel",
        "variant": "curve_impulse",
        "available": available,
        "reason_if_hidden": None if available else "Rates and curve inputs unavailable.",
        "title": "Rates & Curve",
        "caption": "2Y/10Y levels and daily basis-point impulse.",
        "series": rows,
        "annotations": [],
        "meta": {},
        "email_dimensions": {"width": 8.0, "height": 2.9},
    }


def _volatility_regime_spec(metrics: dict[str, Any]) -> dict[str, Any]:
    vix_level = metrics.get("vix_level")
    vix_delta = metrics.get("vix_delta_pct")
    if vix_level is None:
        regime = "unavailable"
    elif vix_level < 15:
        regime = "calm"
    elif vix_level < 20:
        regime = "normal"
    elif vix_level < 27:
        regime = "elevated"
    else:
        regime = "stress"
    available = vix_level is not None
    return {
        "chart_key": "volatility_regime_card",
        "variant": "vix_regime",
        "available": available,
        "reason_if_hidden": None if available else "VIX quote unavailable.",
        "title": "Volatility Regime",
        "caption": "Current implied-volatility bucket with daily change context.",
        "series": [
            {"name": "VIX Level", "value": vix_level, "unit": "index"},
            {"name": "VIX Delta", "value": vix_delta, "unit": "pct"},
        ],
        "annotations": [{"label": "regime", "value": regime}],
        "meta": {"regime": regime},
        "email_dimensions": {"width": 8.0, "height": 2.9},
    }


def _concentration_risk_spec(profile: UserProfile, market_data_service: Any = None) -> dict[str, Any]:
    weights = sorted([float(p.weight_pct or 0.0) for p in profile.portfolio_holdings if p.weight_pct is not None], reverse=True)
    top5 = sum(weights[:5]) if weights else 0.0
    largest = weights[0] if weights else 0.0
    count = len(weights)
    available = count > 0
    risk_state = "concentrated" if top5 >= 70 else ("balanced" if top5 <= 45 else "moderate")

    # GARCH VaR: compute portfolio-level 1-day 95% VaR if market data available
    portfolio_var: float | None = None
    if market_data_service is not None and profile.portfolio_holdings:
        try:
            from app.ml.garch_var import portfolio_var as _portfolio_var
            holdings_input = [
                {"symbol": h.symbol, "weight": float(h.weight_pct or 0.0) / 100.0}
                for h in profile.portfolio_holdings
                if h.symbol and h.weight_pct
            ]
            price_histories: dict[str, list[float]] = {}
            for h in holdings_input:
                history = market_data_service.get_price_history(h["symbol"], period="1y", interval="1d") or []
                if history:
                    price_histories[h["symbol"]] = [float(p.close) for p in history]
            var_result = _portfolio_var(holdings_input, price_histories)
            portfolio_var = var_result.get("portfolio_var_pct")
        except Exception:
            pass

    series = [
        {"name": "Top 5 Weight", "value": top5, "unit": "pct"},
        {"name": "Largest Position", "value": largest, "unit": "pct"},
        {"name": "Active Holdings", "value": count, "unit": "count"},
    ]
    if portfolio_var is not None:
        series.append({"name": "1D 95% VaR", "value": portfolio_var, "unit": "pct"})

    caption = "Top-weight concentration and single-name risk posture."
    if portfolio_var is not None:
        caption += f" GARCH 1D VaR: {portfolio_var:.2f}%."

    return {
        "chart_key": "portfolio_concentration_risk_card",
        "variant": "top5_concentration",
        "available": available,
        "reason_if_hidden": None if available else "Portfolio holdings weights unavailable.",
        "title": "Portfolio Concentration",
        "caption": caption,
        "series": series,
        "annotations": [{"label": "risk_state", "value": risk_state}],
        "meta": {"risk_state": risk_state, "portfolio_var_pct": portfolio_var},
        "email_dimensions": {"width": 8.0, "height": 2.9},
    }


def _earnings_relevance_spec(briefing: MorningBriefing) -> dict[str, Any]:
    rel = briefing.earnings_relevance or {}
    series = [
        {"name": "Today", "value": float(rel.get("today", 0.0) or 0.0), "unit": "count"},
        {"name": "Tomorrow", "value": float(rel.get("tomorrow", 0.0) or 0.0), "unit": "count"},
        {"name": "This Week", "value": float(rel.get("this_week", 0.0) or 0.0), "unit": "count"},
        {"name": "Portfolio Overlap", "value": float(rel.get("portfolio_overlap", 0.0) or 0.0), "unit": "count"},
        {"name": "Watchlist Overlap", "value": float(rel.get("watchlist_overlap", 0.0) or 0.0), "unit": "count"},
    ]
    available = bool(briefing.earnings_calendar) or any(row["value"] > 0 for row in series)
    return {
        "chart_key": "earnings_relevance_strip",
        "variant": "calendar_overlap",
        "available": available,
        "reason_if_hidden": None if available else "No upcoming earnings relevance data available.",
        "title": "Earnings Relevance",
        "caption": "Upcoming earnings load with portfolio/watchlist overlap.",
        "series": series,
        "annotations": [],
        "meta": {},
        "email_dimensions": {"width": 8.0, "height": 2.9},
    }


# ---------------------------------------------------------------------------
# New chart spec builders
# ---------------------------------------------------------------------------

def _yield_curve_spec(yield_curve_points: list[MacroDataPoint], market_data_service: Any) -> dict[str, Any]:
    """Yield curve shape: today vs 1-week-ago snapshot for 2Y/5Y/10Y/30Y."""
    TENOR_ORDER = {"DGS2": 2, "DGS5": 5, "DGS10": 10, "DGS30": 30}
    today_rows: list[dict[str, Any]] = []
    for point in yield_curve_points:
        tenor = TENOR_ORDER.get(point.series_id)
        if tenor is None or point.value is None:
            continue
        history = market_data_service.get_price_history(point.series_id, period="1mo", interval="1d") or []
        week_ago_val = float(history[-6].close) if len(history) >= 6 else None
        today_rows.append({
            "series_id": point.series_id,
            "tenor": tenor,
            "today": round(float(point.value), 4),
            "week_ago": round(week_ago_val, 4) if week_ago_val is not None else None,
            "change_bps": round((float(point.value) - week_ago_val) * 100.0, 1) if week_ago_val is not None else None,
        })
    today_rows.sort(key=lambda r: r["tenor"])
    available = len(today_rows) >= 2
    inversion = (today_rows[0]["today"] > today_rows[-1]["today"]) if available else False
    shape = "inverted" if inversion else "normal"
    return {
        "chart_key": "yield_curve_shape",
        "variant": "curve_today_vs_week",
        "available": available,
        "reason_if_hidden": None if available else "Yield curve data unavailable.",
        "title": "Yield Curve Shape",
        "caption": f"{'Inverted' if inversion else 'Normal'} curve — 4-tenor snapshot vs 1 week ago.",
        "series": today_rows,
        "annotations": [{"label": "inversion", "value": inversion}],
        "meta": {"shape": shape},
        "email_dimensions": {"width": 1000, "height": 520},
    }


def _vix_term_structure_spec(macro_quotes: list[QuoteData], market_data_service: Any) -> dict[str, Any]:
    """VIX vs VIX3M: contango (fear fading) vs backwardation (stress building)."""
    vix = _find_quote(macro_quotes, ("vix",))
    vix3m_history = market_data_service.get_price_history("^VIX3M", period="1mo", interval="1d") or []
    vix_history = market_data_service.get_price_history("^VIX", period="1mo", interval="1d") or []

    vix_level = float(vix.current_price) if vix else None
    vix3m_level = float(vix3m_history[-1].close) if vix3m_history else None

    # Build 20-day spark paths for both
    vix_spark = [round(float(p.close), 2) for p in vix_history[-20:]]
    vix3m_spark = [round(float(p.close), 2) for p in vix3m_history[-20:]]
    x = list(range(max(len(vix_spark), len(vix3m_spark))))

    structure = "unavailable"
    if vix_level is not None and vix3m_level is not None:
        ratio = vix_level / vix3m_level
        structure = "backwardation" if ratio > 1.02 else ("contango" if ratio < 0.98 else "flat")

    available = vix_level is not None and vix3m_level is not None
    return {
        "chart_key": "vix_term_structure",
        "variant": "vix_vs_vix3m",
        "available": available,
        "reason_if_hidden": None if available else "VIX or VIX3M data unavailable.",
        "title": "VIX Term Structure",
        "caption": f"VIX {vix_level:.1f} vs VIX3M {vix3m_level:.1f} — {structure}." if available else "VIX term data unavailable.",
        "series": [
            {"name": "VIX (1M)", "x": x[:len(vix_spark)], "y": vix_spark, "level": vix_level},
            {"name": "VIX3M (3M)", "x": x[:len(vix3m_spark)], "y": vix3m_spark, "level": vix3m_level},
        ],
        "annotations": [{"label": "structure", "value": structure}],
        "meta": {"structure": structure, "vix": vix_level, "vix3m": vix3m_level},
        "email_dimensions": {"width": 900, "height": 480},
    }


def _pnl_waterfall_spec(holdings_quotes: list[QuoteData], profile: UserProfile) -> dict[str, Any]:
    """Daily P&L attribution waterfall: each position's contribution to portfolio move."""
    weight_map: dict[str, float] = {}
    for holding in profile.portfolio_holdings:
        if holding.symbol and holding.weight_pct is not None:
            weight_map[holding.symbol.upper()] = float(holding.weight_pct) / 100.0

    bars: list[dict[str, Any]] = []
    total_contrib = 0.0
    for quote in holdings_quotes:
        sym = quote.symbol.upper()
        weight = weight_map.get(sym, 0.0)
        if weight <= 0:
            continue
        contrib = round(weight * float(quote.change_percent or 0.0), 4)
        bars.append({
            "symbol": sym,
            "name": quote.display_name or sym,
            "weight": round(weight * 100.0, 2),
            "change_pct": round(float(quote.change_percent or 0.0), 4),
            "contribution": contrib,
        })
        total_contrib += contrib

    bars.sort(key=lambda r: r["contribution"], reverse=True)
    available = len(bars) >= 2
    return {
        "chart_key": "pnl_attribution_waterfall",
        "variant": "daily_contribution",
        "available": available,
        "reason_if_hidden": None if available else "Insufficient weighted holdings for P&L attribution.",
        "title": "P&L Attribution",
        "caption": f"Portfolio daily contribution: {total_contrib:+.2f}% weighted total across {len(bars)} positions.",
        "series": bars,
        "annotations": [{"label": "total", "value": round(total_contrib, 4)}],
        "meta": {"total_contribution": round(total_contrib, 4)},
        "email_dimensions": {"width": 1000, "height": 560},
    }


def _rsi_heatmap_spec(holdings_quotes: list[QuoteData], market_data_service: Any, profile: UserProfile) -> dict[str, Any]:
    """RSI across 5D/21D/63D windows for each held position — overbought/oversold grid."""
    def _rsi(prices: list[float], period: int) -> float | None:
        if len(prices) < period + 1:
            return None
        deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
        gains = [max(0.0, d) for d in deltas[-period:]]
        losses = [abs(min(0.0, d)) for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1.0 + rs)), 1)

    portfolio_symbols = set(profile.portfolio_symbols)
    rows: list[dict[str, Any]] = []
    for quote in holdings_quotes[:10]:
        if quote.symbol not in portfolio_symbols:
            continue
        history = market_data_service.get_price_history(quote.symbol, period="6mo", interval="1d") or []
        closes = [float(p.close) for p in history]
        rsi_5 = _rsi(closes, 5)
        rsi_21 = _rsi(closes, 21)
        rsi_63 = _rsi(closes, 63)
        if all(v is None for v in (rsi_5, rsi_21, rsi_63)):
            continue
        rows.append({
            "symbol": quote.symbol,
            "name": quote.display_name or quote.symbol,
            "rsi_5": rsi_5,
            "rsi_21": rsi_21,
            "rsi_63": rsi_63,
            "change_pct": round(float(quote.change_percent or 0.0), 4),
        })

    available = len(rows) >= 2
    return {
        "chart_key": "rsi_momentum_heatmap",
        "variant": "rsi_grid",
        "available": available,
        "reason_if_hidden": None if available else "Insufficient price history for RSI computation.",
        "title": "Momentum / RSI",
        "caption": "RSI(5), RSI(21), RSI(63) heatmap — red >65 overbought, green <35 oversold.",
        "series": rows,
        "annotations": [{"label": "overbought", "value": 65}, {"label": "oversold", "value": 35}],
        "meta": {},
        "email_dimensions": {"width": 1000, "height": 560},
    }


def _implied_move_spec(earnings_calendar: list[Any], market_data_service: Any) -> dict[str, Any]:
    """Options-implied move for upcoming earnings tickers (nearest ATM straddle / spot)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in (earnings_calendar or [])[:15]:
        sym = str(getattr(event, "symbol", "") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        try:
            import yfinance as yf
            ticker = yf.Ticker(sym)
            expirations = ticker.options
            if not expirations:
                continue
            # Pick the nearest expiry that is at or after the report date
            report_date = str(getattr(event, "report_date", "") or "")
            exp = expirations[0]
            for e in expirations[:4]:
                if e >= report_date:
                    exp = e
                    break
            chain = ticker.option_chain(exp)
            spot_info = ticker.fast_info
            spot = float(getattr(spot_info, "last_price", 0) or 0)
            if spot <= 0:
                continue
            # Nearest ATM call + put IV average
            calls = chain.calls
            puts = chain.puts
            calls = calls[(calls["strike"] - spot).abs().argsort()[:1]]
            puts = puts[(puts["strike"] - spot).abs().argsort()[:1]]
            if calls.empty or puts.empty:
                continue
            call_iv = float(calls["impliedVolatility"].iloc[0])
            put_iv = float(puts["impliedVolatility"].iloc[0])
            avg_iv = (call_iv + put_iv) / 2.0
            # Approximate 1-event implied move: IV * sqrt(1/252) * sqrt(days_to_expiry)
            # Simplified: use straddle price / spot
            call_ask = float(calls["ask"].iloc[0]) if not calls["ask"].isna().iloc[0] else float(calls["lastPrice"].iloc[0])
            put_ask = float(puts["ask"].iloc[0]) if not puts["ask"].isna().iloc[0] else float(puts["lastPrice"].iloc[0])
            implied_move_pct = round(((call_ask + put_ask) / spot) * 100.0, 2)
            rows.append({
                "symbol": sym,
                "name": getattr(event, "company_name", sym) or sym,
                "report_date": report_date,
                "implied_move_pct": implied_move_pct,
                "avg_iv": round(avg_iv * 100.0, 1),
                "expiry": exp,
                "relevance_tag": getattr(event, "relevance_tag", "") or "",
            })
        except Exception:
            continue

    rows.sort(key=lambda r: r["report_date"])
    available = len(rows) >= 1
    return {
        "chart_key": "implied_move_strip",
        "variant": "atm_straddle",
        "available": available,
        "reason_if_hidden": None if available else "No upcoming earnings with options data found.",
        "title": "Implied Earnings Moves",
        "caption": f"Options-implied ±move for {len(rows)} upcoming earnings ({', '.join(r['symbol'] for r in rows[:3])}{' …' if len(rows) > 3 else ''})." if rows else "No earnings implied-move data.",
        "series": rows,
        "annotations": [],
        "meta": {},
        "email_dimensions": {"width": 1000, "height": 480},
    }


def _limit_global_series(series: list[dict[str, Any]], *, limit: int = 7) -> list[dict[str, Any]]:
    if len(series) <= limit:
        return series
    primary = series[:2]
    seen = {row["symbol"] for row in primary}
    rest = sorted(
        [row for row in series[2:] if row["symbol"] not in seen],
        key=lambda row: abs(float(row.get("change_pct") or 0.0)),
        reverse=True,
    )
    return (primary + rest)[:limit]


def _global_read_line(series: list[dict[str, Any]]) -> str:
    if not series:
        return "Global leadership chart unavailable because index history is incomplete."
    ranked = sorted(series, key=lambda row: float((row.get("y_5d") or [100.0])[-1]), reverse=True)
    leader = ranked[0]
    laggard = ranked[-1]
    spread = float((leader.get("y_5d") or [100.0])[-1]) - float((laggard.get("y_5d") or [100.0])[-1])
    return f"{leader['name']} leads {laggard['name']} by {spread:.1f} rebased points over the 5D window."


def _impulse_read_line(points: list[dict[str, Any]]) -> str:
    if not points:
        return "Macro impulse strip unavailable because rates and commodity inputs are missing."
    driver = max(points, key=lambda row: abs(float(row.get("impulse") or 0.0)))
    unit = "bp" if driver.get("unit") == "bps" else "%"
    return f"{driver['name']} is the largest cross-asset impulse at {float(driver.get('impulse') or 0.0):+.2f}{unit}."


def _holdings_read_line(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Portfolio movers unavailable because holdings and watchlist quotes are missing."
    driver = max(rows, key=lambda row: abs(float(row.get("excess_pct") or 0.0)))
    return f"{driver['symbol']} is the biggest benchmark-relative mover at {float(driver.get('excess_pct') or 0.0):+.2f}% excess."


def _sector_read_line(points: list[dict[str, Any]]) -> str:
    if not points:
        return "Sector quadrant unavailable because sector exposure or ETF move inputs are missing."
    driver = max(points, key=lambda row: abs(float(row.get("y_change_pct") or 0.0)) + float(row.get("x_exposure") or 0.0) * 0.05)
    return f"{driver['name']} is the key sector outlier at {float(driver.get('x_exposure') or 0.0):.1f}% exposure and {float(driver.get('y_change_pct') or 0.0):+.2f}% move."


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


def _region_family(quote: QuoteData) -> str:
    label = (quote.display_name or quote.symbol).lower()
    if any(key in label for key in US_INDEX_KEYS):
        return "us"
    if any(key in label for key in EU_INDEX_KEYS):
        return "europe"
    if any(key in label for key in ASIA_INDEX_KEYS):
        return "asia"
    return "other"


def _as_bps(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * 100.0, 4)


def _confidence_label(metrics: dict[str, Any]) -> str:
    score = 0
    if metrics.get("total_indices"):
        score += 1
    if metrics.get("vix_level") is not None:
        score += 1
    if metrics.get("ten_y_level") is not None:
        score += 1
    if metrics.get("oil_delta_pct") is not None:
        score += 1
    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    return "low"
