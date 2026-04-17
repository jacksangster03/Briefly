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
from app.portfolio.service import load_active_holdings, replace_holdings_snapshot
from app.settings import Settings
from app.universe.sector_universe import load_sector_universe
from app.universe.ticker_metadata import TICKER_DISPLAY_NAMES, format_company_ticker

def _display_label(value: str) -> str:
    """Render human labels while preserving finance acronyms in uppercase."""
    return analyzer_display_label(value)


def normalize_profile_name(profile_name: str) -> str:
    return (profile_name or "default_user").strip() or "default_user"


def build_profile_state(settings: Settings, profile_name: str) -> dict[str, Any]:
    """Build effective state for UI/API views (effective + overrides + holdings + catalogs)."""
    normalized_profile = normalize_profile_name(profile_name)
    profile = _load_profile_defaults(settings, normalized_profile)

    overrides = get_preferences(normalized_profile)
    catalogs = _build_followables_catalog(settings=settings, profile=profile)
    metadata = _build_profile_metadata(profile=profile, profile_name=normalized_profile)
    validations = _build_profile_validations(profile=profile)
    analysis = build_analyzer_payload(
        profile=profile,
        settings=settings,
        metadata=metadata,
        validations=validations,
    )
    holdings = _build_holdings_view(profile=profile, settings=settings)
    metadata["holdings_snapshot_summary"] = _build_holdings_snapshot_summary(profile, analysis)
    metadata["override_summaries"] = _build_override_summaries(overrides)
    metadata["delivery_summary"] = _build_delivery_summary(profile)

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
