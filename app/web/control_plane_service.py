"""Phase 4.3 control-plane service for web/API settings management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app.analytics.portfolio_analyzer import (
    build_portfolio_analysis as build_analyzer_payload,
    display_label as analyzer_display_label,
)
from app.allocation.service import (
    allocation_catalog,
    build_actual_allocation,
    default_allocation_targets,
    load_allocation_targets,
    merge_targets_with_catalog,
    save_allocation_targets,
)
from app.benchmark.service import (
    benchmark_summary as build_benchmark_summary,
    benchmark_types,
    default_benchmark_config,
    load_benchmark_config,
    save_benchmark_config,
)
from app.cma.service import load_cma_correlations, load_cma_entries, save_cma_entries, save_cma_correlations
from app.rebalancing.service import load_rebalancing_config, save_rebalancing_config
from app.risk.service import invalidate_risk_cache
from app.db.models import PortfolioHolding as PortfolioHoldingRow
from app.db.models import SentMessage as SentMessageRow
from app.db.models import UserPreference as UserPreferenceRow
from app.db.session import get_session
from app.personalization.preferences_service import (
    clear_preferences,
    get_preferences,
    set_preference,
    supported_preference_keys,
    unset_preference,
)
from app.personalization.user_profile import (
    UserProfile,
    _load_portfolio_context,
    _load_profile_overrides,
)
from app.policy.service import (
    default_investor_policy,
    load_investor_policy,
    save_investor_policy,
)
from app.portfolio.importer import load_holdings_file
from app.portfolio.service import load_active_holdings, replace_holdings_snapshot
from app.settings import Settings
from app.universe.sector_universe import load_sector_universe
from app.universe.ticker_metadata import TICKER_DISPLAY_NAMES, format_company_ticker
from app.validation.presets import list_preset_summaries
from app.simulation.service import load_simulation_context
from app.bonds.service import compute_bond_analytics as _compute_bond_analytics, load_bond_overrides
from app.esg.service import compute_portfolio_esg as _compute_portfolio_esg, load_esg_config
from app.fx.service import compute_fx_exposure as _compute_fx_exposure, load_fx_config
from app.verticals.engine import verticals_status_for_profile


POLICY_INVESTOR_TYPES = [
    "individual",
    "family_office",
    "advisor",
    "institutional",
    "model_portfolio",
    "other",
]
BASE_CURRENCY_OPTIONS = ["EUR", "USD", "GBP", "CHF", "JPY", "OTHER"]
REBALANCING_POLICY_OPTIONS = ["threshold", "calendar", "hybrid"]
GOVERNANCE_FREQUENCY_OPTIONS = ["monthly", "quarterly", "semi_annual", "annual"]
ALLOCATION_ROLE_OPTIONS = ["growth", "income", "diversifier", "hedge", "liquidity", "tactical", "other"]

UI_GLOSSARY: dict[str, dict[str, str]] = {
    "policy_fit": {
        "term": "Policy Fit",
        "short_definition": "Checks whether the current portfolio is aligned with your saved policy limits and targets.",
        "why_it_matters": "If fit is weak, your portfolio may no longer match your intended risk profile.",
    },
    "allocation_drift": {
        "term": "Allocation Drift",
        "short_definition": "How far current asset-class weights moved away from strategic target bands.",
        "why_it_matters": "Large drift can change risk and trigger a rebalance.",
    },
    "max_drawdown": {
        "term": "Max Drawdown",
        "short_definition": "Largest peak-to-trough decline over the selected period.",
        "why_it_matters": "Shows how severe losses could feel during stress periods.",
    },
    "tracking_error": {
        "term": "Tracking Error",
        "short_definition": "Volatility of your portfolio's returns relative to the benchmark.",
        "why_it_matters": "Higher tracking error means more active deviation from benchmark behavior.",
    },
    "information_ratio": {
        "term": "Information Ratio",
        "short_definition": "Active return divided by tracking error.",
        "why_it_matters": "Summarizes how efficiently active risk converted into benchmark outperformance.",
    },
    "cma": {
        "term": "Capital Market Assumptions (CMA)",
        "short_definition": "Forward expected returns, volatilities, and correlations by asset class.",
        "why_it_matters": "CMA inputs drive expected portfolio analytics and attribution baselines.",
    },
    "correlation_matrix": {
        "term": "Correlation Matrix",
        "short_definition": "Pairwise relationship between asset-class returns from -1 to +1.",
        "why_it_matters": "Correlation strongly affects diversification and expected portfolio volatility.",
    },
    "expected_sharpe": {
        "term": "Expected Sharpe",
        "short_definition": "Expected excess return divided by expected volatility.",
        "why_it_matters": "A quick risk-adjusted quality check of your forward assumptions.",
    },
    "rebalancing_trigger": {
        "term": "Rebalancing Trigger",
        "short_definition": "Rule that determines when to generate trades (threshold, calendar, or hybrid).",
        "why_it_matters": "Triggers control turnover, cost, and how tightly allocations stay on policy.",
    },
    "drift_threshold": {
        "term": "Drift Threshold",
        "short_definition": "Maximum allowed distance from target weights before action is triggered.",
        "why_it_matters": "Lower thresholds rebalance sooner but can increase trading frequency.",
    },
    "attribution": {
        "term": "Attribution",
        "short_definition": "Breaks active return into drivers such as allocation, selection, and interaction effects.",
        "why_it_matters": "Explains why you beat or lagged the benchmark in a structured way.",
    },
    "allocation_effect": {
        "term": "Allocation Effect",
        "short_definition": "Contribution from being over/underweight asset classes versus benchmark weights.",
        "why_it_matters": "Shows whether your allocation decisions helped or hurt active return.",
    },
    "active_return": {
        "term": "Active Return",
        "short_definition": "Portfolio return minus benchmark return.",
        "why_it_matters": "Core measure of outperformance or underperformance versus the reference benchmark.",
    },
    "var_cvar": {
        "term": "VaR / CVaR",
        "short_definition": "VaR estimates a loss threshold; CVaR estimates average loss beyond that threshold.",
        "why_it_matters": "Useful for understanding tail risk, not just average outcomes.",
    },
    "fan_chart": {
        "term": "Fan Chart",
        "short_definition": "Percentile bands showing a range of simulated portfolio paths over time.",
        "why_it_matters": "Helps visualize uncertainty instead of relying on a single forecast path.",
    },
    "avg_monthly_geom": {
        "term": "Average Monthly Return (Geometric)",
        "short_definition": "Compounded monthly return that, repeated each month, reproduces the total return.",
        "why_it_matters": "Cleaner than the arithmetic mean because it accounts for compounding and is consistent with cumulative results.",
    },
    "beta": {
        "term": "Beta",
        "short_definition": "Sensitivity of portfolio returns to benchmark returns; covariance over benchmark variance.",
        "why_it_matters": "Beta of 1 moves with the market, above 1 amplifies it, below 1 dampens it. Drives systematic risk and CAPM expected return.",
    },
    "r_squared": {
        "term": "R-Squared",
        "short_definition": "Share of portfolio return variation explained by the benchmark.",
        "why_it_matters": "High R-squared means beta and alpha are reliable; low R-squared means the portfolio drifts away from the benchmark and CAPM-based metrics are noisy.",
    },
    "treynor_ratio": {
        "term": "Treynor Ratio",
        "short_definition": "Excess return over the risk-free rate per unit of beta (systematic risk).",
        "why_it_matters": "Like Sharpe but penalises only systematic risk. Useful when comparing well-diversified portfolios where idiosyncratic risk should already be diversified away.",
    },
    "jensens_alpha": {
        "term": "Jensen's Alpha",
        "short_definition": "Return earned above what CAPM predicted, given the portfolio's beta and the benchmark return.",
        "why_it_matters": "Direct measure of skill or factor tilt: positive alpha is unexplained outperformance, negative alpha is unexplained drag.",
    },
    "probability_of_loss": {
        "term": "Probability of Loss",
        "short_definition": "Share of monthly periods where the portfolio return was negative.",
        "why_it_matters": "Frames downside risk as a frequency rather than a single drawdown number. Easier to reason about behaviourally.",
    },
    "average_loss": {
        "term": "Average Loss",
        "short_definition": "Mean monthly return across only the months that ended negative.",
        "why_it_matters": "Tells you what a typical losing month looks like, so you can sanity-check whether you can stomach the recurrence rate.",
    },
    "downside_risk": {
        "term": "Downside Risk",
        "short_definition": "Annualised volatility computed only from returns below the risk-free rate.",
        "why_it_matters": "Penalises only bad volatility, the input behind Sortino and a fairer risk measure for asymmetric strategies.",
    },
    "probability_of_underperformance": {
        "term": "Probability of Underperformance",
        "short_definition": "Share of monthly periods where the portfolio underperformed the benchmark.",
        "why_it_matters": "Tracks how often, not just by how much, you fell behind the benchmark. Cadence matters for IPS reviews.",
    },
    "average_underperformance": {
        "term": "Average Underperformance",
        "short_definition": "Mean monthly active return across only the months that underperformed the benchmark.",
        "why_it_matters": "Quantifies the typical size of a bad month relative to the benchmark, separate from how often it happens.",
    },
    "probability_of_outperformance": {
        "term": "Probability of Outperformance",
        "short_definition": "Share of monthly periods where the portfolio outperformed the benchmark.",
        "why_it_matters": "Counterpart to underperformance probability. Together they show whether outperformance is consistent or sporadic.",
    },
    "average_outperformance": {
        "term": "Average Outperformance",
        "short_definition": "Mean monthly active return across only the months that beat the benchmark.",
        "why_it_matters": "Shows the typical size of a winning month, useful for understanding the risk and reward asymmetry of the strategy.",
    },
    "bull_bear_active": {
        "term": "Bull / Bear Conditional Active Return",
        "short_definition": "Average active return separately in months where the benchmark was up (bull) versus down (bear).",
        "why_it_matters": "Distinguishes a portfolio that adds value when markets rally from one that defends in drawdowns. Asymmetric capture is a key IPS reporting concept.",
    },
    "modified_duration": {
        "term": "Modified Duration",
        "short_definition": "Approximate percentage price change for a 1% move in interest rates.",
        "why_it_matters": "Core measure of interest rate sensitivity. A duration of 6 means ~6% price loss for every +1% rate rise.",
    },
    "ytm": {
        "term": "Yield to Maturity (YTM)",
        "short_definition": "Expected annualised return if a bond is held to maturity, assuming all coupons are reinvested.",
        "why_it_matters": "The most complete single-number yield measure. Higher YTM = higher income but usually higher credit or rate risk.",
    },
    "dv01": {
        "term": "DV01 (Dollar Value of 01)",
        "short_definition": "Portfolio price change in dollars for a 1 basis point (0.01%) move in rates.",
        "why_it_matters": "Translates duration into a concrete P&L figure for a specific portfolio size.",
    },
    "credit_quality": {
        "term": "Credit Quality",
        "short_definition": "Issuer creditworthiness grouping: Government, Investment Grade (IG), High Yield (HY), or Emerging Markets (EM).",
        "why_it_matters": "Drives default risk, spread volatility, and regulatory capital treatment. HY bonds carry meaningfully more credit risk than IG.",
    },
    "rate_sensitivity": {
        "term": "Rate Sensitivity",
        "short_definition": "Estimated portfolio P&L for a parallel +100bps shift in the yield curve.",
        "why_it_matters": "Quick stress-test for rising rate environments. A -6% figure means a 1% rate rise costs ~6% of the bond sleeve value.",
    },
}

REGIONAL_INTELLIGENCE_BUCKETS: list[dict[str, Any]] = [
    {
        "key": "us",
        "label": "US",
        "aliases": {"us", "united_states", "north_america"},
        "focus": "Rates path, megacap earnings, and broad-dollar liquidity conditions.",
    },
    {
        "key": "europe",
        "label": "Europe",
        "aliases": {"europe", "eu", "uk", "united_kingdom"},
        "focus": "ECB policy direction, energy sensitivity, and cyclical export momentum.",
    },
    {
        "key": "china",
        "label": "China",
        "aliases": {"china", "greater_china"},
        "focus": "Growth impulse, policy support cadence, and property-credit confidence.",
    },
    {
        "key": "asia_ex_china",
        "label": "Rest of Asia",
        "aliases": {"asia", "asia_pacific", "japan", "korea", "taiwan", "india", "asean"},
        "focus": "Semiconductor cycle, Japan rates normalization, and Asia FX pressure.",
    },
    {
        "key": "middle_east",
        "label": "Middle East",
        "aliases": {"middle_east", "gulf"},
        "focus": "Energy supply risk, shipping-lane stability, and geopolitical volatility spillover.",
    },
    {
        "key": "russia_ukraine",
        "label": "Russia/Ukraine",
        "aliases": {"russia", "ukraine", "russia_ukraine"},
        "focus": "Sanctions trajectory, commodity flow risk, and Europe risk-premium shifts.",
    },
    {
        "key": "latam",
        "label": "Latin America",
        "aliases": {"latam", "latin_america", "brazil", "mexico"},
        "focus": "Commodity beta, domestic inflation cycles, and EM policy divergence.",
    },
    {
        "key": "cross_asset_spillovers",
        "label": "Cross-Asset Spillovers",
        "aliases": {"global", "global_macro", "fx", "commodities", "rates_macro"},
        "focus": "Cross-market transmission into rates, FX, credit spreads, and volatility.",
    },
]


def _display_label(value: str) -> str:
    """Render human labels while preserving finance acronyms in uppercase."""
    return analyzer_display_label(value)


def normalize_profile_name(profile_name: str) -> str:
    return (profile_name or "default_user").strip() or "default_user"


def build_profile_state(settings: Settings, profile_name: str) -> dict[str, Any]:
    """Build effective state for UI/API views (effective + overrides + holdings + catalogs)."""
    normalized_profile = normalize_profile_name(profile_name)
    profile = _load_profile_defaults(settings, normalized_profile)
    policy = load_investor_policy(normalized_profile) or default_investor_policy()
    allocation_targets_raw = load_allocation_targets(normalized_profile)
    allocation_targets = (
        merge_targets_with_catalog(allocation_targets_raw)
        if allocation_targets_raw
        else default_allocation_targets()
    )
    actual_allocation = build_actual_allocation(profile)
    benchmark = load_benchmark_config(normalized_profile) or default_benchmark_config()
    benchmark_view = build_benchmark_summary(benchmark if benchmark.get("name") or benchmark.get("base_symbol") else None)

    risk_config = _load_risk_config(normalized_profile)
    cma_entries = load_cma_entries(normalized_profile)
    cma_correlations = load_cma_correlations(normalized_profile)
    rebalancing_config = load_rebalancing_config(normalized_profile)
    overrides = get_preferences(normalized_profile)
    catalogs = _build_followables_catalog(settings=settings, profile=profile)
    metadata = _build_profile_metadata(profile=profile, profile_name=normalized_profile)
    validations = _build_profile_validations(profile=profile)
    analysis = build_analyzer_payload(
        profile=profile,
        settings=settings,
        metadata=metadata,
        validations=validations,
        policy=policy,
        allocation_targets=allocation_targets,
        actual_allocation=actual_allocation,
        benchmark=benchmark_view,
        benchmark_config=benchmark,
        risk_config=risk_config,
        cma_entries=cma_entries,
        cma_correlations=cma_correlations,
        rebalancing_config=rebalancing_config,
    )
    holdings = _build_holdings_view(profile=profile, settings=settings)
    metadata["holdings_snapshot_summary"] = _build_holdings_snapshot_summary(profile, analysis)
    metadata["override_summaries"] = _build_override_summaries(overrides)
    metadata["delivery_summary"] = _build_delivery_summary(profile)
    metadata["briefing_region_board"] = _build_briefing_region_board(profile=profile, analysis=analysis)
    metadata["policy_summary"] = _build_policy_summary(policy)
    metadata["allocation_summary"] = _build_allocation_summary(analysis.get("allocation_drift", {}))
    metadata["benchmark_summary"] = benchmark_view
    metadata["validation_presets"] = list_preset_summaries()
    metadata["ui_glossary"] = UI_GLOSSARY
    try:
        metadata["verticals_diagnostics"] = verticals_status_for_profile(profile=profile)
    except Exception:
        metadata["verticals_diagnostics"] = []
    simulation_context = load_simulation_context(
        profile_name=normalized_profile,
        fallback_holdings=holdings,
        fallback_benchmark_symbol=str(benchmark.get("base_symbol") or "ACWI"),
    )
    metadata["simulation"] = simulation_context
    analysis["ui_readiness"] = _build_ui_readiness(
        policy=policy,
        allocation_targets=allocation_targets,
        benchmark=benchmark,
        cma_entries=cma_entries,
    )
    analysis["ui_cma_asset_options"] = _cma_asset_options()

    # Phase 7A: bond analytics
    try:
        analysis["bonds_analytics"] = _compute_bond_analytics(
            profile_name=normalized_profile,
            holdings=profile.portfolio_holdings,
        )
        analysis["bond_overrides"] = load_bond_overrides(normalized_profile)
    except Exception:
        analysis["bonds_analytics"] = {"available": False, "error": "Bond analytics unavailable."}
        analysis["bond_overrides"] = {}

    # Phase 7C: ESG analytics
    try:
        analysis["esg_analytics"] = _compute_portfolio_esg(
            profile_name=normalized_profile,
            holdings=profile.portfolio_holdings,
            persist=False,
        )
        analysis["esg_config"] = load_esg_config(normalized_profile)
    except Exception:
        analysis["esg_analytics"] = {"available": False, "error": "ESG analytics unavailable."}
        analysis["esg_config"] = {}

    # Phase 7D: FX exposure
    try:
        analysis["fx_exposure"] = _compute_fx_exposure(
            profile_name=normalized_profile,
            holdings=profile.portfolio_holdings,
        )
        analysis["fx_config"] = load_fx_config(normalized_profile)
    except Exception:
        analysis["fx_exposure"] = {"available": False, "error": "FX analytics unavailable."}
        analysis["fx_config"] = {}

    analysis["ui_home"] = _build_ui_home_summary(
        profile=profile,
        metadata=metadata,
        analysis=analysis,
        ui_readiness=analysis["ui_readiness"],
    )

    healthcare_pref = profile.healthcare_preferences if hasattr(profile, "healthcare_preferences") else {}
    vertical_effective = {
        "healthcare": {
            "mode": str(overrides.get("verticals.healthcare.mode") or healthcare_pref.get("mode") or ("active" if healthcare_pref.get("enabled", False) else "off")),
            "priority": str(overrides.get("verticals.healthcare.priority") or "normal"),
            "max_items_morning": int(overrides.get("verticals.healthcare.max_items.morning") or healthcare_pref.get("max_items_morning", 4) or 4),
            "max_items_intraday": int(overrides.get("verticals.healthcare.max_items.intraday") or healthcare_pref.get("max_items_intraday", 3) or 3),
            "min_severity": str(overrides.get("verticals.healthcare.min_severity") or healthcare_pref.get("minimum_severity_intraday", "high")),
            "portfolio_weight_threshold": float(overrides.get("verticals.healthcare.portfolio_weight_threshold") or 0.0),
            "watchlist_count_threshold": int(overrides.get("verticals.healthcare.watchlist_count_threshold") or 1),
        }
    }

    return {
        "profile": normalized_profile,
        "risk_config": risk_config,
        "cma_entries": cma_entries,
        "cma_correlations": cma_correlations,
        "rebalancing_config": rebalancing_config,
        "effective": {
            "timezone": profile.timezone,
            "home_region": profile.home_region,
            "watchlist": {
                "primary": profile.watchlist_primary,
                "secondary": profile.watchlist_secondary,
                "monitor": profile.watchlist_monitor,
            },
            "coverage": {
                "sector_weights": profile.sector_weights,
                "portfolio_sector_weights": profile.portfolio_sector_weights,
                "region_weights": profile.coverage_weights,
                "home_region": profile.home_region,
                "home_region_label": _display_label(profile.home_region),
            },
            "delivery": {
                "morning_channels": profile.channels_for("morning"),
                "intraday_channels": profile.channels_for("intraday"),
                "breaking_channels": profile.channels_for("breaking"),
                "morning_brief_time": profile.morning_brief_time,
                "hourly_updates": profile.hourly_updates_enabled,
                "breaking_alerts": profile.breaking_alerts_enabled,
                "intraday_global_risk_enabled": profile.intraday_global_risk_enabled,
                "llm_email_morning": bool(
                    profile.delivery.get("llm_email_morning", settings.enable_llm_email_render)
                ),
                "llm_shadow_mode": bool(
                    profile.delivery.get("llm_shadow_mode", settings.llm_render_shadow_mode)
                ),
                "quiet_hours_start": profile.quiet_hours[0],
                "quiet_hours_end": profile.quiet_hours[1],
            },
            "sections": {
                "market_setup": profile.morning_section_enabled("market_setup"),
                "macro_context": profile.morning_section_enabled("macro_context"),
                "global_news": profile.morning_section_enabled("global_news"),
                "top_themes": profile.morning_section_enabled("top_themes"),
                "portfolio_focus": profile.morning_section_enabled("portfolio_focus"),
                "sector_scan": profile.morning_section_enabled("sector_scan"),
                "watchlist": profile.morning_section_enabled("watchlist"),
                "watchlist_snapshot": profile.morning_section_enabled("watchlist_snapshot"),
            },
            "verticals": vertical_effective,
        },
        "overrides": overrides,
        "policy": policy,
        "allocation": {
            "targets": allocation_targets,
            "actual": actual_allocation,
            "catalog": allocation_catalog(),
        },
        "benchmark": {
            **benchmark,
            "types": benchmark_types(),
        },
        "holdings": holdings,
        "catalogs": catalogs,
        "metadata": metadata,
        "validations": validations,
        "analysis": analysis,
        "supported_preference_keys": supported_preference_keys(),
    }


def apply_preference_updates(profile_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply a bulk preference update and return normalized values."""
    normalized_profile = normalize_profile_name(profile_name)
    normalized_updates: dict[str, Any] = {}
    for key, value in updates.items():
        normalized_updates[key] = set_preference(normalized_profile, key, value)
    return {
        "profile": normalized_profile,
        "updated": normalized_updates,
        "overrides": get_preferences(normalized_profile),
    }


def remove_preference(profile_name: str, pref_key: str) -> dict[str, Any]:
    normalized_profile = normalize_profile_name(profile_name)
    removed = unset_preference(normalized_profile, pref_key)
    return {
        "profile": normalized_profile,
        "key": pref_key,
        "removed": removed,
        "overrides": get_preferences(normalized_profile),
    }


def reset_preferences(profile_name: str) -> dict[str, Any]:
    normalized_profile = normalize_profile_name(profile_name)
    removed_count = clear_preferences(normalized_profile)
    return {
        "profile": normalized_profile,
        "removed_count": removed_count,
        "overrides": get_preferences(normalized_profile),
    }


def save_policy(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_profile = normalize_profile_name(profile_name)
    return save_investor_policy(normalized_profile, payload)


def save_allocation(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_profile = normalize_profile_name(profile_name)
    return save_allocation_targets(normalized_profile, rows)


def save_cma(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_profile = normalize_profile_name(profile_name)
    return save_cma_entries(normalized_profile, rows)


def save_cma_corr(profile_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_profile = normalize_profile_name(profile_name)
    return save_cma_correlations(normalized_profile, rows)


def save_benchmark(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_profile = normalize_profile_name(profile_name)
    return save_benchmark_config(normalized_profile, payload)


def import_holdings_from_upload(
    *,
    profile_name: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    """Parse uploaded holdings file and replace profile snapshot."""
    normalized_profile = normalize_profile_name(profile_name)
    suffix = Path(filename or "").suffix.lower() or ".yaml"
    if suffix not in {".yaml", ".yml", ".csv"}:
        raise ValueError(f"Unsupported holdings file type: {suffix}")

    with tempfile.TemporaryDirectory(prefix="briefly_holdings_") as temp_dir:
        temp_path = Path(temp_dir) / f"upload{suffix}"
        temp_path.write_bytes(content)
        snapshot = load_holdings_file(temp_path, default_profile=normalized_profile)

    imported_count = replace_holdings_snapshot(
        profile_name=snapshot.profile_name or normalized_profile,
        holdings=snapshot.holdings,
        as_of_date=snapshot.as_of_date,
    )
    return {
        "imported_count": imported_count,
        "profile_name": snapshot.profile_name or normalized_profile,
        "as_of_date": snapshot.as_of_date.isoformat() if snapshot.as_of_date else None,
    }


def save_holdings_from_form(
    profile_name: str,
    form: Any,
) -> dict[str, Any]:
    """Persist holdings submitted from the inline editor.

    Reads parallel form arrays (holding_symbol, holding_weight, holding_bucket)
    and calls replace_holdings_snapshot, merging weight/bucket edits with any
    existing per-position metadata (shares, avg_cost, account) so those fields
    are not lost when the user makes weight adjustments from the UI.
    """
    from datetime import date
    from app.schemas.portfolio import PortfolioHolding

    normalized_profile = normalize_profile_name(profile_name)
    existing: dict[str, PortfolioHolding] = {
        h.symbol: h for h in load_active_holdings(normalized_profile)
    }

    symbols = [str(s).strip().upper() for s in form.getlist("holding_symbol") if str(s).strip()]
    weights = list(form.getlist("holding_weight"))
    buckets = list(form.getlist("holding_bucket"))

    # Pad to same length as symbols (defensive)
    while len(weights) < len(symbols):
        weights.append("")
    while len(buckets) < len(symbols):
        buckets.append("")

    holdings: list[PortfolioHolding] = []
    seen: set[str] = set()
    for symbol, weight_raw, bucket_raw in zip(symbols, weights, buckets):
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        weight_val: float | None = None
        try:
            w = float(str(weight_raw).strip())
            weight_val = max(0.0, w) if w >= 0 else None
        except (ValueError, TypeError):
            pass

        bucket_text = str(bucket_raw).strip().lower()
        bucket_val: str | None = bucket_text if bucket_text and bucket_text != "—" else None

        prev = existing.get(symbol)
        holdings.append(
            PortfolioHolding(
                profile_name=normalized_profile,
                symbol=symbol,
                weight_pct=weight_val,
                shares=prev.shares if prev else None,
                avg_cost=prev.avg_cost if prev else None,
                account=prev.account if prev else None,
                sector_override=prev.sector_override if prev else None,
                bucket=bucket_val,
                as_of_date=date.today(),
            )
        )

    count = replace_holdings_snapshot(normalized_profile, holdings)
    return {"count": count, "profile_name": normalized_profile}


def search_followables(
    *,
    settings: Settings,
    q: str = "",
    kind: str = "",
    limit: int = 50,
) -> list[dict[str, str]]:
    """Search followables catalog for dropdown/autocomplete UI controls."""
    query = (q or "").strip().lower()
    normalized_kind = (kind or "").strip().lower()
    catalogs = _build_followables_catalog(settings=settings)

    options: list[dict[str, str]] = []
    kind_map = {
        "stocks": "stock",
        "sectors": "sector",
        "indices": "index",
        "macro": "macro",
        "regions": "region",
    }
    for bucket in ("stocks", "sectors", "indices", "macro", "regions"):
        if normalized_kind and normalized_kind != kind_map[bucket]:
            continue
        for item in catalogs[bucket]:
            haystack = f"{item.get('symbol', '')} {item.get('label', '')} {item.get('key', '')}".lower()
            if query and query not in haystack:
                continue
            options.append(item)

    options.sort(key=lambda item: (item.get("kind", ""), item.get("symbol", item.get("key", ""))))
    return options[: max(1, min(limit, 200))]


def _load_profile_defaults(settings: Settings, profile_name: str) -> UserProfile:
    """Load profile defaults from YAML, then apply persisted overrides + holdings."""
    configs_dir = Path(settings.configs_dir)
    profile_path = configs_dir / "user_profile.yaml"
    if not profile_path.exists():
        profile_path = configs_dir / "user_profile.example.yaml"

    data: dict[str, Any] = {}
    if profile_path.exists():
        with open(profile_path) as handle:
            data = yaml.safe_load(handle) or {}

    user_data = data.get("user", {}) if isinstance(data, dict) else {}
    profile = UserProfile(
        name=profile_name,
        timezone=user_data.get("timezone", settings.timezone),
        home_region=user_data.get("home_region", "spain"),
        message_depth=user_data.get("message_depth", "standard"),
        primary_channel=user_data.get("primary_channel", "telegram"),
        backup_channel=user_data.get("backup_channel", "email"),
        coverage_weights=data.get("coverage_weights", {}) if isinstance(data, dict) else {},
        sector_weights=data.get("sector_weights", {}) if isinstance(data, dict) else {},
        delivery=data.get("delivery", {}) if isinstance(data, dict) else {},
        style=data.get("style", {}) if isinstance(data, dict) else {},
    )

    watchlist_path = configs_dir / "watchlists.yaml"
    if not watchlist_path.exists():
        watchlist_path = configs_dir / "watchlists.example.yaml"
    if watchlist_path.exists():
        with open(watchlist_path) as handle:
            watchlists = yaml.safe_load(handle) or {}
        profile.watchlist_primary = [str(item).upper() for item in watchlists.get("primary", [])]
        profile.watchlist_secondary = [str(item).upper() for item in watchlists.get("secondary", [])]
        profile.watchlist_monitor = [str(item).upper() for item in watchlists.get("monitor", [])]

    _load_profile_overrides(profile)
    _load_portfolio_context(profile, configs_dir)
    return profile


def _build_followables_catalog(
    *,
    settings: Settings,
    profile: UserProfile | None = None,
) -> dict[str, list[dict[str, str]]]:
    universe = load_sector_universe(settings)
    stocks = [
        {
            "kind": "stock",
            "symbol": symbol,
            "label": format_company_ticker(symbol),
        }
        for symbol in sorted(TICKER_DISPLAY_NAMES.keys())
    ]

    sectors = [
        {
            "kind": "sector",
            "key": sector.key,
            "symbol": sector.etf,
            "label": f"{sector.display_name} ({sector.etf})" if sector.etf else sector.display_name,
        }
        for sector in universe.sectors
    ]

    indices = [
        {
            "kind": "index",
            "symbol": instrument.symbol,
            "label": instrument.display,
        }
        for instrument in universe.indices
    ]

    macro = [
        {
            "kind": "macro",
            "symbol": instrument.symbol,
            "label": instrument.display,
        }
        for instrument in universe.macro_instruments
    ]

    default_regions = {
        "us",
        "europe",
        "china",
        "asia",
        "asia_ex_china",
        "middle_east",
        "russia_ukraine",
        "latam",
        "global",
        "cross_asset_spillovers",
    }
    profile_regions = set(profile.coverage_weights.keys()) if profile else set()
    if profile and profile.home_region:
        profile_regions.add(profile.home_region)
    region_keys = sorted(default_regions | {str(item).lower() for item in profile_regions if item})
    regions = [
        {
            "kind": "region",
            "key": region,
            "label": _display_label(region),
        }
        for region in region_keys
    ]

    return {
        "stocks": stocks,
        "sectors": sectors,
        "indices": indices,
        "macro": macro,
        "regions": regions,
        "enum_options": {
            "investor_type": POLICY_INVESTOR_TYPES,
            "base_currency": BASE_CURRENCY_OPTIONS,
            "rebalancing_policy": REBALANCING_POLICY_OPTIONS,
            "governance_frequency": GOVERNANCE_FREQUENCY_OPTIONS,
            "allocation_role": ALLOCATION_ROLE_OPTIONS,
        },
    }


def _build_profile_metadata(*, profile: UserProfile, profile_name: str) -> dict[str, Any]:
    """Build operational metadata used by UI summary cards."""
    holdings_updated_at = _latest_holdings_update(profile_name)
    preferences_updated_at = _latest_preferences_update(profile_name)
    return {
        "last_holdings_update": _iso_or_none(holdings_updated_at),
        "last_preferences_update": _iso_or_none(preferences_updated_at),
        "last_holdings_update_local": _display_local_datetime(holdings_updated_at, profile.timezone),
        "last_preferences_update_local": _display_local_datetime(preferences_updated_at, profile.timezone),
        "next_morning_send_local": _next_morning_send(profile),
        "delivery_recent": _recent_delivery_status(profile=profile),
    }


def _build_profile_validations(*, profile: UserProfile) -> list[dict[str, str]]:
    """Produce user-facing validation warnings for obvious misconfiguration states."""
    warnings: list[dict[str, str]] = []

    weights = [position.weight_pct for position in profile.portfolio_holdings if position.weight_pct is not None]
    if weights:
        weight_sum = float(sum(weights))
        if weight_sum < 95 or weight_sum > 105:
            warnings.append(
                {
                    "code": "holdings_weight_sum",
                    "message": (
                        f"Holdings weights sum to {weight_sum:.1f}%. "
                        "Consider normalizing toward 100% for cleaner portfolio relevance."
                    ),
                }
            )

    region_values = [float(value) for value in profile.coverage_weights.values()] if profile.coverage_weights else []
    if not region_values or max(region_values) <= 0:
        warnings.append(
            {
                "code": "region_weights_empty",
                "message": "Region weights are all zero. Coverage by region may feel random.",
            }
        )

    sector_values = [float(value) for value in profile.sector_weights.values()] if profile.sector_weights else []
    if sector_values:
        max_sector = max(sector_values)
        positive_values = [value for value in sector_values if value > 0]
        if positive_values:
            baseline = sum(positive_values) / len(positive_values)
            if baseline > 0 and max_sector / baseline >= 3.0:
                warnings.append(
                    {
                        "code": "sector_weight_concentration",
                        "message": (
                            "Sector weighting is highly concentrated. "
                            "This may suppress useful cross-sector stories."
                        ),
                    }
                )

    delivery_groups = {
        "morning": profile.channels_for("morning"),
        "intraday": profile.channels_for("intraday"),
        "breaking": profile.channels_for("breaking"),
    }
    for briefing_type, channels in delivery_groups.items():
        if channels:
            continue
        warnings.append(
            {
                "code": f"delivery_missing_{briefing_type}",
                "message": f"No channels enabled for {briefing_type} briefing delivery.",
            }
        )

    if not profile.watchlist_primary:
        warnings.append(
            {
                "code": "watchlist_primary_empty",
                "message": "Primary watchlist is empty. High-priority personalization will be weaker.",
            }
        )

    return warnings


def _latest_holdings_update(profile_name: str) -> datetime | None:
    with get_session() as session:
        row = (
            session.query(PortfolioHoldingRow.updated_at)
            .filter(
                PortfolioHoldingRow.profile_name == profile_name,
                PortfolioHoldingRow.active.is_(True),
            )
            .order_by(PortfolioHoldingRow.updated_at.desc())
            .first()
        )
    return row[0] if row else None


def _latest_preferences_update(profile_name: str) -> datetime | None:
    with get_session() as session:
        row = (
            session.query(UserPreferenceRow.updated_at)
            .filter(
                UserPreferenceRow.profile_name == profile_name,
                UserPreferenceRow.active.is_(True),
            )
            .order_by(UserPreferenceRow.updated_at.desc())
            .first()
        )
    return row[0] if row else None


def _next_morning_send(profile: UserProfile) -> str:
    time_parts = (profile.morning_brief_time or "08:45").split(":")
    hour = int(time_parts[0])
    minute = int(time_parts[1])
    tz = ZoneInfo(profile.timezone)
    now_local = datetime.now(tz)
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_local:
        candidate = candidate + timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M %Z")


def _recent_delivery_status(*, profile: UserProfile) -> list[dict[str, str]]:
    """Summarize latest delivery outcomes by message type and channel."""
    with get_session() as session:
        rows = (
            session.query(SentMessageRow)
            .order_by(SentMessageRow.sent_at.desc())
            .limit(200)
            .all()
        )

    latest: dict[tuple[str, str], SentMessageRow] = {}
    for row in rows:
        key = (str(row.message_type or ""), str(row.channel or ""))
        latest.setdefault(key, row)

    expected: list[tuple[str, str]] = []
    for msg_type, channels in (
        ("morning_brief", profile.channels_for("morning")),
        ("intraday", profile.channels_for("intraday")),
        ("breaking", profile.channels_for("breaking")),
    ):
        expected.extend((msg_type, channel) for channel in channels)

    unique_pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for pair in expected + list(latest.keys()):
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        unique_pairs.append(pair)

    summary: list[dict[str, str]] = []
    for message_type, channel in unique_pairs:
        row = latest.get((message_type, channel))
        if row is None:
            status = "never_sent"
            reason = "No successful or failed delivery records yet."
            sent_at_local = "Not yet"
        else:
            status = "sent" if bool(row.success) else "failed"
            reason = (
                str(row.error_message)
                if row.error_message
                else ("Delivered successfully." if bool(row.success) else "Delivery attempt returned unsuccessful status.")
            )
            sent_at_local = _display_local_datetime(row.sent_at, profile.timezone)
        summary.append(
            {
                "message_type": message_type,
                "channel": channel,
                "status": status,
                "reason": reason,
                "sent_at_local": sent_at_local,
            }
        )
    summary.sort(key=lambda item: (item["message_type"], item["channel"]))
    return summary


def _display_local_datetime(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "Not provided"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(tz_name)
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _build_holdings_view(*, profile: UserProfile, settings: Settings) -> list[dict[str, Any]]:
    universe = load_sector_universe(settings)
    ticker_to_sector: dict[str, str] = {}
    sector_labels: dict[str, str] = {}
    for sector in universe.sectors:
        sector_labels[sector.key] = sector.display_name or _display_label(sector.key)
        for ticker in sector.key_names:
            ticker_to_sector[ticker.upper()] = sector.key

    rows: list[dict[str, Any]] = []
    for holding in profile.portfolio_holdings:
        row = holding.model_dump(mode="json")
        sector_key = holding.sector_override or ticker_to_sector.get(holding.symbol)
        row["sector_label"] = sector_labels.get(sector_key, "Not provided")
        row["bucket_label"] = _display_label(holding.bucket) if holding.bucket else "Not provided"
        rows.append(row)
    rows.sort(
        key=lambda item: (
            item.get("weight_pct") is None,
            -(item.get("weight_pct") or 0.0),
            item["symbol"],
        )
    )
    return rows


def _build_holdings_snapshot_summary(profile: UserProfile, analysis: dict[str, Any]) -> str:
    totals = analysis.get("holdings_totals", {})
    return (
        f"Analyzed snapshot: {len(profile.portfolio_holdings)} holding(s), "
        f"{totals.get('weighted_positions', 0)} weighted, {totals.get('total_weight_display', 'Not provided')} total."
    )


def _build_override_summaries(overrides: dict[str, Any]) -> list[str]:
    label_map = {
        "coverage.home_region": "Home region focus updated",
        "coverage.weights": "Region weights updated",
        "delivery.breaking_alerts": "Breaking alerts updated",
        "delivery.breaking_channels": "Breaking routing updated",
        "delivery.hourly_updates": "Intraday cadence updated",
        "delivery.intraday_channels": "Intraday routing updated",
        "delivery.intraday_global_risk_enabled": "Intraday global risk block updated",
        "delivery.llm_email_morning": "Morning LLM email updated",
        "delivery.llm_shadow_mode": "LLM shadow mode updated",
        "delivery.morning_brief_time": "Morning brief time updated",
        "delivery.morning_channels": "Morning routing updated",
        "delivery.quiet_hours_end": "Quiet hours end updated",
        "delivery.quiet_hours_start": "Quiet hours start updated",
        "sections.global_news": "Global News section updated",
        "sector.weights": "Sector coverage weights updated",
        "watchlist.monitor": "Monitor watchlist updated",
        "watchlist.primary": "Primary watchlist updated",
        "watchlist.secondary": "Secondary watchlist updated",
    }
    return [label_map.get(key, f"{key} override saved") for key in sorted(overrides.keys())]


def _build_delivery_summary(profile: UserProfile) -> dict[str, str]:
    def _channel_text(channels: list[str]) -> str:
        return " + ".join(item.capitalize() for item in channels) if channels else "Off"

    return {
        "headline": (
            f"Morning: {_channel_text(profile.channels_for('morning'))} at {profile.morning_brief_time} · "
            f"Intraday: {_channel_text(profile.channels_for('intraday'))} · "
            f"Breaking: {_channel_text(profile.channels_for('breaking'))}"
        ),
        "quiet_hours": f"{profile.quiet_hours[0]}–{profile.quiet_hours[1]}",
        "llm_mode": (
            "Morning email stays deterministic while shadow mode prints an LLM preview."
            if bool(profile.delivery.get('llm_shadow_mode', False))
            else "Morning email uses the selected primary formatter."
        ),
    }


def _normalise_region_key(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")


def _region_bucket_for_key(region_key: str) -> str | None:
    normalized = _normalise_region_key(region_key)
    for bucket in REGIONAL_INTELLIGENCE_BUCKETS:
        if normalized in bucket["aliases"]:
            return str(bucket["key"])
    return None


def _build_briefing_region_board(*, profile: UserProfile, analysis: dict[str, Any]) -> dict[str, Any]:
    totals: dict[str, float] = {str(bucket["key"]): 0.0 for bucket in REGIONAL_INTELLIGENCE_BUCKETS}
    positive_total = 0.0
    for key, value in (profile.coverage_weights or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric <= 0:
            continue
        positive_total += numeric
        bucket_key = _region_bucket_for_key(str(key))
        if bucket_key:
            totals[bucket_key] += numeric

    home_bucket = _region_bucket_for_key(profile.home_region) or "cross_asset_spillovers"
    top_sector = str(((analysis.get("kpis") or {}).get("top_sector_label") or "core sectors")).strip()
    holdings_count = len(profile.portfolio_holdings)

    def _status_for_share(share_pct: float) -> tuple[str, str]:
        if share_pct >= 28.0:
            return "lead", "Lead"
        if share_pct >= 14.0:
            return "active", "Active"
        if share_pct >= 6.0:
            return "monitor", "Monitor"
        return "underweight", "Underweight"

    rows: list[dict[str, Any]] = []
    for index, bucket in enumerate(REGIONAL_INTELLIGENCE_BUCKETS):
        key = str(bucket["key"])
        raw_weight = float(totals.get(key, 0.0))
        share_pct = (raw_weight / positive_total * 100.0) if positive_total > 0 else 0.0
        status_key, status_label = _status_for_share(share_pct)
        if key == home_bucket and share_pct < 8.0:
            status_key, status_label = "needs_attention", "Needs Attention"

        if key == "cross_asset_spillovers":
            portfolio_lens = "Prioritize rates/FX/volatility transmission into cross-sleeve risk."
        elif key == home_bucket:
            portfolio_lens = (
                f"Home-region lens ({_display_label(profile.home_region)}) should remain visible in tomorrow's routing."
            )
        else:
            portfolio_lens = (
                f"Watch for spillover into {top_sector} and {holdings_count} holding(s) as regional narratives rotate."
            )

        rows.append(
            {
                "key": key,
                "label": str(bucket["label"]),
                "priority_rank": index + 1,
                "coverage_weight": raw_weight,
                "coverage_share_pct": round(share_pct, 1),
                "coverage_share_display": f"{share_pct:.1f}%",
                "status": status_key,
                "status_label": status_label,
                "focus": str(bucket["focus"]),
                "portfolio_lens": portfolio_lens,
                "is_home_region": key == home_bucket,
            }
        )

    rows.sort(key=lambda item: (-(item["coverage_share_pct"]), item["priority_rank"]))
    lead = rows[0] if rows else None
    secondary = rows[1] if len(rows) > 1 else None
    headline = (
        f"Regional lead is {lead['label']} ({lead['coverage_share_display']}). "
        + (
            f"Secondary emphasis is {secondary['label']} ({secondary['coverage_share_display']})."
            if secondary
            else "Configure additional positive region weights for broader global balance."
        )
    ) if lead else "Configure region weights to activate regional intelligence routing."

    return {
        "headline": headline,
        "home_region_label": _display_label(profile.home_region),
        "rows": rows,
    }


def _build_policy_summary(policy: dict[str, Any]) -> dict[str, str]:
    investor_type = _display_or_not_provided(_display_label(str(policy.get("investor_type") or "")))
    base_currency = _display_or_not_provided(str(policy.get("base_currency") or "").upper())
    horizon = policy.get("investment_horizon_years")
    target_return = policy.get("target_return_percent")
    max_vol = policy.get("max_volatility_percent")
    return {
        "headline": f"{investor_type} policy in {base_currency}",
        "detail": (
            f"Horizon {horizon:g}y, target return {_format_numeric_percent(target_return)}, "
            f"max volatility {_format_numeric_percent(max_vol)}."
            if horizon is not None or target_return is not None or max_vol is not None
            else "No IPS inputs saved yet. Add policy targets to anchor the portfolio to an investor mandate."
        ),
    }


def _build_allocation_summary(allocation_drift: dict[str, Any]) -> dict[str, str]:
    rows = allocation_drift.get("rows", []) if isinstance(allocation_drift, dict) else []
    breaches = [
        row for row in rows
        if row.get("status") in {"above_band", "below_band"}
    ]
    if not rows:
        return {
            "headline": "Allocation targets not configured",
            "detail": "Add strategic target weights and allowed bands to compare the live book with policy allocation.",
        }
    if breaches:
        lead = breaches[0]
        return {
            "headline": f"{len(breaches)} allocation band breach(s)",
            "detail": f"{lead.get('label', 'Portfolio')} is {lead.get('status', 'off target').replace('_', ' ')} against policy bands.",
        }
    return {
        "headline": "Allocation is within configured bands",
        "detail": allocation_drift.get("summary") or "Actual allocation is currently inside the configured policy ranges.",
    }


def _policy_is_configured(policy: dict[str, Any]) -> bool:
    return any(
        policy.get(key) not in (None, "", [])
        for key in (
            "target_return_percent",
            "max_volatility_percent",
            "max_drawdown_percent",
            "single_name_limit_percent",
            "max_equity_percent",
            "min_liquid_assets_percent",
        )
    )


def _allocation_is_configured(allocation_targets: list[dict[str, Any]]) -> bool:
    for row in allocation_targets or []:
        if row.get("target_weight_pct") is not None and row.get("min_weight_pct") is not None and row.get("max_weight_pct") is not None:
            return True
    return False


def _benchmark_is_configured(benchmark: dict[str, Any]) -> bool:
    if not benchmark:
        return False
    if benchmark.get("base_symbol"):
        return True
    if benchmark.get("name"):
        return True
    return False


def _build_ui_readiness(
    *,
    policy: dict[str, Any],
    allocation_targets: list[dict[str, Any]],
    benchmark: dict[str, Any],
    cma_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    policy_ready = _policy_is_configured(policy)
    allocation_ready = _allocation_is_configured(allocation_targets)
    benchmark_ready = _benchmark_is_configured(benchmark)
    cma_ready = bool(cma_entries)
    return {
        "policy": {"configured": policy_ready, "status_label": "Configured" if policy_ready else "Unavailable"},
        "allocation": {"configured": allocation_ready, "status_label": "Configured" if allocation_ready else "Partial"},
        "benchmark": {"configured": benchmark_ready, "status_label": "Configured" if benchmark_ready else "Unavailable"},
        "cma": {"configured": cma_ready, "status_label": "Configured" if cma_ready else "Unavailable"},
    }


def _cma_asset_options() -> list[dict[str, str]]:
    return [
        {"key": "equities", "label": "Equities"},
        {"key": "high_quality_bonds", "label": "High-Quality Bonds"},
        {"key": "credit", "label": "Credit"},
        {"key": "alternatives", "label": "Alternatives"},
        {"key": "real_assets", "label": "Real Assets"},
        {"key": "gold", "label": "Gold"},
        {"key": "cash_liquidity", "label": "Cash / Liquidity"},
    ]


def _build_ui_home_summary(
    *,
    profile: UserProfile,
    metadata: dict[str, Any],
    analysis: dict[str, Any],
    ui_readiness: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic, personalized home-screen context."""

    def _status_label(raw: str, fallback: str = "Not provided") -> str:
        text = (raw or "").strip()
        if not text:
            return fallback
        return _display_label(text).title()

    def _pick_most_recent_local() -> str:
        h = str(metadata.get("last_holdings_update_local") or "Not provided")
        p = str(metadata.get("last_preferences_update_local") or "Not provided")
        h_iso = metadata.get("last_holdings_update")
        p_iso = metadata.get("last_preferences_update")

        def _to_dt(value: Any) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return None

        h_dt = _to_dt(h_iso)
        p_dt = _to_dt(p_iso)
        if h_dt and p_dt:
            return h if h_dt >= p_dt else p
        return h if h != "Not provided" else p

    timing = analysis.get("timing", {}) if isinstance(analysis, dict) else {}
    policy_fit = analysis.get("policy_fit", {}) if isinstance(analysis, dict) else {}
    risk = analysis.get("risk_analytics", {}) if isinstance(analysis, dict) else {}
    rebalance = analysis.get("rebalance_proposal", {}) if isinstance(analysis, dict) else {}
    holdings_totals = analysis.get("holdings_totals", {}) if isinstance(analysis, dict) else {}

    policy_status = str(policy_fit.get("status") or "unavailable")
    risk_status = str(risk.get("risk_status") or "unavailable")
    rebalance_status = str(rebalance.get("status") or "unavailable")

    if policy_status in {"needs_attention", "breach"}:
        summary = "Policy fit needs attention. Review allocation drift and risk guardrails before the next session."
        recommended_workspace = "portfolio"
    elif rebalance_status in {"rebalance_recommended", "breach", "action_needed"}:
        summary = "Rebalancing action is recommended based on current drift. Generate and review the latest proposal."
        recommended_workspace = "portfolio"
    elif risk_status in {"needs_attention", "elevated"}:
        summary = "Risk snapshot is elevated versus current settings. Review benchmark-relative metrics and drawdown profile."
        recommended_workspace = "portfolio_risk"
    else:
        next_brief = timing.get("next_morning_send_local") or metadata.get("next_morning_send_local") or "Not provided"
        summary = f"Portfolio is stable and delivery is ready. Next morning brief is scheduled for {next_brief}."
        recommended_workspace = "briefing"

    holdings_count = len(profile.portfolio_holdings)
    weighted_positions = int(holdings_totals.get("weighted_positions") or 0)
    total_weight_display = str(holdings_totals.get("total_weight_display") or "Not provided")

    last_active_workspace = "Portfolio Workbench"
    h_iso = metadata.get("last_holdings_update")
    p_iso = metadata.get("last_preferences_update")
    if h_iso and p_iso:
        try:
            if datetime.fromisoformat(str(p_iso)) > datetime.fromisoformat(str(h_iso)):
                last_active_workspace = "Market Briefing"
        except ValueError:
            pass

    portfolio_is_unconfigured = not all(
        bool(ui_readiness.get(key, {}).get("configured"))
        for key in ("policy", "allocation", "benchmark", "cma")
    )

    return {
        "title": "Briefly Home",
        "subtitle": (
            f"{profile.name} · {profile.timezone} · "
            f"{_display_label(profile.home_region)} focus · "
            f"Last update {_pick_most_recent_local()}"
        ),
        "what_matters_now": summary,
        "profile_snapshot": (
            f"{holdings_count} holding(s), {weighted_positions} weighted, "
            f"{total_weight_display} total weight."
        ),
        "recommended_workspace": recommended_workspace,
        "last_active_workspace": last_active_workspace,
        "primary_ctas": [
            {"label": "Open Portfolio", "href": "/ui/portfolio", "emphasis": "primary"},
            {"label": "Edit Briefing", "href": "/ui/briefing", "emphasis": "secondary"},
            {"label": "Review Risk", "href": "/ui/portfolio/risk", "emphasis": "secondary"},
        ],
        "portfolio_is_unconfigured": portfolio_is_unconfigured,
        "quick_setup_cta": {
            "label": "Quick portfolio setup (recommended)"
            if portfolio_is_unconfigured
            else "Re-run quick setup",
            "href": "/ui/portfolio/easy-setup",
            "description": (
                "Answer a few simple questions and auto-fill policy, allocation, benchmark, CMA, risk, and rebalancing defaults."
            ),
        },
        "status_chips": [
            {"label": "Next Brief", "value": str(timing.get("next_morning_send_local") or "Not provided")},
            {"label": "Policy Fit", "value": _status_label(policy_status)},
            {"label": "Risk Snapshot", "value": _status_label(str(risk.get("risk_status") or ""))},
            {"label": "Rebalance", "value": str(rebalance.get("status_label") or "Not provided")},
        ],
    }


def _format_numeric_percent(value: Any) -> str:
    try:
        if value in ("", None):
            return "Not provided"
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "Not provided"


def _display_or_not_provided(value: str) -> str:
    text = str(value or "").strip()
    return text or "Not provided"


def _load_risk_config(profile_name: str) -> dict:
    """Load risk configuration from UserPreference store."""
    with get_session() as session:
        def _get_pref(key: str):
            row = (
                session.query(UserPreferenceRow)
                .filter(
                    UserPreferenceRow.profile_name == profile_name,
                    UserPreferenceRow.pref_key == key,
                    UserPreferenceRow.active.is_(True),
                )
                .first()
            )
            return row.pref_value if row else None

        lookback_raw = _get_pref("risk.lookback_days")
        rfr_raw = _get_pref("risk.risk_free_rate_pct")

    try:
        lookback_days = int(lookback_raw) if lookback_raw is not None else 252
    except (TypeError, ValueError):
        lookback_days = 252

    try:
        risk_free_rate_pct = float(rfr_raw) if rfr_raw is not None else 4.5
    except (TypeError, ValueError):
        risk_free_rate_pct = 4.5

    return {"lookback_days": lookback_days, "risk_free_rate_pct": risk_free_rate_pct}


def save_risk_config(profile_name: str, lookback_days: int, risk_free_rate_pct: float) -> dict:
    """Persist risk configuration to UserPreference."""
    from app.personalization.preferences_service import get_preferences
    from app.db.models import UserPreference as UserPreferenceModel

    normalized = normalize_profile_name(profile_name)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

    with get_session() as session:
        for key, value in [
            ("risk.lookback_days", lookback_days),
            ("risk.risk_free_rate_pct", risk_free_rate_pct),
        ]:
            row = (
                session.query(UserPreferenceModel)
                .filter(
                    UserPreferenceModel.profile_name == normalized,
                    UserPreferenceModel.pref_key == key,
                )
                .first()
            )
            if row is None:
                row = UserPreferenceModel(
                    profile_name=normalized,
                    pref_key=key,
                    pref_value=value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.pref_value = value
                row.updated_at = now
                row.active = True

    return {"lookback_days": lookback_days, "risk_free_rate_pct": risk_free_rate_pct}


def refresh_risk_for_profile(profile_name: str) -> None:
    """Invalidate cache so next state build recomputes risk analytics."""
    invalidate_risk_cache(normalize_profile_name(profile_name))


def save_rebalancing_config_for_profile(profile_name: str, payload: dict) -> dict:
    """Persist rebalancing config and return the saved values."""
    return save_rebalancing_config(normalize_profile_name(profile_name), payload)
