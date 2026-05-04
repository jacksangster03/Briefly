"""Deterministic morning chart contract + promotion engine.

This module is the single source of truth for morning chart decisions.
Renderers consume these specs but do not decide visual hierarchy.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.processing.cleaners import truncate
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
CHART_SPEC_REQUIRED_KEYS = (
    "chart_key",
    "variant",
    "available",
    "priority",
    "reason_if_hidden",
    "title",
    "caption",
    "series",
    "annotations",
    "email_dimensions",
)

logger = get_logger("morning_charts")


def _feature_on(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw not in {"0", "false", "off", "no"}


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
    selected, selection_meta = _select_candidates(
        candidates,
        profile=profile,
        briefing=briefing,
        normalized=normalized,
        regime_tags=regime_tags,
    )
    contract_errors = validate_chart_contract({"charts": [item.spec for item in candidates], "selected": selected})

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
            "email_density_mode": _chart_density_mode(profile, briefing),
            "chart_stack_key": _stack_key_for_session_regime(briefing, normalized, regime_tags),
            "selected_charts": selection_meta.get("selected_charts", []),
            "suppressed_charts": selection_meta.get("suppressed_charts", []),
            "required_charts_missing": selection_meta.get("required_charts_missing", []),
            "data_confidence": _confidence_label(normalized),
            "contract_errors": contract_errors,
            **anomaly_meta,
        },
    }
    if selection_meta.get("required_charts_missing"):
        logger.warning(
            "Chart stack missing required coverage | session=%s mode=%s stack=%s tags=%s selected=%s missing=%s",
            briefing.session_key,
            _chart_density_mode(profile, briefing),
            _stack_key_for_session_regime(briefing, normalized, regime_tags),
            regime_tags,
            selection_meta.get("selected_charts"),
            selection_meta.get("required_charts_missing"),
        )
    logger.info(
        "Chart stack | session=%s mode=%s stack=%s tags=%s selected=%s suppressed=%s",
        briefing.session_key,
        _chart_density_mode(profile, briefing),
        _stack_key_for_session_regime(briefing, normalized, regime_tags),
        regime_tags,
        selection_meta.get("selected_charts"),
        selection_meta.get("suppressed_charts"),
    )
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


def validate_chart_contract(bundle: dict[str, Any]) -> list[str]:
    """Validate deterministic chart contract shape.

    This is a guardrail only: renderers still skip unavailable charts.
    """
    errors: list[str] = []
    charts = list(bundle.get("charts") or [])
    selected = [str(item.get("chart_key") or "") for item in (bundle.get("selected") or [])]
    seen_keys: set[str] = set()
    for idx, chart in enumerate(charts):
        key = str(chart.get("chart_key") or "")
        if not key:
            errors.append(f"charts[{idx}] missing chart_key")
            continue
        if key in seen_keys:
            errors.append(f"duplicate chart_key: {key}")
        seen_keys.add(key)
        for req in CHART_SPEC_REQUIRED_KEYS:
            if req not in chart:
                errors.append(f"{key} missing required field '{req}'")
        if chart.get("available") is False and not chart.get("reason_if_hidden"):
            errors.append(f"{key} unavailable without reason_if_hidden")
    for key in selected:
        if key and key not in seen_keys:
            errors.append(f"selected chart missing from charts list: {key}")
    return errors


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
    if oil_delta is not None and abs(float(oil_delta)) >= 1.2 and vix_delta is not None and float(vix_delta) >= 1.0:
        tags.append("geo_energy")
    if abs(us - eu) >= 0.9:
        tags.append("regional_split")
    if abs(float(metrics.get("small_vs_large") or 0.0)) >= 0.6:
        tags.append("breadth_divergence")
    # Keep volatility-watch explicit so required chart gating is tag-driven.
    if vix_level is not None and (float(vix_level) >= 20.0 or (float(vix_level) >= 18.0 and float(vix_delta or 0.0) >= 3.0)):
        tags.append("volatility_watch")
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

    if _feature_on("FEATURE_REGIONAL_DIVERGENCE_SCORE", True):
        specs.append(
            ChartCandidate(
                chart_key="regional_divergence_score",
                category="support",
                priority=0.78 + (0.09 if "regional_split" in regime_tags else 0.0),
                spec=_regional_divergence_score_spec(normalized),
                reason="Regional spread score surfaces where dispersion is widest.",
            )
        )

    specs.append(
        ChartCandidate(
            chart_key="watchlist_movers_card",
            category="support",
            priority=0.7 + (0.1 if briefing.watchlist_quotes else 0.0),
            spec=_watchlist_movers_spec(briefing),
            reason="Watchlist leaders/laggards show where live single-name risk is concentrated.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="setup_confirmation_card",
            category="support",
            priority=0.72 + (0.08 if briefing.session_key in {"us_pre_open", "us_intraday_risk", "into_close"} else 0.0),
            spec=_setup_confirmation_spec(briefing, normalized, regime_tags),
            reason="Setup confirmation card tracks whether the morning thesis is confirming or fading.",
        )
    )

    specs.append(
        ChartCandidate(
            chart_key="what_changed_card",
            category="support",
            priority=0.74 + (0.08 if briefing.session_key in {"europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"} else 0.0),
            spec=_what_changed_spec(briefing),
            reason="What changed card highlights session deltas since the prior comparable snapshot.",
        )
    )

    if _feature_on("FEATURE_GEO_CONFIRMATION_LADDER", True):
        specs.append(
            ChartCandidate(
                chart_key="geo_confirmation_ladder",
                category="micro",
                priority=0.71 + (0.10 if ("oil_shock" in regime_tags or "defensive" in regime_tags) else 0.0),
                spec=_geo_confirmation_ladder_spec(briefing, normalized),
                reason="Geo confirmation ladder separates stress signals from non-confirmation.",
            )
        )

    if _feature_on("FEATURE_OIL_TRANSMISSION_CARD", True):
        specs.append(
            ChartCandidate(
                chart_key="oil_transmission_card",
                category="micro",
                priority=0.69 + (0.10 if ("oil_shock" in regime_tags or abs(float(normalized.get("oil_delta_pct") or 0.0)) >= 1.0) else 0.0),
                spec=_oil_transmission_card_spec(briefing, normalized),
                reason="Oil transmission card distinguishes inflation stress from broad risk-off.",
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

    if _feature_on("FEATURE_VIX_RISK_CARD", True):
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

    if _feature_on("FEATURE_YIELD_CURVE_CARD", True):
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


def _select_candidates(
    candidates: list[ChartCandidate],
    *,
    profile: UserProfile,
    briefing: MorningBriefing,
    normalized: dict[str, Any],
    regime_tags: list[str],
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    by_key = {item.chart_key: item for item in candidates}
    available = [item for item in candidates if bool(item.spec.get("available"))]
    if not available:
        fallback = next(iter(by_key.values()), None)
        selected = [{"chart_key": fallback.chart_key, "role": "hero", "reason": "Fallback due to missing availability."}] if fallback else []
        meta = {"selected_charts": [row["chart_key"] for row in selected], "suppressed_charts": [], "required_charts_missing": []}
        return selected, meta

    if not _feature_on("FEATURE_DYNAMIC_CHART_STACK", True):
        selected: list[dict[str, str]] = []
        hero = next((item for item in available if item.category == "hero"), None) or available[0]
        selected.append({"chart_key": hero.chart_key, "role": "hero", "reason": hero.reason})
        for item in [row for row in available if row.category == "support" and row.chart_key != hero.chart_key][:2]:
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
        meta = {"selected_charts": [row["chart_key"] for row in selected], "suppressed_charts": [], "required_charts_missing": []}
        return selected, meta

    density_mode = _chart_density_mode(profile, briefing)
    target_charts = 8
    min_charts = 7
    if density_mode == "desk":
        target_charts = 5
        min_charts = 4
    elif density_mode == "medium":
        target_charts = 6
        min_charts = 5
    stack_key = _stack_key_for_session_regime(briefing, normalized, regime_tags)
    desired = _stack_policy(stack_key, briefing)
    required_groups = _required_chart_groups(briefing, regime_tags, normalized)
    hard_groups = _hard_required_groups(briefing, regime_tags)

    selection_keys: list[str] = []
    available_keys = {item.chart_key for item in available}
    suppressed: list[str] = []
    for key in desired:
        if key in available_keys:
            # Hide low-value event chart in desk mode when catalyst label is unavailable.
            if density_mode == "desk" and key == "event_linked_annotated_trend":
                spec = by_key.get(key).spec if by_key.get(key) else {}
                event_name = str((spec.get("meta") or {}).get("event_name") or "").strip().lower()
                if not event_name or "unavailable" in event_name:
                    suppressed.append(f"{key}:desk_mode_unavailable_event_label")
                    continue
            if key not in selection_keys:
                selection_keys.append(key)
        if len(selection_keys) >= target_charts:
            break

    required_selected: list[str] = []
    for group in required_groups:
        selected_for_group = next((key for key in selection_keys if key in group), None)
        if selected_for_group is not None:
            required_selected.append(selected_for_group)
            continue
        candidate = next((key for key in group if key in available_keys), None)
        if candidate is None:
            continue
        selection_keys.append(candidate)
        required_selected.append(candidate)

    portfolio_keys = [key for key in _portfolio_required_keys(briefing) if key in available_keys]
    for must_key in portfolio_keys:
        if must_key not in selection_keys:
            selection_keys.append(must_key)

    # If mandatory coverage exceeds desk cap, drop soft required groups first.
    if density_mode == "desk" and len(selection_keys) > target_charts:
        hard_group_keys = {key for group in hard_groups for key in group}
        soft_required_keys = [
            key for group in required_groups
            if group not in hard_groups
            for key in group
            if key in selection_keys and key not in hard_group_keys
        ]
        for key in list(dict.fromkeys(soft_required_keys)):
            if len(selection_keys) <= target_charts:
                break
            try:
                selection_keys.remove(key)
                suppressed.append(f"{key}:desk_overflow_soft_required")
            except ValueError:
                continue

    # Fill remaining capacity with highest-priority available charts.
    if len(selection_keys) < target_charts:
        for item in sorted(available, key=lambda row: row.priority, reverse=True):
            if item.chart_key not in selection_keys:
                selection_keys.append(item.chart_key)
            if len(selection_keys) >= target_charts:
                break
    protected_keys: set[str] = set(portfolio_keys)
    for group in required_groups:
        # Protect one selected representative per group.
        representative = next((key for key in selection_keys if key in group), None)
        if representative:
            protected_keys.add(representative)
    if density_mode == "desk" and len(selection_keys) > target_charts:
        # Desk mode hard cap: prioritize session-critical + live-risk + portfolio P&L.
        hard_representatives: list[str] = []
        for group in required_groups:
            if group not in hard_groups:
                continue
            rep = next((key for key in selection_keys if key in group), None)
            if rep and rep not in hard_representatives:
                hard_representatives.append(rep)
        for key in portfolio_keys:
            if key not in hard_representatives:
                hard_representatives.append(key)
        priority_order = [
            "what_changed_card",
            "setup_confirmation_card",
            "watchlist_movers_card",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "regional_divergence_score",
            "global_relative_performance",
            "yield_curve_shape",
            "cross_asset_impulse_strip",
            "volatility_regime_card",
            "breadth_leadership_panel",
            "geo_confirmation_ladder",
            "oil_transmission_card",
        ]
        selected_ranked = hard_representatives + [key for key in priority_order if key in selection_keys and key not in hard_representatives]
        # Keep deterministic fallback ordering for any unranked keys.
        selected_ranked.extend([key for key in selection_keys if key not in selected_ranked])
        keep = selected_ranked[:target_charts]
        for key in selection_keys:
            if key not in keep:
                suppressed.append(f"{key}:desk_priority_trim")
        selection_keys = keep
        protected_keys = set(selection_keys)
    while len(selection_keys) > target_charts:
        removed = False
        for idx in range(len(selection_keys) - 1, -1, -1):
            key = selection_keys[idx]
            if key in protected_keys:
                continue
            selection_keys.pop(idx)
            suppressed.append(f"{key}:density_cap")
            removed = True
            break
        if not removed:
            break

    if len(selection_keys) < min_charts:
        for item in sorted(available, key=lambda row: row.priority, reverse=True):
            if item.chart_key not in selection_keys:
                selection_keys.append(item.chart_key)
            if len(selection_keys) >= min_charts:
                break

    # Final pass: avoid silently dropping required tag coverage when an available chart exists.
    for group in required_groups:
        if any(key in selection_keys for key in group):
            continue
        fallback = next((key for key in group if key in available_keys), None)
        if fallback is None:
            continue
        if fallback not in selection_keys:
            if len(selection_keys) >= target_charts:
                removable = next((key for key in reversed(selection_keys) if key not in protected_keys), None)
                if removable is not None:
                    selection_keys.remove(removable)
                    suppressed.append(f"{removable}:replaced_for_required_coverage")
            selection_keys.append(fallback)
            suppressed.append(f"{fallback}:forced_required_coverage")

    roles: list[dict[str, str]] = []
    micro_counter = 0
    for idx, key in enumerate(selection_keys):
        item = by_key.get(key)
        if not item:
            continue
        if idx == 0:
            role = "hero"
        elif item.category == "micro":
            micro_counter += 1
            role = f"micro_{micro_counter}"
        elif item.category == "portfolio":
            role = "optional_portfolio"
        elif item.category == "event":
            role = "optional_event"
        else:
            role = "support"
        roles.append({"chart_key": key, "role": role, "reason": item.reason})

    selected_set = set(selection_keys)
    required_missing = [
        "/".join(group)
        for group in required_groups
        if not any(key in selected_set for key in group)
    ]
    meta = {
        "selected_charts": selection_keys,
        "suppressed_charts": suppressed,
        "required_charts_missing": required_missing,
    }
    return roles, meta


def _chart_density_mode(profile: UserProfile, briefing: MorningBriefing) -> str:
    if not _feature_on("FEATURE_EMAIL_DENSITY_MODE", True):
        return "full"
    raw_pref = str(profile.delivery.get("email_density_mode", "auto")).strip().lower()
    session_key = (briefing.session_key or "morning").lower()
    if raw_pref in {"desk", "full", "medium"}:
        return raw_pref
    # Auto policy by session.
    if session_key == "morning":
        return "full"
    if session_key == "closing_wrap":
        return "medium"
    return "desk"


def _stack_key_for_session_regime(
    briefing: MorningBriefing,
    normalized: dict[str, Any],
    regime_tags: list[str],
) -> str:
    session_key = (briefing.session_key or "morning").lower()
    tags = {str(t).lower() for t in regime_tags}
    oil_move = abs(float(normalized.get("oil_delta_pct") or 0.0))
    ten_y = abs(float(normalized.get("ten_y_change") or 0.0))
    dispersion = float(normalized.get("dispersion") or 0.0)
    vix_delta = float(normalized.get("vix_delta_pct") or 0.0)
    if session_key == "europe_midday":
        return "europe_midday"
    if session_key == "us_pre_open":
        return "us_pre_open"
    if session_key == "us_intraday_risk":
        return "us_intraday_risk"
    if session_key == "into_close":
        return "into_close"
    if session_key == "closing_wrap":
        return "closing_wrap"
    if "oil_shock" in tags or (oil_move >= 2.0 and vix_delta >= 1.0):
        return "energy_geo"
    if "rates_led" in tags or ten_y >= 0.025:
        return "rates_repricing"
    if briefing.earnings_calendar and any(tok in " ".join((evt.title or "").lower() for evt in briefing.top_themes[:6]) for tok in ("earnings", "guidance", "ai", "semiconductor")):
        return "earnings_tech"
    if "regional_split" in tags or "breadth_divergence" in tags or dispersion >= 0.9:
        return "mixed_regional_split"
    return "balanced"


def _stack_policy(stack_key: str, briefing: MorningBriefing) -> list[str]:
    policies: dict[str, list[str]] = {
        "europe_midday": [
            "what_changed_card",
            "regional_divergence_score",
            "cross_asset_impulse_strip",
            "volatility_regime_card",
            "pnl_attribution_waterfall",
        ],
        "us_pre_open": [
            "what_changed_card",
            "setup_confirmation_card",
            "regional_divergence_score",
            "watchlist_movers_card",
            "yield_curve_shape",
            "cross_asset_impulse_strip",
            "volatility_regime_card",
            "earnings_relevance_strip",
            "pnl_attribution_waterfall",
        ],
        "us_intraday_risk": [
            "what_changed_card",
            "setup_confirmation_card",
            "volatility_regime_card",
            "watchlist_movers_card",
            "cross_asset_impulse_strip",
            "pnl_attribution_waterfall",
        ],
        "into_close": [
            "what_changed_card",
            "breadth_leadership_panel",
            "volatility_regime_card",
            "watchlist_movers_card",
            "pnl_attribution_waterfall",
            "cross_asset_impulse_strip",
        ],
        "closing_wrap": [
            "regional_divergence_score",
            "breadth_leadership_panel",
            "cross_asset_impulse_strip",
            "watchlist_movers_card",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "earnings_relevance_strip",
        ],
        "energy_geo": [
            "cross_asset_impulse_strip",
            "geo_confirmation_ladder",
            "oil_transmission_card",
            "regional_divergence_score",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "volatility_regime_card",
            "global_relative_performance",
        ],
        "rates_repricing": [
            "global_relative_performance",
            "regional_divergence_score",
            "yield_curve_shape",
            "cross_asset_impulse_strip",
            "volatility_regime_card",
            "breadth_leadership_panel",
            "pnl_attribution_waterfall",
            "rates_curve_micro_panel",
            "portfolio_concentration_risk_card",
            "regional_divergence_score",
        ],
        "earnings_tech": [
            "global_relative_performance",
            "holdings_excess_performance",
            "sector_exposure_quadrant",
            "earnings_relevance_strip",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "implied_move_strip",
            "breadth_leadership_panel",
        ],
        "mixed_regional_split": [
            "regional_divergence_score",
            "global_relative_performance",
            "breadth_leadership_panel",
            "volatility_regime_card",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "cross_asset_impulse_strip",
            "rates_curve_micro_panel",
        ],
        "balanced": [
            "global_relative_performance",
            "regional_divergence_score",
            "watchlist_movers_card",
            "cross_asset_impulse_strip",
            "breadth_leadership_panel",
            "pnl_attribution_waterfall",
            "portfolio_concentration_risk_card",
            "regional_divergence_score",
            "volatility_regime_card",
            "rates_curve_micro_panel",
        ],
    }
    policy = list(policies.get(stack_key, policies["balanced"]))
    # Intraday sessions should avoid static concentration unless relevant.
    if (briefing.session_key or "").lower() in {"us_intraday_risk", "into_close"}:
        concentration_relevant = any(
            abs(float(q.change_percent or 0.0)) >= 3.0
            for q in (briefing.portfolio_quotes or [])
        )
        if not concentration_relevant:
            policy = [key for key in policy if key != "portfolio_concentration_risk_card"]
    return policy


def _portfolio_required_keys(briefing: MorningBriefing) -> tuple[str, ...]:
    session_key = (briefing.session_key or "morning").lower()
    if session_key in {"us_intraday_risk", "into_close"}:
        return ("pnl_attribution_waterfall",)
    return ("pnl_attribution_waterfall", "portfolio_concentration_risk_card")


def _required_chart_groups(
    briefing: MorningBriefing,
    regime_tags: list[str],
    normalized: dict[str, Any],
) -> list[tuple[str, ...]]:
    tags = {str(tag).lower() for tag in regime_tags}
    required: list[tuple[str, ...]] = []
    session_key = (briefing.session_key or "morning").lower()

    if session_key in {"us_pre_open", "us_intraday_risk", "into_close"}:
        required.append(("watchlist_movers_card",))
    if session_key in {"us_pre_open", "us_intraday_risk", "into_close", "europe_midday", "closing_wrap"}:
        required.append(("what_changed_card", "setup_confirmation_card"))
    if session_key in {"us_intraday_risk", "into_close"}:
        required.append(("setup_confirmation_card",))

    if "regional_split" in tags:
        required.append(("regional_divergence_score", "global_relative_performance"))
    if "breadth_divergence" in tags:
        required.append(("breadth_leadership_panel",))
    if "rates_led" in tags:
        required.append(("yield_curve_shape", "rates_curve_micro_panel"))
        required.append(("cross_asset_impulse_strip",))
    if "oil_shock" in tags or "geo_energy" in tags:
        required.append(("geo_confirmation_ladder", "oil_transmission_card"))
    if "volatility_watch" in tags:
        required.append(("volatility_regime_card",))
    return required


def _hard_required_groups(briefing: MorningBriefing, regime_tags: list[str]) -> set[tuple[str, ...]]:
    tags = {str(tag).lower() for tag in regime_tags}
    hard: set[tuple[str, ...]] = set()
    session_key = (briefing.session_key or "morning").lower()
    if session_key in {"us_pre_open", "us_intraday_risk", "into_close"}:
        hard.add(("watchlist_movers_card",))
    if session_key in {"us_intraday_risk", "into_close"}:
        hard.add(("setup_confirmation_card",))
    if "regional_split" in tags:
        hard.add(("regional_divergence_score", "global_relative_performance"))
    if "breadth_divergence" in tags:
        hard.add(("breadth_leadership_panel",))
    if "rates_led" in tags:
        hard.add(("yield_curve_shape", "rates_curve_micro_panel"))
        hard.add(("cross_asset_impulse_strip",))
    if "oil_shock" in tags or "geo_energy" in tags:
        hard.add(("geo_confirmation_ladder", "oil_transmission_card"))
    if "volatility_watch" in tags:
        hard.add(("volatility_regime_card",))
    return hard


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
    event_label = _event_label_for_trend(briefing, focus_symbol)
    return {
        "chart_key": "event_linked_annotated_trend",
        "variant": "30d",
        "available": True,
        "reason_if_hidden": None,
        "title": f"{focus_symbol} Event-Linked Trend",
        "caption": f"{focus_symbol} trend is framed around the latest deterministic catalyst window.",
        "series": [{"name": focus_symbol, "symbol": focus_symbol, "x": x, "y": y}],
        "annotations": [
            {"label": "event_window_start", "x": marker, "event_name": event_label},
            {"label": "latest", "x": len(path) - 1},
        ],
        "meta": {"symbol": focus_symbol, "event_name": event_label},
        "email_dimensions": {"width": 1000, "height": 580},
    }


def _regional_divergence_score_spec(metrics: dict[str, Any]) -> dict[str, Any]:
    us = float(metrics.get("us_avg") or 0.0)
    eu = float(metrics.get("eu_avg") or 0.0)
    asia = float(metrics.get("asia_avg") or 0.0)
    rows = [
        {"name": "US", "value": round(us, 3)},
        {"name": "Europe", "value": round(eu, 3)},
        {"name": "Asia", "value": round(asia, 3)},
    ]
    spread = round(max(us, eu, asia) - min(us, eu, asia), 3)
    available = any(abs(float(row["value"])) > 0 for row in rows)
    return {
        "chart_key": "regional_divergence_score",
        "variant": "regional_bar_spread",
        "available": available,
        "reason_if_hidden": None if available else "Regional benchmark moves unavailable.",
        "title": "Regional Divergence",
        "caption": f"Regional spread is {spread:.2f} pts between strongest and weakest sessions.",
        "series": rows,
        "annotations": [{"label": "spread", "value": spread}],
        "meta": {"spread": spread},
        "email_dimensions": {"width": 900, "height": 460},
    }


def _watchlist_movers_spec(briefing: MorningBriefing) -> dict[str, Any]:
    quotes = list(briefing.watchlist_quotes or briefing.portfolio_quotes or [])
    ranked = sorted(
        [q for q in quotes if q.current_price is not None],
        key=lambda q: float(q.change_percent or 0.0),
        reverse=True,
    )
    leaders = ranked[:3]
    laggards = list(reversed(ranked[-3:])) if len(ranked) > 3 else []
    rows = []
    for row in leaders + laggards:
        rows.append(
            {
                "name": (row.display_name or row.symbol or "").strip()[:28],
                "symbol": row.symbol,
                "value": round(float(row.change_percent or 0.0), 3),
            }
        )
    available = len(rows) >= 2
    if leaders and laggards:
        caption = (
            f"Leaders: {leaders[0].symbol} {float(leaders[0].change_percent or 0.0):+.2f}% | "
            f"Laggards: {laggards[0].symbol} {float(laggards[0].change_percent or 0.0):+.2f}%."
        )
    else:
        caption = "Watchlist mover context is limited in this cycle."
    return {
        "chart_key": "watchlist_movers_card",
        "variant": "leaders_laggards",
        "available": available,
        "reason_if_hidden": None if available else "Watchlist quote set too small.",
        "title": "Watchlist Movers",
        "caption": caption,
        "series": rows,
        "annotations": [],
        "meta": {"leader_count": len(leaders), "laggard_count": len(laggards)},
        "email_dimensions": {"width": 900, "height": 460},
    }


def _setup_confirmation_spec(
    briefing: MorningBriefing,
    metrics: dict[str, Any],
    regime_tags: list[str],
) -> dict[str, Any]:
    us = float(metrics.get("us_avg") or 0.0)
    eu = float(metrics.get("eu_avg") or 0.0)
    asia = float(metrics.get("asia_avg") or 0.0)
    breadth = float(metrics.get("breadth") or 0.0)
    ten_y_change = float(metrics.get("ten_y_change") or 0.0)
    nasdaq = _avg_change(briefing.market_setup.index_quotes, ("nasdaq", "ixic"))
    spx = _avg_change(briefing.market_setup.index_quotes, ("s&p", "spx"))

    def _status(value: float, *, pos_threshold: float = 0.2, neg_threshold: float = -0.2) -> str:
        if value >= pos_threshold:
            return "confirmed"
        if value <= neg_threshold:
            return "faded"
        return "mixed"

    rows = [
        {"name": "US confirmation", "state": _status(us), "value": round(us, 3)},
        {"name": "Europe weakness", "state": "confirmed" if eu <= -0.3 else "faded", "value": round(eu, 3)},
        {"name": "Rates pressure", "state": "confirmed" if ten_y_change >= 0.025 else "mixed" if ten_y_change > 0 else "faded", "value": round(ten_y_change, 4)},
        {"name": "Tech cushion", "state": "holding" if nasdaq >= spx else "failing", "value": round(nasdaq - spx, 3)},
        {"name": "Breadth", "state": "confirming" if breadth >= 0.6 else "diverging" if breadth <= 0.4 else "mixed", "value": round(breadth * 100.0, 2)},
    ]
    tag_label = ", ".join(regime_tags[:3]) if regime_tags else "mixed"
    return {
        "chart_key": "setup_confirmation_card",
        "variant": "status_matrix",
        "available": True,
        "reason_if_hidden": None,
        "title": "Setup Confirmation",
        "caption": f"Session setup check across US/Europe/Asia, rates, and breadth (tags: {tag_label}).",
        "series": rows,
        "annotations": [],
        "meta": {"tag_label": tag_label},
        "email_dimensions": {"width": 900, "height": 460},
    }


def _what_changed_spec(briefing: MorningBriefing) -> dict[str, Any]:
    lines = [str(line).strip() for line in (briefing.what_changed_lines or []) if str(line).strip()]
    available = bool(lines) and not lines[0].lower().startswith("no prior comparable snapshot")
    rows = [{"name": line[:74], "value": idx + 1} for idx, line in enumerate(lines[:6])]
    return {
        "chart_key": "what_changed_card",
        "variant": "delta_summary",
        "available": available,
        "reason_if_hidden": None if available else "No prior comparable snapshot available.",
        "title": "What Changed",
        "caption": lines[0] if lines else "No prior comparable snapshot available.",
        "series": rows,
        "annotations": [],
        "meta": {"line_count": len(lines)},
        "email_dimensions": {"width": 900, "height": 460},
    }


def _geo_confirmation_ladder_spec(briefing: MorningBriefing, metrics: dict[str, Any]) -> dict[str, Any]:
    oil = float(metrics.get("oil_delta_pct") or 0.0)
    vix_delta = float(metrics.get("vix_delta_pct") or 0.0)
    gold = float(metrics.get("gold_delta_pct") or 0.0)
    us = float(metrics.get("us_avg") or 0.0)
    eu = float(metrics.get("eu_avg") or 0.0)
    asia = float(metrics.get("asia_avg") or 0.0)
    equity_confirm = (us + eu + asia) / 3.0 <= -0.4
    oil_confirm = oil >= 1.0
    vix_confirm = vix_delta >= 1.5
    haven_confirm = gold >= 0.3
    geo_level = str(briefing.geo_risk_level or "n/a")

    rows = [
        {"name": "Oil", "state": "YES" if oil_confirm else "MILD" if oil > 0 else "NO", "value": oil},
        {"name": "VIX", "state": "YES" if vix_confirm else "NO", "value": vix_delta},
        {"name": "Gold/Haven", "state": "YES" if haven_confirm else "NO", "value": gold},
        {"name": "Equities", "state": "YES" if equity_confirm else "PARTIAL", "value": (us + eu + asia) / 3.0},
    ]
    if oil_confirm and vix_confirm and haven_confirm and equity_confirm:
        conclusion = "broad stress confirmed"
    elif oil_confirm or vix_confirm:
        conclusion = "market-contained stress"
    else:
        conclusion = "limited confirmation"
    return {
        "chart_key": "geo_confirmation_ladder",
        "variant": "confirmations",
        "available": True,
        "reason_if_hidden": None,
        "title": "Geo Confirmation Ladder",
        "caption": (
            f"Oil {'confirms' if oil_confirm else 'is mild'}, VIX {'confirms' if vix_confirm else 'does not confirm'}, "
            f"gold {'confirms' if haven_confirm else 'does not confirm'} haven demand; final geo label {geo_level} ({conclusion})."
        ),
        "series": rows,
        "annotations": [{"label": "conclusion", "value": conclusion}],
        "meta": {"geo_level": geo_level, "conclusion": conclusion},
        "email_dimensions": {"width": 900, "height": 460},
    }


def _oil_transmission_card_spec(briefing: MorningBriefing, metrics: dict[str, Any]) -> dict[str, Any]:
    oil_row = _find_quote(briefing.market_setup.macro_quotes, OIL_KEYS)
    brent_row = _find_quote(briefing.market_setup.macro_quotes, ("brent", "bz=F", "co1:com"))
    energy_etf = next((row for row in briefing.market_setup.market_breadth if str(row.symbol).upper() == "XLE"), None)
    vix = float(metrics.get("vix_delta_pct") or 0.0)
    gold = float(metrics.get("gold_delta_pct") or 0.0)
    oil = float(oil_row.change_percent or 0.0) if oil_row else 0.0
    brent = float(brent_row.change_percent or 0.0) if brent_row else 0.0
    xle = float(energy_etf.change_percent or 0.0) if energy_etf else 0.0

    if oil > 1.0 and vix > 1.0 and gold <= 0.2:
        verdict = "inflation stress > haven panic"
    elif oil > 1.0 and xle > 0:
        verdict = "energy leadership confirms supply stress"
    elif oil <= 0 and vix <= 0:
        verdict = "energy pressure easing"
    else:
        verdict = "mixed transmission"

    rows = [
        {"name": "WTI", "value": round(oil, 3)},
        {"name": "Brent", "value": round(brent, 3)},
        {"name": "XLE", "value": round(xle, 3)},
        {"name": "VIX", "value": round(vix, 3)},
        {"name": "Gold", "value": round(gold, 3)},
    ]
    return {
        "chart_key": "oil_transmission_card",
        "variant": "macro_transmission",
        "available": True,
        "reason_if_hidden": None,
        "title": "Oil Transmission",
        "caption": (
            f"WTI {oil:+.2f}% / Brent {brent:+.2f}% with XLE {xle:+.2f}%, VIX {vix:+.2f}% and gold {gold:+.2f}%: {verdict}."
        ),
        "series": rows,
        "annotations": [{"label": "verdict", "value": verdict}],
        "meta": {"verdict": verdict},
        "email_dimensions": {"width": 900, "height": 460},
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
        regime = "watchful"
    elif vix_level < 30:
        regime = "stress"
    else:
        regime = "shock"
    available = vix_level is not None
    return {
        "chart_key": "volatility_regime_card",
        "variant": "vix_regime",
        "available": available,
        "reason_if_hidden": None if available else "VIX quote unavailable.",
        "title": "Volatility Regime",
        "caption": (
            f"VIX is {float(vix_level):.2f}, {float(vix_delta or 0.0):+.2f}%, in the {regime} zone."
            if available
            else "VIX quote unavailable."
        ),
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

    if top5 >= 75.0 or largest >= 15.0:
        caption = "High top-weight concentration means daily P&L can be dominated by a small number of sleeves."
    else:
        caption = "Portfolio concentration is moderate; monitor whether returns are broad or sleeve-driven."
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
    """Yield curve shape from deterministic macro series only (no yfinance fallback)."""
    TENOR_ORDER = {"DGS2": 2, "DGS5": 5, "DGS10": 10, "DGS30": 30}
    today_rows: list[dict[str, Any]] = []
    for point in yield_curve_points:
        tenor = TENOR_ORDER.get(point.series_id)
        if tenor is None or point.value is None:
            continue
        prev_val = float(point.previous_value) if point.previous_value is not None else None
        today_rows.append({
            "series_id": point.series_id,
            "tenor": tenor,
            "today": round(float(point.value), 4),
            "week_ago": round(prev_val, 4) if prev_val is not None else None,
            "change_bps": round((float(point.value) - prev_val) * 100.0, 1) if prev_val is not None else None,
        })
    today_rows.sort(key=lambda r: r["tenor"])
    available = len(today_rows) >= 2
    inversion = (today_rows[0]["today"] > today_rows[-1]["today"]) if available else False
    shape = "inverted" if inversion else "normal"
    row_map = {int(row["tenor"]): row for row in today_rows}
    two = row_map.get(2, {}).get("today")
    ten = row_map.get(10, {}).get("today")
    thirty = row_map.get(30, {}).get("today")
    spread_10_2 = ((ten - two) * 100.0) if (ten is not None and two is not None) else None
    if available and two is not None and ten is not None:
        read = (
            f"{'Inverted' if inversion else 'Normal'} curve: 2Y {two:.2f}%, 10Y {ten:.2f}%"
            + (f", 30Y {thirty:.2f}%" if thirty is not None else "")
        )
        if spread_10_2 is not None:
            read += f"; 10Y-2Y spread {spread_10_2:+.0f} bp."
    else:
        read = "Yield curve data unavailable."
    return {
        "chart_key": "yield_curve_shape",
        "variant": "curve_today_vs_week",
        "available": available,
        "reason_if_hidden": None if available else "Yield curve data unavailable.",
        "title": "Yield Curve Shape",
        "caption": read,
        "series": today_rows,
        "annotations": [{"label": "inversion", "value": inversion}],
        "meta": {"shape": shape, "spread_10_2_bps": spread_10_2, "two": two, "ten": ten, "thirty": thirty},
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
    top_pos = max(bars, key=lambda row: float(row.get("contribution") or 0.0), default=None)
    top_neg = min(bars, key=lambda row: float(row.get("contribution") or 0.0), default=None)
    lens_hint = ""
    if top_pos and top_neg:
        if total_contrib < 0:
            lens_hint = (
                f" Main drag: {top_neg['symbol']} {float(top_neg['contribution']):+.2f}%; "
                f"largest positive offset: {top_pos['symbol']} {float(top_pos['contribution']):+.2f}%."
            )
        elif total_contrib > 0:
            lens_hint = (
                f" Main contributor: {top_pos['symbol']} {float(top_pos['contribution']):+.2f}%; "
                f"largest drag: {top_neg['symbol']} {float(top_neg['contribution']):+.2f}%."
            )
        else:
            lens_hint = (
                f" Offsetting sleeves: {top_pos['symbol']} {float(top_pos['contribution']):+.2f}% "
                f"vs {top_neg['symbol']} {float(top_neg['contribution']):+.2f}%."
            )
    return {
        "chart_key": "pnl_attribution_waterfall",
        "variant": "daily_contribution",
        "available": available,
        "reason_if_hidden": None if available else "Insufficient weighted holdings for P&L attribution.",
        "title": "P&L Attribution",
        "caption": f"Portfolio daily contribution: {total_contrib:+.2f}% weighted total across {len(bars)} positions.{lens_hint}",
        "series": bars,
        "annotations": [{"label": "total", "value": round(total_contrib, 4)}],
        "meta": {"total_contribution": round(total_contrib, 4)},
        "email_dimensions": {"width": 1000, "height": 560},
    }


def _event_label_for_trend(briefing: MorningBriefing, focus_symbol: str) -> str:
    candidates = list(briefing.portfolio_focus or []) + list(briefing.top_themes or []) + list(briefing.global_news or [])
    focus_upper = (focus_symbol or "").upper()
    for evt in candidates:
        text = f"{evt.title} {evt.summary}".strip()
        if not text:
            continue
        if focus_upper and focus_upper in [t.upper() for t in (evt.tickers or [])]:
            return truncate(text, 56)
    for evt in candidates:
        text = f"{evt.title} {evt.summary}".strip()
        if not text:
            continue
        return truncate(text, 56)
    return "latest catalyst unavailable"


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
    positives = [row for row in points if float(row.get("impulse") or 0.0) >= 0.0]
    negatives = [row for row in points if float(row.get("impulse") or 0.0) < 0.0]
    strongest_pos = max(positives, key=lambda row: float(row.get("impulse") or 0.0), default=None)
    strongest_neg = min(negatives, key=lambda row: float(row.get("impulse") or 0.0), default=None)

    def _fmt(row: dict[str, Any] | None) -> str:
        if row is None:
            return "none"
        unit = "bp" if row.get("unit") == "bps" else "%"
        return f"{row['name']} {float(row.get('impulse') or 0.0):+.2f}{unit}"

    if strongest_pos and strongest_neg:
        return (
            f"On a normalised impulse basis, upside is {_fmt(strongest_pos)} and downside is {_fmt(strongest_neg)}. "
            "Units are normalised before comparison."
        )
    driver = max(points, key=lambda row: abs(float(row.get("impulse") or 0.0)))
    return f"On a normalised impulse basis, the largest move is {_fmt(driver)}; the rest of the strip is comparatively muted."


def _holdings_read_line(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Portfolio movers unavailable because holdings and watchlist quotes are missing."
    ranked = sorted(rows, key=lambda row: float(row.get("excess_pct") or 0.0), reverse=True)
    leader = ranked[0]
    laggard = ranked[-1]
    spread = float(leader.get("excess_pct") or 0.0) - float(laggard.get("excess_pct") or 0.0)
    return (
        f"{leader['symbol']} leads at {float(leader.get('excess_pct') or 0.0):+.2f}% excess; "
        f"{laggard['symbol']} lags at {float(laggard.get('excess_pct') or 0.0):+.2f}% (dispersion {spread:.2f} pts)."
    )


def _sector_read_line(points: list[dict[str, Any]]) -> str:
    if not points:
        return "Sector quadrant unavailable because sector exposure or ETF move inputs are missing."
    driver = max(points, key=lambda row: abs(float(row.get("y_change_pct") or 0.0)) + float(row.get("x_exposure") or 0.0) * 0.05)
    direction = "winner" if float(driver.get("y_change_pct") or 0.0) >= 0 else "laggard"
    return (
        f"{driver['name']} is the key {direction} outlier: "
        f"{float(driver.get('x_exposure') or 0.0):.1f}% exposure with {float(driver.get('y_change_pct') or 0.0):+.2f}% move."
    )


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
