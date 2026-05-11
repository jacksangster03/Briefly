from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.db.models import SessionSendState
from app.db.session import get_session
from app.settings import Settings
from app.web.app import create_web_app


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "briefings_delivery.db"
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
                "  breaking_channels: [telegram, email]",
                "  intraday_channels: [telegram, email]",
                "  morning_channels: [telegram, email]",
                "  quiet_hours_start: '23:00'",
                "  quiet_hours_end: '07:00'",
                "  weekend_mode: saturday_only",
                "  failure_alerts_enabled: true",
                "  failure_alert_channels: [email]",
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


def test_briefings_delivery_route_renders(client):
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "Briefings &amp; Delivery" in response.text
    assert "Today’s Session Timeline" in response.text


def test_briefings_delivery_shows_status_and_channels(client):
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "Scheduler:" in response.text
    assert "Current session:" in response.text
    assert "Next session:" in response.text
    assert "Delivery Channel Matrix" in response.text
    assert "Breaking Alerts" in response.text
    assert "Delivery Failure Alerts" in response.text
    assert "Telegram error alerts" in response.text


def test_briefings_delivery_shows_session_preview_commands(client):
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "python -m app.cli session-preview --session morning --show-output" in response.text
    assert "python -m app.cli session-preview --session us_pre_open --show-output" in response.text


def test_briefings_delivery_empty_records_state(client):
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "No delivery records for this date." in response.text


def test_briefings_delivery_with_records_and_emea_sessions(client):
    now = datetime.now(timezone.utc)
    today_local = now.astimezone(ZoneInfo("Europe/Madrid")).date()
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                local_date=today_local,
                session_key="morning",
                channel="telegram",
                message_type="session_brief:morning",
                idempotency_key="test-briefings-delivery-ui",
                success=True,
                sent_at=now,
                in_progress=False,
                replay_namespace="",
            )
        )
        db.commit()
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "Morning Briefing" in response.text
    assert "Europe Midday Check" in response.text
    assert "US Pre-Open Setup" in response.text


def test_briefings_delivery_supports_alternate_template_sessions(client, monkeypatch):
    from app.briefing.session_metadata import SessionMeta
    from datetime import time as _time

    monkeypatch.setattr(
        "app.briefing.session_metadata.sessions_for_profile",
        lambda _profile: (
            SessionMeta("apac_open", "APAC Open Brief", "APAC setup", _time(1, 0), _time(3, 0)),
            SessionMeta("asia_midday", "Asia Midday Check", "Asia midpoint", _time(5, 0), _time(6, 0)),
        ),
    )
    response = client.get("/ui/briefing?profile=default_user")
    assert response.status_code == 200
    assert "APAC Open Brief" in response.text
    assert "Asia Midday Check" in response.text


def test_routes_still_available(client):
    assert client.get("/ui?profile=default_user").status_code == 200
    assert client.get("/ui/settings?profile=default_user").status_code == 200
    assert client.get("/ui/briefing/macro?profile=default_user").status_code == 200
    assert client.get("/ui/briefing/verticals?profile=default_user").status_code == 200
