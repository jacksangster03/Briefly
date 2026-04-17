"""Phase 4.3 control-plane service for web/API settings management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from app.db.models import PortfolioHolding as PortfolioHoldingRow
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
from app.portfolio.importer import load_holdings_file
from app.portfolio.service import replace_holdings_snapshot
from app.settings import Settings
from app.universe.sector_universe import load_sector_universe
from app.universe.ticker_metadata import TICKER_DISPLAY_NAMES, format_company_ticker

DISPLAY_ACRONYMS = {
    "ai": "AI",
    "api": "API",
    "cpi": "CPI",
    "ecb": "ECB",
    "etf": "ETF",
    "eu": "EU",
    "fed": "FED",
    "fomc": "FOMC",
    "fx": "FX",
    "gdp": "GDP",
    "ibex": "IBEX",
    "ipo": "IPO",
    "latam": "LATAM",
    "opec": "OPEC",
    "pce": "PCE",
    "ppi": "PPI",
    "sec": "SEC",
    "uk": "UK",
    "us": "US",
    "usa": "USA",
    "uae": "UAE",
}


def _display_label(value: str) -> str:
    """Render human labels while preserving finance acronyms in uppercase."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    text = raw.replace("_", " ").replace("-", " ")
    words: list[str] = []
    for token in text.split():
        if "/" in token:
            parts = [_display_label(part) for part in token.split("/") if part]
            words.append("/".join(parts))
            continue
        lowered = token.lower()
        if lowered in DISPLAY_ACRONYMS:
            words.append(DISPLAY_ACRONYMS[lowered])
        else:
            words.append(token.capitalize())
    return " ".join(words)


def normalize_profile_name(profile_name: str) -> str:
    return (profile_name or "default_user").strip() or "default_user"


def build_profile_state(settings: Settings, profile_name: str) -> dict[str, Any]:
    """Build effective state for UI/API views (effective + overrides + holdings + catalogs)."""
    normalized_profile = normalize_profile_name(profile_name)
    profile = _load_profile_defaults(settings, normalized_profile)

    overrides = get_preferences(normalized_profile)
    holdings = [holding.model_dump(mode="json") for holding in profile.portfolio_holdings]
    catalogs = _build_followables_catalog(settings=settings, profile=profile)
    metadata = _build_profile_metadata(profile=profile, profile_name=normalized_profile)
    validations = _build_profile_validations(profile=profile)
    analysis = _build_portfolio_analysis(
        profile=profile,
        settings=settings,
        metadata=metadata,
        validations=validations,
    )

    return {
        "profile": normalized_profile,
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
            },
        },
        "overrides": overrides,
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

    default_regions = {"us", "europe", "asia", "latam", "middle_east", "global"}
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


def _build_portfolio_analysis(
    *,
    profile: UserProfile,
    settings: Settings,
    metadata: dict[str, Any],
    validations: list[dict[str, str]],
) -> dict[str, Any]:
    """Compute presentation-ready analyzer metrics for the settings UI/API."""
    universe = load_sector_universe(settings)
    sector_label_by_key = {
        sector.key: (sector.display_name or _display_label(sector.key))
        for sector in universe.sectors
    }
    ticker_to_sector: dict[str, str] = {}
    for sector in universe.sectors:
        for ticker in sector.key_names:
            ticker_to_sector[ticker.upper()] = sector.key

    ranked_positions = sorted(
        profile.portfolio_holdings,
        key=lambda p: (p.weight_pct is None, -(p.weight_pct or 0.0), p.symbol),
    )
    weighted_positions = [position for position in ranked_positions if position.weight_pct is not None]
    weights_sorted = [float(position.weight_pct) for position in weighted_positions]
    total_weight = float(sum(weights_sorted)) if weights_sorted else None
    gap_to_100 = (100.0 - total_weight) if total_weight is not None else None
    largest_weight = weights_sorted[0] if weights_sorted else None

    top_positions: list[dict[str, Any]] = []
    for position in ranked_positions[:8]:
        sector_key = position.sector_override or ticker_to_sector.get(position.symbol)
        sector_label = sector_label_by_key.get(sector_key, _display_label(sector_key)) if sector_key else None
        weight_pct = _rounded_metric(position.weight_pct, digits=2) if position.weight_pct is not None else None
        share_of_total_pct = None
        if position.weight_pct is not None and total_weight and total_weight > 0:
            share_of_total_pct = _rounded_metric(position.weight_pct / total_weight * 100.0)
        top_positions.append(
            {
                "symbol": position.symbol,
                "weight_pct": weight_pct,
                "weight_display": _format_percent(weight_pct, digits=2),
                "share_of_total_pct": share_of_total_pct,
                "share_of_total_display": _format_percent(share_of_total_pct),
                "bucket": position.bucket or None,
                "bucket_label": _display_or_not_provided(_display_label(position.bucket) if position.bucket else ""),
                "sector": sector_key,
                "sector_label": _display_or_not_provided(sector_label or ""),
                "bar_pct": _bar_pct(weight_pct, largest_weight),
            }
        )

    largest_position = top_positions[0] if top_positions else None
    concentration = _build_concentration_rows(weights_sorted, total_weight)
    sector_exposure = _build_sector_comparison_rows(profile, sector_label_by_key)
    region_emphasis = _build_region_rows(profile)
    bucket_allocation = _build_bucket_rows(profile)
    analyzer_warnings = _build_analyzer_warning_rows(validations)

    top_sector_row = next((row for row in sector_exposure["rows"] if row["portfolio_pct"] and row["portfolio_pct"] > 0), None)
    top_region_row = next((row for row in region_emphasis["rows"] if row["normalized_pct"] and row["normalized_pct"] > 0), None)

    delivery_enabled = {
        "morning": bool(profile.channels_for("morning")),
        "intraday": bool(profile.channels_for("intraday")),
        "breaking": bool(profile.channels_for("breaking")),
    }
    watchlist_rows = [
        {"label": "Primary", "count": len(profile.watchlist_primary)},
        {"label": "Secondary", "count": len(profile.watchlist_secondary)},
        {"label": "Monitor", "count": len(profile.watchlist_monitor)},
    ]
    max_watch_count = max((row["count"] for row in watchlist_rows), default=0)

    top5_weight = concentration["top5_weight_pct"]
    top5_share = concentration["top5_share_of_total_pct"]

    return {
        "overview": {
            "profile": profile.name,
            "timezone": profile.timezone,
            "home_region_label": _display_label(profile.home_region),
            "holdings_count": len(profile.portfolio_holdings),
            "watchlist_total": (
                len(profile.watchlist_primary)
                + len(profile.watchlist_secondary)
                + len(profile.watchlist_monitor)
            ),
            "primary_watchlist_count": len(profile.watchlist_primary),
            "active_override_count": len(profile.preference_overrides),
            "next_morning_send_local": metadata["next_morning_send_local"],
            "last_holdings_update_local": metadata["last_holdings_update_local"],
            "last_preferences_update_local": metadata["last_preferences_update_local"],
        },
        "executive_summary": _build_executive_summary(
            profile=profile,
            holdings_count=len(profile.portfolio_holdings),
            total_weight=total_weight,
            largest_position=largest_position,
            top_sector_label=top_sector_row["label"] if top_sector_row else "Not provided",
            top_region_label=top_region_row["label"] if top_region_row else "Not provided",
            next_morning_send_local=metadata["next_morning_send_local"],
        ),
        "warnings": analyzer_warnings,
        "holdings_totals": {
            "weighted_positions": len(weighted_positions),
            "unweighted_positions": len([position for position in ranked_positions if position.weight_pct is None]),
            "total_weight_pct": _rounded_metric(total_weight),
            "total_weight_display": _format_percent(total_weight),
            "gap_to_100_pct": _rounded_metric(gap_to_100),
            "gap_to_100_display": _format_gap(gap_to_100),
            "near_100": bool(total_weight is not None and 95.0 <= total_weight <= 105.0),
        },
        "kpis": {
            "holdings_weight_total_pct": _rounded_metric(total_weight),
            "holdings_weight_total_display": _format_percent(total_weight),
            "holdings_weight_gap_pct": _rounded_metric(gap_to_100),
            "holdings_weight_gap_display": _format_gap(gap_to_100),
            "largest_position": largest_position,
            "top_sector": top_sector_row["key"] if top_sector_row else "",
            "top_sector_label": top_sector_row["label"] if top_sector_row else "Not provided",
            "top_region": top_region_row["key"] if top_region_row else "",
            "top_region_label": top_region_row["label"] if top_region_row else "Not provided",
            "top5_concentration_pct": top5_weight,
            "top5_concentration_display": _format_percent(top5_weight),
            "top5_share_of_holdings_pct": top5_share,
            "top5_share_of_holdings_display": _format_percent(top5_share),
            "watchlist_total": (
                len(profile.watchlist_primary)
                + len(profile.watchlist_secondary)
                + len(profile.watchlist_monitor)
            ),
            "delivery_enabled": delivery_enabled,
            "next_morning_send_local": metadata["next_morning_send_local"],
        },
        "top_positions": top_positions,
        "concentration": concentration,
        "sector_exposure": sector_exposure,
        "region_emphasis": region_emphasis,
        "bucket_allocation": bucket_allocation,
        "timing": {
            "next_morning_send_local": metadata["next_morning_send_local"],
            "last_holdings_update_local": metadata["last_holdings_update_local"],
            "last_preferences_update_local": metadata["last_preferences_update_local"],
        },
        "charts": {
            "sector_comparison": sector_exposure["rows"],
            "region_weights": region_emphasis["rows"],
            "watchlist_priority": watchlist_rows,
            "bucket_allocation": bucket_allocation["rows"],
        },
        "chart_max": {
            "sector": sector_exposure["max_scale_pct"],
            "region": region_emphasis["max_scale_pct"],
            "watchlist": float(max_watch_count),
            "bucket": bucket_allocation["max_scale_pct"],
            "positions": largest_weight or 0.0,
            "concentration": concentration["max_scale_pct"],
        },
        "briefing_impact_preview": _build_briefing_impact_preview(
            profile=profile,
            top_positions=top_positions,
            top_sector_label=top_sector_row["label"] if top_sector_row else "Not provided",
            top_region_label=top_region_row["label"] if top_region_row else "Not provided",
            next_morning_send_local=metadata["next_morning_send_local"],
        ),
    }


def _build_sector_comparison_rows(
    profile: UserProfile,
    sector_label_by_key: dict[str, str],
) -> dict[str, Any]:
    coverage_total = sum(float(weight) for weight in profile.sector_weights.values() if float(weight) > 0)
    keys = set(profile.portfolio_sector_weights.keys()) | set(profile.sector_weights.keys())
    rows: list[dict[str, Any]] = []
    for key in sorted(keys):
        portfolio_pct = float(profile.portfolio_sector_weights.get(key, 0.0) * 100.0)
        coverage_weight_raw = float(profile.sector_weights.get(key, 0.0))
        coverage_pct = (coverage_weight_raw / coverage_total * 100.0) if coverage_total > 0 else 0.0
        rows.append(
            {
                "key": key,
                "label": sector_label_by_key.get(key, _display_label(key)),
                "portfolio_pct": _rounded_metric(portfolio_pct),
                "coverage_pct": _rounded_metric(coverage_pct),
                "coverage_weight": _rounded_metric(coverage_weight_raw, digits=2),
                "portfolio_display": _format_percent(portfolio_pct),
                "coverage_display": _format_percent(coverage_pct),
            }
        )
    rows.sort(key=lambda row: max(row["portfolio_pct"] or 0.0, row["coverage_pct"] or 0.0), reverse=True)
    trimmed = rows[:8]
    max_scale = max(
        (
            max(row["portfolio_pct"] or 0.0, row["coverage_pct"] or 0.0)
            for row in trimmed
        ),
        default=0.0,
    )
    for row in trimmed:
        row["portfolio_bar_pct"] = _bar_pct(row["portfolio_pct"], max_scale)
        row["coverage_bar_pct"] = _bar_pct(row["coverage_pct"], max_scale)
    return {
        "rows": trimmed,
        "max_scale_pct": _rounded_metric(max_scale) or 0.0,
    }


def _build_region_rows(profile: UserProfile) -> dict[str, Any]:
    positive_total = sum(float(value) for value in profile.coverage_weights.values() if float(value) > 0)
    rows = [
        {
            "key": key,
            "label": _display_label(key),
            "value": _rounded_metric(float(value), digits=2),
            "normalized_pct": _rounded_metric((float(value) / positive_total * 100.0) if positive_total > 0 else 0.0),
        }
        for key, value in profile.coverage_weights.items()
    ]
    rows.sort(key=lambda row: (row["value"] or 0.0, row["label"]), reverse=True)
    trimmed = rows[:8]
    max_scale = max((row["normalized_pct"] or 0.0 for row in trimmed), default=0.0)
    for row in trimmed:
        row["display"] = _format_percent(row["normalized_pct"])
        row["bar_pct"] = _bar_pct(row["normalized_pct"], max_scale)
    return {
        "rows": trimmed,
        "max_scale_pct": _rounded_metric(max_scale) or 0.0,
    }


def _build_bucket_rows(profile: UserProfile) -> dict[str, Any]:
    bucket_totals: dict[str, float] = {}
    bucket_counts: dict[str, int] = {}
    unweighted_count = 0
    for position in profile.portfolio_holdings:
        bucket = (position.bucket or "unlabeled").strip().lower()
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if position.weight_pct is None:
            unweighted_count += 1
            continue
        bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + float(position.weight_pct)
    max_value = max(bucket_totals.values(), default=0.0)
    rows = [
        {
            "key": bucket,
            "label": _display_label(bucket),
            "value": _rounded_metric(value),
            "display": _format_percent(value),
            "holdings_count": bucket_counts.get(bucket, 0),
            "bar_pct": _bar_pct(value, max_value),
        }
        for bucket, value in bucket_totals.items()
    ]
    rows.sort(key=lambda row: ((row["value"] or 0.0), row["holdings_count"]), reverse=True)
    return {
        "rows": rows[:6],
        "unweighted_positions": unweighted_count,
        "max_scale_pct": _rounded_metric(max_value) or 0.0,
    }


def _build_concentration_rows(weights_sorted: list[float], total_weight: float | None) -> dict[str, Any]:
    def _sum_slice(limit: int) -> float | None:
        if not weights_sorted:
            return None
        return float(sum(weights_sorted[:limit]))

    top1_weight = _sum_slice(1)
    top3_weight = _sum_slice(3)
    top5_weight = _sum_slice(5)
    tail_weight = None
    if total_weight is not None and top5_weight is not None:
        tail_weight = max(total_weight - top5_weight, 0.0)

    rows = [
        {"key": "top1", "label": "Top 1", "value_pct": _rounded_metric(top1_weight)},
        {"key": "top3", "label": "Top 3", "value_pct": _rounded_metric(top3_weight)},
        {"key": "top5", "label": "Top 5", "value_pct": _rounded_metric(top5_weight)},
        {"key": "tail", "label": "Residual Tail", "value_pct": _rounded_metric(tail_weight)},
    ]
    max_scale = max((row["value_pct"] or 0.0 for row in rows), default=0.0)
    for row in rows:
        share = None
        if row["value_pct"] is not None and total_weight and total_weight > 0:
            share = row["value_pct"] / total_weight * 100.0
        row["share_of_total_pct"] = _rounded_metric(share)
        row["display"] = _format_percent(row["value_pct"])
        row["bar_pct"] = _bar_pct(row["value_pct"], max_scale)

    return {
        "rows": rows,
        "top1_weight_pct": _rounded_metric(top1_weight),
        "top3_weight_pct": _rounded_metric(top3_weight),
        "top5_weight_pct": _rounded_metric(top5_weight),
        "tail_weight_pct": _rounded_metric(tail_weight),
        "top1_share_of_total_pct": rows[0]["share_of_total_pct"],
        "top3_share_of_total_pct": rows[1]["share_of_total_pct"],
        "top5_share_of_total_pct": rows[2]["share_of_total_pct"],
        "tail_share_of_total_pct": rows[3]["share_of_total_pct"],
        "max_scale_pct": _rounded_metric(max_scale) or 0.0,
    }


def _build_analyzer_warning_rows(validations: list[dict[str, str]]) -> list[dict[str, str]]:
    warning_titles = {
        "holdings_weight_sum": "Holdings Weight Check",
        "watchlist_primary_empty": "Primary Watchlist Empty",
        "region_weights_empty": "Region Emphasis Missing",
    }
    analyzer_codes = {
        "holdings_weight_sum",
        "watchlist_primary_empty",
        "region_weights_empty",
    }
    warnings: list[dict[str, str]] = []
    for item in validations:
        code = item.get("code", "")
        if code.startswith("delivery_missing_"):
            title = "Delivery Channel Missing"
        elif code in analyzer_codes:
            title = warning_titles.get(code, "Analyzer Warning")
        else:
            continue
        warnings.append(
            {
                "code": code,
                "title": title,
                "message": item.get("message", ""),
            }
        )
    return warnings


def _build_briefing_impact_preview(
    *,
    profile: UserProfile,
    top_positions: list[dict[str, Any]],
    top_sector_label: str,
    top_region_label: str,
    next_morning_send_local: str,
) -> str:
    focus_positions = [
        f"{row['symbol']} ({row['weight_display']})"
        for row in top_positions[:2]
        if row.get("weight_pct") is not None
    ]
    focus_text = (
        f"Direct portfolio attention will skew toward {', '.join(focus_positions)}."
        if focus_positions
        else "No weighted holdings are configured yet, so direct holding emphasis is limited."
    )
    sector_text = top_sector_label if top_sector_label != "Not provided" else "balanced sector coverage"
    region_text = top_region_label if top_region_label != "Not provided" else _display_label(profile.home_region)
    primary_count = len(profile.watchlist_primary)
    morning_channels = ", ".join(profile.channels_for("morning")) or "no enabled morning channels"
    portfolio_focus = (
        "Portfolio focus remains enabled in the morning brief."
        if profile.morning_section_enabled("portfolio_focus")
        else "Portfolio focus is disabled in the morning brief."
    )
    return (
        f"Tomorrow's brief is set to lead with {region_text} context and {sector_text} exposure. "
        f"{focus_text} Primary watchlist has {primary_count} symbol(s). "
        f"Next scheduled morning send is {next_morning_send_local} via {morning_channels}. "
        f"{portfolio_focus}"
    )


def _build_executive_summary(
    *,
    profile: UserProfile,
    holdings_count: int,
    total_weight: float | None,
    largest_position: dict[str, Any] | None,
    top_sector_label: str,
    top_region_label: str,
    next_morning_send_local: str,
) -> str:
    largest_text = "No weighted lead position is configured yet"
    if largest_position and largest_position.get("weight_display") != "Not provided":
        largest_text = (
            f"{largest_position['symbol']} is the anchor holding at {largest_position['weight_display']}"
        )

    sector_text = top_sector_label if top_sector_label != "Not provided" else "balanced sector exposure"
    region_text = top_region_label if top_region_label != "Not provided" else _display_label(profile.home_region)
    total_weight_text = _format_percent(total_weight) if total_weight is not None else "Not provided"
    return (
        f"{holdings_count} active holding(s), {total_weight_text} weighted in total. "
        f"{largest_text}. Sector tilt is strongest in {sector_text}, "
        f"while editorial emphasis leads with {region_text}. "
        f"Next local morning brief is due at {next_morning_send_local}."
    )


def _rounded_metric(value: float | None, *, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _format_percent(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "Not provided"
    return f"{float(value):.{digits}f}%"


def _format_gap(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "Not provided"
    if abs(float(value)) < 0.05:
        return "0.0% vs 100%"
    direction = "below" if value > 0 else "above"
    return f"{abs(float(value)):.{digits}f}% {direction} 100%"


def _display_or_not_provided(value: str | None) -> str:
    text = str(value or "").strip()
    return text or "Not provided"


def _bar_pct(value: float | None, max_value: float | None) -> float:
    if value is None or not max_value or max_value <= 0:
        return 0.0
    return round(max(0.0, min(float(value) / float(max_value) * 100.0, 100.0)), 1)


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
