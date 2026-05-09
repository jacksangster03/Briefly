"""Phase 4.2 control-plane preference persistence and normalization."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Callable

from app.db.models import UserPreference as UserPreferenceRow
from app.db.session import get_session

ALLOWED_CHANNELS = {"telegram", "email"}
ALLOWED_SESSION_MODES = {"quiet", "default", "active"}
ALLOWED_SESSION_KEYS = {
    "morning", "europe_midday", "us_pre_open",
    "us_intraday_risk", "into_close", "closing_wrap",
}
ALLOWED_MORNING_SECTIONS = {
    "market_setup",
    "macro_context",
    "global_news",
    "top_themes",
    "portfolio_focus",
    "sector_scan",
    "watchlist",
    "watchlist_snapshot",
}
ALLOWED_HOME_REGIONS = {
    "us",
    "europe",
    "asia",
    "latam",
    "middle_east",
    "global",
}
ALLOWED_EMAIL_DENSITY_MODES = {"desk", "full"}
ALLOWED_HEALTHCARE_SEVERITIES = {"low", "medium", "high", "critical"}
ALLOWED_VERTICAL_MODES = {"off", "watch", "active", "portfolio_linked"}
ALLOWED_WEEKEND_MODES = {"off", "saturday_only", "saturday_and_sunday_news"}
ALLOWED_SUNDAY_NEWS_MATERIALITY = {"material_only", "always_short"}


def _normalize_profile(profile_name: str) -> str:
    return (profile_name or "default_user").strip() or "default_user"


def _normalize_key(key: str) -> str:
    normalized = (key or "").strip().lower()
    if not normalized:
        raise ValueError("Preference key is required")
    return normalized


def _normalize_tickers(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise ValueError("Ticker list must be a comma-separated string or JSON array")
    return [ticker.upper() for ticker in candidates]


def _normalize_weight_map(value: Any, *, label: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    normalized: dict[str, float] = {}
    for key, raw in value.items():
        map_key = str(key).strip().lower()
        if not map_key:
            continue
        weight = float(raw)
        if weight < 0:
            raise ValueError(f"{label} cannot be negative")
        normalized[map_key] = weight
    return normalized


def _normalize_sector_weights(value: Any) -> dict[str, float]:
    return _normalize_weight_map(value, label="Sector weights")


def _normalize_region_weights(value: Any) -> dict[str, float]:
    return _normalize_weight_map(value, label="Region weights")


def _normalize_home_region(value: Any) -> str:
    region = str(value).strip().lower().replace(" ", "_")
    if not region:
        raise ValueError("Home region is required")
    if region not in ALLOWED_HOME_REGIONS:
        raise ValueError(
            f"Unsupported home region '{region}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_HOME_REGIONS))}"
        )
    return region


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "on"}:
            return True
        if token in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValueError("Boolean preference must be true/false")


def _normalize_time(value: Any) -> str:
    raw = str(value).strip()
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        raise ValueError("Time must be in HH:MM format")
    hour, minute = raw.split(":")
    hh = int(hour)
    mm = int(minute)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("Time must be in 24h range HH:MM")
    return f"{hh:02d}:{mm:02d}"


def _normalize_email_density_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in ALLOWED_EMAIL_DENSITY_MODES:
        raise ValueError(
            f"Unsupported email density mode '{mode}'. Allowed: {', '.join(sorted(ALLOWED_EMAIL_DENSITY_MODES))}"
        )
    return mode


def _normalize_channels(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip().lower() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        candidates = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        raise ValueError("Channels must be a comma-separated string or JSON array")
    invalid = [item for item in candidates if item not in ALLOWED_CHANNELS]
    if invalid:
        raise ValueError(f"Unsupported channels: {', '.join(invalid)}")
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for channel in candidates:
        if channel in seen:
            continue
        seen.add(channel)
        unique.append(channel)
    return unique


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("Value must be a comma-separated string or JSON array")


def _normalize_session_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in ALLOWED_SESSION_MODES:
        raise ValueError(
            f"Unsupported session_mode '{mode}'. Allowed: {', '.join(sorted(ALLOWED_SESSION_MODES))}"
        )
    return mode


def _normalize_session_key_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                candidates = [str(item).strip().lower() for item in parsed if str(item).strip()]
            else:
                candidates = [item.strip().lower() for item in value.split(",") if item.strip()]
        except Exception:
            candidates = [item.strip().lower() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        candidates = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        raise ValueError("always_send_sessions must be a comma-separated string or JSON array")
    invalid = [k for k in candidates if k not in ALLOWED_SESSION_KEYS]
    if invalid:
        raise ValueError(
            f"Unsupported session key(s): {', '.join(invalid)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_SESSION_KEYS))}"
        )
    seen: set[str] = set()
    unique: list[str] = []
    for key in candidates:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def _normalize_healthcare_severity(value: Any) -> str:
    severity = str(value).strip().lower()
    if severity not in ALLOWED_HEALTHCARE_SEVERITIES:
        raise ValueError(
            f"Unsupported healthcare severity '{severity}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_HEALTHCARE_SEVERITIES))}"
        )
    return severity


def _normalize_vertical_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ALLOWED_VERTICAL_MODES:
        raise ValueError(
            f"Unsupported vertical mode '{mode}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_VERTICAL_MODES))}"
        )
    return mode


def _normalize_weekend_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ALLOWED_WEEKEND_MODES:
        raise ValueError(
            f"Unsupported weekend mode '{mode}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_WEEKEND_MODES))}"
        )
    return mode


def _normalize_sunday_news_materiality(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ALLOWED_SUNDAY_NEWS_MATERIALITY:
        raise ValueError(
            f"Unsupported sunday_news_materiality '{mode}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_SUNDAY_NEWS_MATERIALITY))}"
        )
    return mode


def _normalize_positive_int(value: Any) -> int:
    val = int(value)
    if val < 0:
        raise ValueError("Value must be >= 0")
    return val


def _normalize_morning_section_key(key: str) -> str:
    section = key.replace("sections.morning.", "", 1)
    if section not in ALLOWED_MORNING_SECTIONS:
        raise ValueError(
            f"Unsupported morning section '{section}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_MORNING_SECTIONS))}"
        )
    return key


def _identity(value: Any) -> Any:
    return value


PREFERENCE_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "watchlist.primary": _normalize_tickers,
    "watchlist.secondary": _normalize_tickers,
    "watchlist.monitor": _normalize_tickers,
    "sector.weights": _normalize_sector_weights,
    "coverage.home_region": _normalize_home_region,
    "coverage.weights": _normalize_region_weights,
    "delivery.morning_channels": _normalize_channels,
    "delivery.intraday_channels": _normalize_channels,
    "delivery.breaking_channels": _normalize_channels,
    "delivery.morning_brief_time": _normalize_time,
    "delivery.hourly_updates": _normalize_bool,
    "delivery.breaking_alerts": _normalize_bool,
    "delivery.intraday_global_risk_enabled": _normalize_bool,
    "delivery.llm_email_morning": _normalize_bool,
    "delivery.llm_shadow_mode": _normalize_bool,
    "delivery.quiet_hours_start": _normalize_time,
    "delivery.quiet_hours_end": _normalize_time,
    "delivery.session_mode": _normalize_session_mode,
    "delivery.always_send_sessions": _normalize_session_key_list,
    "delivery.suppress_low_materiality": _normalize_bool,
    "delivery.email_density_mode": _normalize_email_density_mode,
    "delivery.weekend_mode": _normalize_weekend_mode,
    "delivery.sunday_news_materiality": _normalize_sunday_news_materiality,
    "sections.morning.market_setup": _normalize_bool,
    "sections.morning.macro_context": _normalize_bool,
    "sections.morning.global_news": _normalize_bool,
    "sections.morning.top_themes": _normalize_bool,
    "sections.morning.portfolio_focus": _normalize_bool,
    "sections.morning.sector_scan": _normalize_bool,
    "sections.morning.watchlist": _normalize_bool,
    "sections.morning.watchlist_snapshot": _normalize_bool,
    "sections.global_news": _normalize_bool,
    "healthcare.enabled": _normalize_bool,
    "healthcare.mode": _normalize_vertical_mode,
    "healthcare.max_items_morning": _normalize_positive_int,
    "healthcare.max_items_intraday": _normalize_positive_int,
    "healthcare.breaking_alerts": _normalize_bool,
    "healthcare.themes": _normalize_string_list,
    "healthcare.tickers": _normalize_tickers,
    "healthcare.assets": _normalize_string_list,
    "healthcare.minimum_severity_morning": _normalize_healthcare_severity,
    "healthcare.minimum_severity_intraday": _normalize_healthcare_severity,
    "healthcare.minimum_severity_breaking": _normalize_healthcare_severity,
    "snapshots.enabled": _normalize_bool,
    "snapshots.retention_days": _normalize_positive_int,
    "snapshots.store_email_html": _normalize_bool,
    "snapshots.store_failed_attempts": _normalize_bool,
}


def supported_preference_keys() -> list[str]:
    return sorted(PREFERENCE_NORMALIZERS.keys())


def normalize_preference_value(pref_key: str, value: Any) -> Any:
    key = _normalize_key(pref_key)
    if key.startswith("sections.morning."):
        _normalize_morning_section_key(key)
    normalizer = PREFERENCE_NORMALIZERS.get(key, _identity)
    if normalizer is _identity:
        raise ValueError(
            f"Unsupported preference key '{key}'. "
            f"Supported keys: {', '.join(supported_preference_keys())}"
        )
    return normalizer(value)


def set_preference(profile_name: str, pref_key: str, value: Any) -> Any:
    """Upsert a profile preference override and return the normalized value."""
    profile = _normalize_profile(profile_name)
    key = _normalize_key(pref_key)
    normalized_value = normalize_preference_value(key, value)
    now = datetime.now(timezone.utc)

    with get_session() as session:
        row = (
            session.query(UserPreferenceRow)
            .filter(
                UserPreferenceRow.profile_name == profile,
                UserPreferenceRow.pref_key == key,
            )
            .one_or_none()
        )
        if row is None:
            session.add(
                UserPreferenceRow(
                    profile_name=profile,
                    pref_key=key,
                    pref_value=normalized_value,
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.pref_value = normalized_value
            row.active = True
            row.updated_at = now

    return normalized_value


def get_preferences(profile_name: str) -> dict[str, Any]:
    """Load active preference overrides for a profile."""
    profile = _normalize_profile(profile_name)
    with get_session() as session:
        rows = (
            session.query(UserPreferenceRow)
            .filter(
                UserPreferenceRow.profile_name == profile,
                UserPreferenceRow.active.is_(True),
            )
            .order_by(UserPreferenceRow.pref_key.asc())
            .all()
        )
    return {row.pref_key: row.pref_value for row in rows}


def unset_preference(profile_name: str, pref_key: str) -> bool:
    """Deactivate a preference override. Returns True if an override was removed."""
    profile = _normalize_profile(profile_name)
    key = _normalize_key(pref_key)
    now = datetime.now(timezone.utc)
    with get_session() as session:
        row = (
            session.query(UserPreferenceRow)
            .filter(
                UserPreferenceRow.profile_name == profile,
                UserPreferenceRow.pref_key == key,
                UserPreferenceRow.active.is_(True),
            )
            .one_or_none()
        )
        if row is None:
            return False
        row.active = False
        row.updated_at = now
    return True


def clear_preferences(profile_name: str) -> int:
    """Deactivate all active overrides for a profile and return affected row count."""
    profile = _normalize_profile(profile_name)
    now = datetime.now(timezone.utc)
    with get_session() as session:
        count = (
            session.query(UserPreferenceRow)
            .filter(
                UserPreferenceRow.profile_name == profile,
                UserPreferenceRow.active.is_(True),
            )
            .update(
                {
                    UserPreferenceRow.active: False,
                    UserPreferenceRow.updated_at: now,
                },
                synchronize_session=False,
            )
        )
    return int(count or 0)


def parse_cli_value(raw_value: str) -> Any:
    """Parse CLI preference values, accepting JSON or raw strings."""
    raw = (raw_value or "").strip()
    if not raw:
        raise ValueError("Preference value cannot be empty")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
