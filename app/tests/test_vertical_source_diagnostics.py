from __future__ import annotations

from fastapi.testclient import TestClient

from app.personalization.user_profile import UserProfile
from app.web.app import create_web_app
from app.verticals.engine import verticals_status_for_profile


def _profile() -> UserProfile:
    return UserProfile(name="default_user", timezone="Europe/Madrid", healthcare={"enabled": False})


def test_vertical_status_includes_new_plugins(monkeypatch):
    profile = _profile()
    rows = verticals_status_for_profile(profile=profile)
    keys = {str(r.get("vertical_key")) for r in rows}
    assert {"healthcare", "geopolitics", "ai_tech"}.issubset(keys)


def test_verticals_ui_renders_with_unavailable_sources(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Vertical Intelligence" in html
    assert "Geopolitics" in html
    assert "AI / Tech" in html


def test_verticals_ui_status_render_does_not_require_live_provider_calls(validation_test_settings, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("live provider call should not execute on status render")

    monkeypatch.setattr("app.verticals.sources.geopolitics.collect_geopolitics_events", _boom)
    monkeypatch.setattr("app.verticals.sources.ai_tech.collect_ai_tech_events", _boom)
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
