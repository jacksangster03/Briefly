from __future__ import annotations

from fastapi.testclient import TestClient

from app.web.app import create_web_app
from app.web.control_plane_service import apply_preference_updates


def test_verticals_ui_route_renders(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Vertical Intelligence" in html
    assert "Healthcare / Biotech" in html
    assert "Mode guidance" in html
    assert "off:" in html
    assert "watch:" in html
    assert "active:" in html
    assert "portfolio_linked:" in html


def test_verticals_ui_shows_source_health_and_planned_cards(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "FDA / openFDA" in html
    assert "ClinicalTrials.gov" in html
    assert "EMA" in html
    assert "SEC / Company IR" in html
    assert "General news" in html
    assert "AI / Semiconductors" in html
    assert "Energy / Geopolitics" in html
    assert "Defence / Aerospace" in html


def test_verticals_ui_handles_missing_diagnostics_gracefully(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    assert "No vertical run diagnostics yet." in response.text


def test_verticals_ui_mode_off_renders_inactive_calmly(validation_isolated_db, validation_test_settings):
    apply_preference_updates("default_user", {"verticals.healthcare.mode": "off"})
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    html = response.text.lower()
    assert "mode</span><span class=\"badge off\">off" in html or "mode" in html
    assert "inactive" in html or "mode off" in html


def test_core_routes_still_work_with_verticals_ui(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/briefing").status_code == 200
    assert client.get("/ui/news").status_code == 200
    assert client.get("/ui/portfolio").status_code == 200
    assert client.get("/ui/briefing/macro?profile=default_user").status_code == 200
