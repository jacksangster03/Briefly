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
    analysis = _build_portfolio_analysis(profile=profile)

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
            },
            "delivery": {
                "morning_channels": profile.channels_for("morning"),
                "intraday_channels": profile.channels_for("intraday"),
                "breaking_channels": profile.channels_for("breaking"),
                "morning_brief_time": profile.morning_brief_time,
                "hourly_updates": profile.hourly_updates_enabled,
                "breaking_alerts": profile.breaking_alerts_enabled,
                "quiet_hours_start": profile.quiet_hours[0],
                "quiet_hours_end": profile.quiet_hours[1],
            },
            "sections": {
                "market_setup": profile.morning_section_enabled("market_setup"),
                "macro_context": profile.morning_section_enabled("macro_context"),
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
            "label": region.replace("_", " ").title(),
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


def _build_portfolio_analysis(*, profile: UserProfile) -> dict[str, Any]:
    """Compute concise analysis metrics for overview cards/charts."""
    weights = [
        float(position.weight_pct)
        for position in profile.portfolio_holdings
        if position.weight_pct is not None
    ]
    weights_sorted = sorted(weights, reverse=True)
    total_weight = float(sum(weights))
    top5_weight = float(sum(weights_sorted[:5])) if weights_sorted else 0.0
    top5_share = (top5_weight / total_weight * 100.0) if total_weight > 0 else 0.0

    largest_position = None
    if profile.portfolio_holdings:
        ranked_positions = sorted(
            profile.portfolio_holdings,
            key=lambda p: (p.weight_pct is not None, p.weight_pct or 0.0),
            reverse=True,
        )
        head = ranked_positions[0]
        largest_position = {
            "symbol": head.symbol,
            "weight_pct": float(head.weight_pct or 0.0) if head.weight_pct is not None else None,
            "bucket": head.bucket or "",
        }

    top_sector = ""
    if profile.portfolio_sector_weights:
        top_sector = max(profile.portfolio_sector_weights.items(), key=lambda item: item[1])[0]
    top_region = ""
    if profile.coverage_weights:
        top_region = max(profile.coverage_weights.items(), key=lambda item: item[1])[0]

    delivery_enabled = {
        "morning": bool(profile.channels_for("morning")),
        "intraday": bool(profile.channels_for("intraday")),
        "breaking": bool(profile.channels_for("breaking")),
    }

    sector_comparison = _build_sector_comparison_rows(profile)
    region_rows = _build_region_rows(profile)
    watchlist_rows = [
        {"label": "Primary", "count": len(profile.watchlist_primary)},
        {"label": "Secondary", "count": len(profile.watchlist_secondary)},
        {"label": "Monitor", "count": len(profile.watchlist_monitor)},
    ]
    bucket_rows = _build_bucket_rows(profile)
    max_sector_pct = max((row["portfolio_pct"] for row in sector_comparison), default=0.0)
    max_sector_weight = max((row["coverage_weight"] for row in sector_comparison), default=0.0)
    max_region_weight = max((row["value"] for row in region_rows), default=0.0)
    max_watch_count = max((row["count"] for row in watchlist_rows), default=0)
    max_bucket_value = max((row["value"] for row in bucket_rows), default=0.0)

    return {
        "kpis": {
            "holdings_weight_total_pct": total_weight,
            "holdings_weight_gap_pct": 100.0 - total_weight if total_weight > 0 else None,
            "largest_position": largest_position,
            "top_sector": top_sector,
            "top_region": top_region,
            "top5_concentration_pct": top5_share,
            "watchlist_total": (
                len(profile.watchlist_primary)
                + len(profile.watchlist_secondary)
                + len(profile.watchlist_monitor)
            ),
            "delivery_enabled": delivery_enabled,
        },
        "charts": {
            "sector_comparison": sector_comparison,
            "region_weights": region_rows,
            "watchlist_priority": watchlist_rows,
            "bucket_allocation": bucket_rows,
        },
        "chart_max": {
            "sector": max(max_sector_pct, max_sector_weight),
            "region": max_region_weight,
            "watchlist": float(max_watch_count),
            "bucket": max_bucket_value,
        },
        "briefing_impact_preview": _build_briefing_impact_preview(profile),
    }


def _build_sector_comparison_rows(profile: UserProfile) -> list[dict[str, Any]]:
    keys = set(profile.portfolio_sector_weights.keys()) | set(profile.sector_weights.keys())
    rows: list[dict[str, Any]] = []
    for key in sorted(keys):
        rows.append(
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "portfolio_pct": float(profile.portfolio_sector_weights.get(key, 0.0) * 100.0),
                "coverage_weight": float(profile.sector_weights.get(key, 0.0)),
            }
        )
    rows.sort(key=lambda row: max(row["portfolio_pct"], row["coverage_weight"]), reverse=True)
    return rows[:8]


def _build_region_rows(profile: UserProfile) -> list[dict[str, Any]]:
    rows = [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "value": float(value),
        }
        for key, value in profile.coverage_weights.items()
    ]
    rows.sort(key=lambda row: row["value"], reverse=True)
    return rows[:8]


def _build_bucket_rows(profile: UserProfile) -> list[dict[str, Any]]:
    bucket_totals: dict[str, float] = {}
    unweighted_count = 0
    for position in profile.portfolio_holdings:
        bucket = (position.bucket or "unlabeled").strip().lower()
        if position.weight_pct is None:
            unweighted_count += 1
            continue
        bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + float(position.weight_pct)
    rows = [
        {"label": bucket.replace("_", " ").title(), "value": value}
        for bucket, value in bucket_totals.items()
    ]
    rows.sort(key=lambda row: row["value"], reverse=True)
    if unweighted_count > 0:
        rows.append({"label": "Unweighted Positions", "value": float(unweighted_count)})
    return rows[:6]


def _build_briefing_impact_preview(profile: UserProfile) -> str:
    primary_count = len(profile.watchlist_primary)
    top_region = ""
    if profile.coverage_weights:
        top_region = max(profile.coverage_weights.items(), key=lambda item: item[1])[0].replace("_", " ").title()
    top_sectors = sorted(profile.sector_weights.items(), key=lambda item: float(item[1]), reverse=True)[:3]
    sector_text = ", ".join(
        key.replace("_", " ").title()
        for key, weight in top_sectors
        if float(weight) > 0
    )
    region_text = top_region or profile.home_region.replace("_", " ").title()
    if not sector_text:
        sector_text = "balanced sector coverage"
    return (
        f"Current settings emphasize {region_text} context and {sector_text}. "
        f"Primary watchlist contains {primary_count} symbol(s), which drives strongest ranking impact."
    )


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
