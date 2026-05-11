from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings
from app.web.app import create_web_app


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "portfolio_ui.db"
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
            ]
        )
    )
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


def test_portfolio_home_overview_renders(client):
    response = client.get("/ui/portfolio?profile=default_user")
    assert response.status_code == 200
    assert "<h1>Portfolio</h1>" in response.text
    assert "Overview" in response.text
    assert "What you own" in response.text
    assert "Next actions" in response.text


def test_portfolio_home_shows_empty_state_without_holdings(client):
    response = client.get("/ui/portfolio?profile=default_user")
    assert response.status_code == 200
    assert "No holdings configured yet." in response.text


def test_portfolio_home_policy_and_risk_fallbacks_render(client):
    response = client.get("/ui/portfolio?profile=default_user")
    assert response.status_code == 200
    assert "Policy fit" in response.text
    assert ("Risk snapshot unavailable." in response.text) or ("Risk snapshot" in response.text)


def test_portfolio_home_compact_holdings_no_raw_diagnostics_dump(client):
    response = client.get("/ui/portfolio?profile=default_user")
    assert response.status_code == 200
    assert "Holdings (compact)" in response.text
    assert "raw_data" not in response.text
    assert "debug_payload" not in response.text


def test_portfolio_intermediate_view_renders(client):
    response = client.get("/ui/portfolio?profile=default_user&view=intermediate")
    assert response.status_code == 200
    assert "Risk & benchmark summary" in response.text
    assert "Attribution summary" in response.text


def test_portfolio_advanced_view_has_advanced_links(client):
    response = client.get("/ui/portfolio?profile=default_user&view=advanced")
    assert response.status_code == 200
    assert "Advanced tools" in response.text
    assert "/ui/portfolio/cma?profile=default_user" in response.text
    assert "/ui/portfolio/simulation?profile=default_user" in response.text
    assert "/ui/portfolio/benchmark?profile=default_user" in response.text
    assert "/ui/portfolio/rebalancing?profile=default_user" in response.text
    assert "/ui/portfolio/attribution?profile=default_user" in response.text
    assert "/ui/portfolio/diagnostics?profile=default_user" in response.text


def test_portfolio_and_core_routes_still_work(client):
    assert client.get("/ui/portfolio?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/risk?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/cma?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/scenarios?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/simulation?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/attribution?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/rebalancing?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/benchmark?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio/diagnostics?profile=default_user").status_code == 200
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/briefing").status_code == 200
    assert client.get("/ui/briefing/macro?profile=default_user").status_code == 200
