from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
import pytest

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings
from app.web.app import create_web_app


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "command_centre.db"
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


@pytest.fixture
def test_settings(tmp_path, isolated_db):
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "user_profile.example.yaml").write_text(
        "\n".join(
            [
                "user:",
                "  name: default_user",
                "  timezone: Europe/Madrid",
                "  home_region: spain",
                "delivery:",
                "  morning_brief_time: '08:45'",
                "  hourly_updates: true",
                "  breaking_alerts: true",
                "  quiet_hours_start: '23:00'",
                "  quiet_hours_end: '07:00'",
                "  weekend_mode: saturday_only",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text("primary: [AAPL]\nsecondary: [MSFT]\nmonitor: [NVDA]\n")
    (config_dir / "sectors.yaml").write_text(
        "\n".join(
            [
                "sectors:",
                "  technology:",
                "    etf: XLK",
                "    display_name: Technology",
                "    key_names: [AAPL, MSFT, NVDA]",
                "indices:",
                "  - { symbol: SPY, display: \"S&P 500\" }",
                "macro_instruments:",
                "  - { symbol: TLT, display: \"20Y+ Treasury\" }",
            ]
        )
    )
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


def test_command_centre_route_renders(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "Briefly Command Centre" in response.text
    assert "Legacy: Briefly Home" not in response.text


def test_command_centre_shows_next_or_weekend_state(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert ("Next Briefing" in response.text) or ("no automatic weekend briefing scheduled" in response.text)


def test_command_centre_shows_failure_alert_status(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "Delivery failure alerts" in response.text
    assert "Failure alerts to Telegram" in response.text
    assert "off" in response.text


def test_command_centre_macro_fallback_when_service_unavailable(client, monkeypatch):
    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "Macro dashboard unavailable." in response.text


def test_command_centre_portfolio_fallback_when_no_holdings(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "Active holdings" in response.text
    assert "Unavailable" in response.text


def test_command_centre_has_expected_links(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "/ui/briefing/macro?profile=default_user" in response.text
    assert "/ui/portfolio?profile=default_user" in response.text
    assert "/ui/briefing/history?profile=default_user" in response.text
    assert "/ui/audit?profile=default_user" in response.text
    assert "/ui/settings?profile=default_user" in response.text


def test_command_centre_does_not_show_raw_default_user_market_labels(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "default_user:" not in response.text


def test_ui_settings_route_still_works(client):
    response = client.get("/ui/settings?profile=default_user")
    assert response.status_code == 200
    assert "Briefly" in response.text
