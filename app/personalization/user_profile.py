"""User profile loader: reads personalisation from YAML config."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.logger import get_logger
from app.settings import Settings

logger = get_logger("user_profile")


class UserProfile:
    """User preferences and personalisation, loaded from YAML."""

    def __init__(
        self,
        name: str = "default_user",
        timezone: str = "Europe/Madrid",
        home_region: str = "spain",
        message_depth: str = "standard",
        primary_channel: str = "telegram",
        backup_channel: str = "email",
        coverage_weights: dict[str, float] | None = None,
        sector_weights: dict[str, float] | None = None,
        delivery: dict | None = None,
        style: dict | None = None,
        watchlist_primary: list[str] | None = None,
        watchlist_secondary: list[str] | None = None,
        watchlist_monitor: list[str] | None = None,
    ):
        self.name = name
        self.timezone = timezone
        self.home_region = home_region
        self.message_depth = message_depth
        self.primary_channel = primary_channel
        self.backup_channel = backup_channel
        self.coverage_weights = coverage_weights or {"us": 1.0, "europe": 0.75}
        self.sector_weights = sector_weights or {"technology": 1.0}
        self.delivery = delivery or {}
        self.style = style or {}
        self.watchlist_primary = watchlist_primary or []
        self.watchlist_secondary = watchlist_secondary or []
        self.watchlist_monitor = watchlist_monitor or []

    @property
    def all_watchlist_tickers(self) -> list[str]:
        """All tickers across primary, secondary, and monitor watchlists."""
        return self.watchlist_primary + self.watchlist_secondary + self.watchlist_monitor

    @property
    def morning_brief_time(self) -> str:
        return self.delivery.get("morning_brief_time", "12:30")

    @property
    def hourly_updates_enabled(self) -> bool:
        return self.delivery.get("hourly_updates", True)

    @property
    def breaking_alerts_enabled(self) -> bool:
        return self.delivery.get("breaking_alerts", True)

    @property
    def quiet_hours(self) -> tuple[str, str]:
        return (
            self.delivery.get("quiet_hours_start", "23:00"),
            self.delivery.get("quiet_hours_end", "07:00"),
        )

    @property
    def is_concise(self) -> bool:
        return self.style.get("concise", True)

    @property
    def numbers_first(self) -> bool:
        return self.style.get("numbers_first", True)


def load_user_profile(settings: Settings) -> UserProfile:
    """Load user profile from YAML config files.

    Tries configs/user_profile.yaml first, falls back to the example file.
    Merges in watchlist data from configs/watchlists.yaml if present.
    """
    configs_dir = Path(settings.configs_dir)

    # Load user profile
    profile_path = configs_dir / "user_profile.yaml"
    if not profile_path.exists():
        profile_path = configs_dir / "user_profile.example.yaml"

    if not profile_path.exists():
        logger.warning("No user profile found; using defaults")
        return UserProfile()

    with open(profile_path) as f:
        data = yaml.safe_load(f) or {}

    user_data = data.get("user", {})
    profile = UserProfile(
        name=user_data.get("name", "default_user"),
        timezone=user_data.get("timezone", settings.timezone),
        home_region=user_data.get("home_region", "spain"),
        message_depth=user_data.get("message_depth", "standard"),
        primary_channel=user_data.get("primary_channel", "telegram"),
        backup_channel=user_data.get("backup_channel", "email"),
        coverage_weights=data.get("coverage_weights", {}),
        sector_weights=data.get("sector_weights", {}),
        delivery=data.get("delivery", {}),
        style=data.get("style", {}),
    )

    # Merge watchlist
    watchlist_path = configs_dir / "watchlists.yaml"
    if not watchlist_path.exists():
        watchlist_path = configs_dir / "watchlists.example.yaml"

    if watchlist_path.exists():
        with open(watchlist_path) as f:
            wl = yaml.safe_load(f) or {}
        profile.watchlist_primary = wl.get("primary", [])
        profile.watchlist_secondary = wl.get("secondary", [])
        profile.watchlist_monitor = wl.get("monitor", [])

    logger.info(
        "Loaded profile '%s' (tz=%s, %d watchlist tickers, %d sectors)",
        profile.name,
        profile.timezone,
        len(profile.all_watchlist_tickers),
        len(profile.sector_weights),
    )
    return profile
