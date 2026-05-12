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
    db_path = tmp_path / "diagnostics_ui.db"
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
                "  breaking_alerts: true",
                "  breaking_channels: [telegram, email]",
                "  morning_channels: [telegram, email]",
                "  intraday_channels: [telegram, email]",
                "  weekend_mode: saturday_only",
                "  failure_alerts_enabled: true",
                "  failure_alert_channels: [email]",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text("primary: [AAPL]\n")
    (config_dir / "sectors.yaml").write_text("sectors: {}\nindices: []\nmacro_instruments: []\n")
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


def test_diagnostics_route_renders(client):
    response = client.get("/ui/diagnostics?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Diagnostics" in html
    assert "Scheduler, delivery, provider health and audit tools." in html


def test_diagnostics_overview_shows_core_status(client):
    response = client.get("/ui/diagnostics?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Scheduler status" in html
    assert "Current session" in html
    assert "Next session" in html
    assert "Delivery failure alerts" in html
    assert "Telegram failure alerts" in html


def test_diagnostics_distinguishes_failure_alerts_from_breaking(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=delivery")
    assert response.status_code == 200
    html = response.text
    assert "Breaking alerts" in html
    assert "Delivery failure alerts" in html
    assert "separate from market breaking alerts" in html


def test_diagnostics_providers_tab_renders_or_empty_state(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=providers")
    assert response.status_code == 200
    html = response.text
    assert "Provider Health" in html
    assert ("No provider health recorded yet." in html) or ("Status" in html)


def test_diagnostics_scheduler_tab_shows_commands(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=scheduler")
    assert response.status_code == 200
    html = response.text
    assert "python -m app.cli schedule-status" in html
    assert "python -m app.cli daily-summary --date today" in html


def test_diagnostics_delivery_tab_shows_commands(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=delivery")
    assert response.status_code == 200
    html = response.text
    assert "python -m app.cli delivery-log --date today" in html
    assert ("No delivery records for this date." in html) or ("Latest delivery records" in html)


def test_diagnostics_news_tab_authority_and_link(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=news")
    assert response.status_code == 200
    html = response.text
    assert "Deterministic classifier remains authoritative" in html
    assert "/ui/news?profile=default_user" in html


def test_diagnostics_verticals_tab_and_link(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=verticals")
    assert response.status_code == 200
    html = response.text
    assert "Verticals" in html
    assert "/ui/briefing/verticals?profile=default_user" in html


def test_diagnostics_macro_tab_no_live_provider_call_assumption(client, monkeypatch):
    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not call")))
    response = client.get("/ui/diagnostics?profile=default_user&tab=macro")
    assert response.status_code == 200
    assert "Macro Sources" in response.text


def test_diagnostics_cli_tab_contains_key_commands(client):
    response = client.get("/ui/diagnostics?profile=default_user&tab=cli")
    assert response.status_code == 200
    html = response.text
    assert "python -m app.cli version" in html
    assert "python -m app.cli session-preview --session morning --show-output" in html
    assert "./scripts/service.sh restart" in html


def test_diagnostics_accepts_tab_query_params(client):
    for tab in ("providers", "delivery", "cli"):
        response = client.get(f"/ui/diagnostics?profile=default_user&tab={tab}")
        assert response.status_code == 200
        assert "Diagnostics" in response.text


def test_core_routes_still_available(client):
    assert client.get("/ui?profile=default_user").status_code == 200
    assert client.get("/ui/briefing?profile=default_user").status_code == 200
    assert client.get("/ui/news?profile=default_user").status_code == 200
    assert client.get("/ui/portfolio?profile=default_user").status_code == 200
    assert client.get("/ui/briefing/macro?profile=default_user").status_code == 200
    assert client.get("/ui/briefing/verticals?profile=default_user").status_code == 200

