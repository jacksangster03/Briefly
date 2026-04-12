"""Phase 4.3 control-plane service for web/API settings management."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import yaml

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

    with tempfile.TemporaryDirectory(prefix="mbb_holdings_") as temp_dir:
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
