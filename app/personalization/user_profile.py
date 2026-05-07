"""User profile loader: reads personalisation from YAML config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.logger import get_logger
from app.briefing.market_regions import infer_market_profile
from app.schemas.portfolio import PortfolioHolding
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
        portfolio_holdings: list[PortfolioHolding] | None = None,
        portfolio_sector_weights: dict[str, float] | None = None,
        delivery_channels: dict[str, list[str]] | None = None,
        morning_section_flags: dict[str, bool] | None = None,
        preference_overrides: dict[str, Any] | None = None,
        healthcare: dict[str, Any] | None = None,
        country: str | None = None,
        market_region: str | None = None,
        sub_region: str | None = None,
        session_template: str | None = None,
        session_intensity: str | None = None,
        primary_markets: list[str] | None = None,
        secondary_markets: list[str] | None = None,
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
        self.portfolio_holdings = portfolio_holdings or []
        self.portfolio_sector_weights = portfolio_sector_weights or {}
        self.delivery_channels = delivery_channels or {}
        self.morning_section_flags = morning_section_flags or {}
        self.preference_overrides = preference_overrides or {}
        self.healthcare = healthcare or {}
        self.country = country or ""
        self.market_region = market_region or ""
        self.sub_region = sub_region or ""
        self.session_template = session_template or ""
        self.session_intensity = session_intensity or "standard"
        self.primary_markets = primary_markets or []
        self.secondary_markets = secondary_markets or []

    @property
    def all_watchlist_tickers(self) -> list[str]:
        """All tickers across primary, secondary, and monitor watchlists."""
        return self.watchlist_primary + self.watchlist_secondary + self.watchlist_monitor

    @property
    def portfolio_symbols(self) -> list[str]:
        """Unique held symbols, ordered by descending position weight when available."""
        weighted = sorted(
            self.portfolio_holdings,
            key=lambda p: (p.weight_pct is not None, p.weight_pct or 0.0),
            reverse=True,
        )
        symbols: list[str] = []
        seen: set[str] = set()
        for position in weighted:
            if position.symbol in seen:
                continue
            seen.add(position.symbol)
            symbols.append(position.symbol)
        return symbols

    @property
    def portfolio_weight_by_ticker(self) -> dict[str, float]:
        """Aggregated position weights by ticker (percent, not fraction)."""
        totals: dict[str, float] = {}
        for position in self.portfolio_holdings:
            if position.weight_pct is None:
                continue
            totals[position.symbol] = totals.get(position.symbol, 0.0) + position.weight_pct
        return totals

    def portfolio_top_positions(self, limit: int = 5) -> list[PortfolioHolding]:
        """Top positions by configured weight."""
        ranked = sorted(
            self.portfolio_holdings,
            key=lambda p: (p.weight_pct is not None, p.weight_pct or 0.0),
            reverse=True,
        )
        return ranked[:limit]

    @property
    def has_portfolio(self) -> bool:
        return bool(self.portfolio_holdings)

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
    def intraday_global_risk_enabled(self) -> bool:
        return self.delivery.get("intraday_global_risk_enabled", True)

    @property
    def session_mode(self) -> str:
        """Delivery cadence mode: quiet | default | active."""
        raw = str(self.delivery.get("session_mode", "default")).strip().lower()
        return raw if raw in {"quiet", "default", "active"} else "default"

    @property
    def always_send_sessions(self) -> list[str]:
        raw = self.delivery.get("always_send_sessions", [])
        if not isinstance(raw, list):
            return []
        return [str(item).strip().lower() for item in raw if str(item).strip()]

    @property
    def suppress_low_materiality(self) -> bool:
        return bool(self.delivery.get("suppress_low_materiality", True))

    @property
    def email_density_mode(self) -> str:
        raw = str(self.delivery.get("email_density_mode", "auto")).strip().lower()
        return raw if raw in {"desk", "full", "auto", "medium"} else "auto"

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

    def channels_for(self, message_type: str) -> list[str]:
        """Return preferred delivery channels for a message type or session key.

        All six canonical session keys (morning, europe_midday, us_pre_open,
        us_intraday_risk, into_close, closing_wrap) are supported. A session key
        with no explicit channel config falls back to the "morning" config, which
        always includes both telegram and email unless the profile overrides it.
        """
        # Map all six canonical session keys to their config key.
        # Non-morning sessions fall back to "morning" if not explicitly configured,
        # ensuring all six sessions default to both telegram and email.
        _CANONICAL_SESSIONS = frozenset({
            "morning", "europe_midday", "us_pre_open",
            "us_intraday_risk", "into_close", "closing_wrap",
        })
        key_map: dict[str, str] = {
            "morning": "morning",
            "morning_brief": "morning",
            "intraday": "intraday",
            "breaking": "breaking",
        }
        mt = (message_type or "").strip().lower()
        if mt in _CANONICAL_SESSIONS and mt != "morning":
            # Use session-specific config if present, otherwise fall through to "morning"
            normalized = mt
        else:
            normalized = key_map.get(mt, "")

        if not normalized:
            return []

        channels = self.delivery_channels.get(normalized)
        if not channels:
            raw = self.delivery.get(f"{normalized}_channels")
            if isinstance(raw, list):
                channels = [str(item).strip().lower() for item in raw if str(item).strip()]

        # For non-morning canonical sessions without explicit config, fall back to morning config.
        if not channels and normalized in _CANONICAL_SESSIONS and normalized != "morning":
            channels = self.delivery_channels.get("morning")
            if not channels:
                raw = self.delivery.get("morning_channels")
                if isinstance(raw, list):
                    channels = [str(item).strip().lower() for item in raw if str(item).strip()]

        if not channels:
            return []
        allowed = {"telegram", "email"}
        return [channel for channel in channels if channel in allowed]

    def morning_section_enabled(self, section_key: str) -> bool:
        """Check whether a morning section is enabled by preference."""
        return self.morning_section_flags.get(section_key, True)

    @property
    def healthcare_preferences(self) -> dict[str, Any]:
        return dict(self.healthcare or {})

    @property
    def healthcare_enabled(self) -> bool:
        return bool(self.healthcare.get("enabled", False))


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
        healthcare=_load_healthcare_preferences(configs_dir),
        country=user_data.get("country", ""),
        market_region=user_data.get("market_region", ""),
        sub_region=user_data.get("sub_region", ""),
        session_template=user_data.get("session_template", ""),
        session_intensity=user_data.get("session_intensity", "standard"),
        primary_markets=user_data.get("primary_markets", []),
        secondary_markets=user_data.get("secondary_markets", []),
    )

    inferred = infer_market_profile(profile.country, profile.timezone)
    if not profile.timezone:
        profile.timezone = inferred.timezone
    if not profile.market_region:
        profile.market_region = inferred.market_region
    if not profile.sub_region:
        profile.sub_region = inferred.sub_region
    if not profile.session_template:
        profile.session_template = inferred.session_template
    if not profile.session_intensity:
        profile.session_intensity = inferred.session_intensity

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

    _load_profile_overrides(profile)
    _load_portfolio_context(profile, configs_dir)

    logger.info(
        "Loaded profile '%s' (tz=%s, %d watchlist tickers, %d sectors, %d holdings)",
        profile.name,
        profile.timezone,
        len(profile.all_watchlist_tickers),
        len(profile.sector_weights),
        len(profile.portfolio_holdings),
    )
    return profile


def _load_healthcare_preferences(configs_dir: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "enabled": False,
        "max_items_morning": 4,
        "max_items_intraday": 3,
        "breaking_alerts": False,
        "themes": [
            "peptides",
            "GLP-1",
            "obesity",
            "metabolic disease",
            "diabetes",
            "oncology",
            "rare disease",
            "immunology",
            "API manufacturing",
            "CDMO",
            "fill-finish",
            "sterile manufacturing",
            "clinical trials",
            "FDA",
            "EMA",
        ],
        "tickers": [
            "LLY",
            "NVO",
            "AZN",
            "RHHBY",
            "REGN",
            "AMGN",
            "VRTX",
            "MRNA",
            "BMRN",
            "TMO",
            "DHR",
            "WST",
            "LONN.SW",
        ],
        "assets": [
            "tirzepatide",
            "semaglutide",
            "retatrutide",
            "orforglipron",
            "wegovy",
            "ozempic",
            "mounjaro",
            "zepbound",
        ],
        "minimum_severity_morning": "medium",
        "minimum_severity_intraday": "high",
        "minimum_severity_breaking": "critical",
    }
    cfg_path = configs_dir / "healthcare.yaml"
    if not cfg_path.exists():
        cfg_path = configs_dir / "healthcare.example.yaml"
    if not cfg_path.exists():
        return defaults
    try:
        with open(cfg_path) as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        logger.warning("Failed to load healthcare config from %s", cfg_path, exc_info=True)
        return defaults
    section = data.get("healthcare", {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        return defaults
    merged = dict(defaults)
    merged.update(section)
    merged["themes"] = [str(item).strip() for item in (merged.get("themes") or []) if str(item).strip()]
    merged["tickers"] = [str(item).strip().upper() for item in (merged.get("tickers") or []) if str(item).strip()]
    merged["assets"] = [str(item).strip() for item in (merged.get("assets") or []) if str(item).strip()]
    return merged


def _load_profile_overrides(profile: UserProfile) -> None:
    """Load persisted preference overrides from the Phase 4.2 control plane."""
    try:
        from app.personalization.preferences_service import get_preferences
    except Exception:
        logger.debug("Preference service unavailable", exc_info=True)
        return

    try:
        overrides = get_preferences(profile.name)
    except Exception:
        logger.debug("No persisted preference overrides available yet", exc_info=True)
        return

    if not overrides:
        return

    profile.preference_overrides = overrides
    for key, value in overrides.items():
        if key == "watchlist.primary":
            profile.watchlist_primary = [str(item).upper() for item in (value or [])]
            continue
        if key == "watchlist.secondary":
            profile.watchlist_secondary = [str(item).upper() for item in (value or [])]
            continue
        if key == "watchlist.monitor":
            profile.watchlist_monitor = [str(item).upper() for item in (value or [])]
            continue
        if key == "sector.weights" and isinstance(value, dict):
            profile.sector_weights = {
                str(sector).lower(): float(weight)
                for sector, weight in value.items()
            }
            continue
        if key == "coverage.home_region":
            profile.home_region = str(value).strip().lower() or profile.home_region
            continue
        if key == "coverage.weights" and isinstance(value, dict):
            profile.coverage_weights = {
                str(region).lower(): float(weight)
                for region, weight in value.items()
            }
            continue
        if key == "delivery.morning_channels":
            profile.delivery_channels["morning"] = [str(item).lower() for item in (value or [])]
            continue
        if key == "delivery.intraday_channels":
            profile.delivery_channels["intraday"] = [str(item).lower() for item in (value or [])]
            continue
        if key == "delivery.breaking_channels":
            profile.delivery_channels["breaking"] = [str(item).lower() for item in (value or [])]
            continue
        if key in {
            "delivery.morning_brief_time",
            "delivery.hourly_updates",
            "delivery.breaking_alerts",
            "delivery.intraday_global_risk_enabled",
            "delivery.llm_email_morning",
            "delivery.llm_shadow_mode",
            "delivery.quiet_hours_start",
            "delivery.quiet_hours_end",
            "delivery.email_density_mode",
            "delivery.session_mode",
            "delivery.always_send_sessions",
            "delivery.suppress_low_materiality",
        }:
            profile.delivery[key.replace("delivery.", "")] = value
            continue
        if key == "sections.global_news":
            profile.morning_section_flags["global_news"] = bool(value)
            continue
        if key.startswith("sections.morning."):
            section = key.replace("sections.morning.", "", 1)
            profile.morning_section_flags[section] = bool(value)
            continue
        if key.startswith("healthcare."):
            pref_key = key.replace("healthcare.", "", 1)
            profile.healthcare[pref_key] = value


def _load_portfolio_context(profile: UserProfile, configs_dir: Path) -> None:
    """Load persisted holdings and derive simple portfolio exposure context."""
    try:
        from app.portfolio.importer import load_holdings_file
        from app.portfolio.service import load_active_holdings, replace_holdings_snapshot
    except Exception:
        logger.debug("Portfolio modules unavailable", exc_info=True)
        return

    try:
        holdings = load_active_holdings(profile.name)
    except Exception:
        logger.debug("No persisted holdings available yet", exc_info=True)
        holdings = []
    if not holdings:
        bootstrap_path = configs_dir / "holdings.yaml"
        if bootstrap_path.exists():
            try:
                snapshot = load_holdings_file(
                    bootstrap_path,
                    default_profile=profile.name,
                )
                if snapshot.holdings:
                    replace_holdings_snapshot(
                        profile_name=snapshot.profile_name or profile.name,
                        holdings=snapshot.holdings,
                        as_of_date=snapshot.as_of_date,
                    )
                    holdings = load_active_holdings(snapshot.profile_name or profile.name)
                    logger.info(
                        "Bootstrapped %d holdings from %s",
                        len(holdings),
                        bootstrap_path.name,
                    )
            except Exception:
                logger.warning("Failed to bootstrap holdings from %s", bootstrap_path, exc_info=True)

    profile.portfolio_holdings = holdings
    profile.portfolio_sector_weights = _derive_portfolio_sector_weights(holdings, configs_dir)


def _derive_portfolio_sector_weights(
    holdings: list[PortfolioHolding],
    configs_dir: Path,
) -> dict[str, float]:
    """Infer portfolio sector concentration from holdings + sectors config."""
    if not holdings:
        return {}

    sectors_path = configs_dir / "sectors.yaml"
    if not sectors_path.exists():
        return {}

    with open(sectors_path) as handle:
        cfg = yaml.safe_load(handle) or {}

    ticker_to_sector: dict[str, str] = {}
    for sector_key, data in (cfg.get("sectors") or {}).items():
        for ticker in data.get("key_names", []):
            ticker_to_sector[ticker.upper()] = sector_key

    weight_values = [position.weight_pct for position in holdings if position.weight_pct is not None]
    use_equal_weights = not weight_values

    sector_totals: dict[str, float] = {}
    for position in holdings:
        sector = position.sector_override or ticker_to_sector.get(position.symbol)
        if not sector:
            continue
        if use_equal_weights:
            weight = 1.0
        else:
            weight = position.weight_pct or 0.0
        sector_totals[sector] = sector_totals.get(sector, 0.0) + weight

    total = sum(sector_totals.values())
    if total <= 0:
        return {}
    return {
        sector: weight / total
        for sector, weight in sector_totals.items()
    }
