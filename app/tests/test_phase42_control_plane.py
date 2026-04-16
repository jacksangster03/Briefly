"""Phase 4.2 tests: control-plane preferences and profile overrides."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.db.models import UserPreference as UserPreferenceRow
from app.db.session import get_session
from app.main import _apply_morning_section_preferences, _get_messengers
from app.personalization.preferences_service import get_preferences, set_preference
from app.personalization.user_profile import UserProfile, load_user_profile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent, QuoteData, SectorSnapshot
from app.settings import Settings


@pytest.fixture
def isolated_db(tmp_path):
    """Swap DB session globals to an isolated SQLite file for this test."""
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "phase42_test.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()


def test_preference_upsert_and_load(isolated_db):
    set_preference("default_user", "watchlist.primary", ["nvda", "msft"])
    set_preference("default_user", "watchlist.primary", ["tsla"])
    prefs = get_preferences("default_user")
    assert prefs["watchlist.primary"] == ["TSLA"]

    with get_session() as session:
        rows = (
            session.query(UserPreferenceRow)
            .filter(
                UserPreferenceRow.profile_name == "default_user",
                UserPreferenceRow.pref_key == "watchlist.primary",
                UserPreferenceRow.active.is_(True),
            )
            .all()
        )
    assert len(rows) == 1


def test_preference_rejects_invalid_channel(isolated_db):
    with pytest.raises(ValueError, match="Unsupported channels"):
        set_preference("default_user", "delivery.morning_channels", ["telegram", "sms"])


def test_preference_accepts_region_focus_overrides(isolated_db):
    set_preference("default_user", "coverage.home_region", "us")
    set_preference("default_user", "coverage.weights", {"us": 1.2, "europe": 0.8, "asia": 0.5})
    prefs = get_preferences("default_user")
    assert prefs["coverage.home_region"] == "us"
    assert prefs["coverage.weights"] == {"us": 1.2, "europe": 0.8, "asia": 0.5}


def test_preference_accepts_llm_delivery_toggles(isolated_db):
    set_preference("default_user", "delivery.llm_email_morning", False)
    set_preference("default_user", "delivery.llm_shadow_mode", True)
    set_preference("default_user", "delivery.intraday_global_risk_enabled", False)
    prefs = get_preferences("default_user")
    assert prefs["delivery.llm_email_morning"] is False
    assert prefs["delivery.llm_shadow_mode"] is True
    assert prefs["delivery.intraday_global_risk_enabled"] is False


def test_load_user_profile_applies_preference_overrides(isolated_db, tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "user_profile.example.yaml").write_text(
        "\n".join(
            [
                "user:",
                "  name: default_user",
                "  timezone: Europe/Madrid",
                "coverage_weights:",
                "  us: 1.0",
                "sector_weights:",
                "  technology: 1.0",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text(
        "primary: [AAPL]\nsecondary: [MSFT]\nmonitor: [AMD]\n"
    )
    (config_dir / "sectors.yaml").write_text("sectors: {}\nindices: []\nmacro_instruments: []\n")

    set_preference("default_user", "watchlist.primary", ["tsla", "nvda"])
    set_preference("default_user", "delivery.morning_channels", ["email"])
    set_preference("default_user", "sections.morning.watchlist", False)
    set_preference("default_user", "sections.global_news", False)

    settings = Settings(configs_dir=str(config_dir))
    profile = load_user_profile(settings)

    assert profile.watchlist_primary == ["TSLA", "NVDA"]
    assert profile.channels_for("morning") == ["email"]
    assert profile.morning_section_enabled("watchlist") is False
    assert profile.morning_section_enabled("global_news") is False


def test_get_messengers_respects_profile_channel_preferences():
    settings = Settings(
        dry_run=False,
        telegram_bot_token="token",
        telegram_chat_id="123",
        email_user="from@example.com",
        email_password="secret",
        email_to="to@example.com",
    )
    profile = UserProfile(
        delivery_channels={
            "morning": ["email"],
            "intraday": ["telegram"],
        }
    )
    morning = [messenger.name for messenger in _get_messengers(settings, profile, message_type="morning")]
    intraday = [messenger.name for messenger in _get_messengers(settings, profile, message_type="intraday")]
    assert morning == ["email"]
    assert intraday == ["telegram"]


def test_get_messengers_breaking_defaults_to_telegram_only():
    settings = Settings(
        dry_run=False,
        telegram_bot_token="token",
        telegram_chat_id="123",
        email_user="from@example.com",
        email_password="secret",
        email_to="to@example.com",
    )
    profile = UserProfile()
    breaking = [messenger.name for messenger in _get_messengers(settings, profile, message_type="breaking")]
    assert breaking == ["telegram"]


def test_apply_morning_section_preferences_hides_disabled_sections():
    briefing = MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[QuoteData(symbol="SPY", current_price=1.0)],
            macro_quotes=[QuoteData(symbol="GLD", current_price=2.0)],
        ),
        macro_context=[],
        global_news=[NormalisedEvent(title="Global event", event_type="geopolitical")],
        top_themes=[NormalisedEvent(title="Top theme", tickers=["NVDA"])],
        portfolio_focus=[NormalisedEvent(title="Portfolio focus", tickers=["NVDA"])],
        sector_scan=[
            SectorSnapshot(
                sector_key="technology",
                display_name="Technology",
                etf_symbol="XLK",
                top_events=[NormalisedEvent(title="Sector event", tickers=["NVDA"])],
            )
        ],
        watchlist_events=[NormalisedEvent(title="Watchlist event", tickers=["AAPL"])],
        watchlist_quotes=[QuoteData(symbol="AAPL", current_price=3.0)],
    )
    profile = UserProfile(
        morning_section_flags={
            "market_setup": False,
            "global_news": False,
            "top_themes": False,
            "watchlist": False,
        }
    )
    _apply_morning_section_preferences(briefing, profile)

    assert briefing.market_setup.index_quotes == []
    assert briefing.market_setup.macro_quotes == []
    assert briefing.global_news == []
    assert briefing.top_themes == []
    assert briefing.watchlist_events == []
    assert briefing.watchlist_quotes == []
