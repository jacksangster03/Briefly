from __future__ import annotations

from fastapi.testclient import TestClient

from app.web.app import create_web_app


def test_macro_dashboard_ui_route_renders(validation_test_settings, monkeypatch):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    monkeypatch.setattr(
        "app.web.app.build_macro_policy_dashboard",
        lambda **_kwargs: {
            "generated_at": "2026-05-10T08:00:00+00:00",
            "status": "partial",
            "central_bank_policy": {"status": "partial", "series": {}},
            "inflation_tracker": {
                "status": "partial",
                "series": {
                    "us_cpi": {
                        "label": "US CPI YoY",
                        "value": 3.4,
                        "unit": "%",
                        "latest_observation_date": "2026-05-01",
                        "freshness": "fresh",
                    }
                },
            },
            "labour_tracker": {"status": "partial", "series": {}},
            "rates_yield_curve_panel": {
                "status": "partial",
                "series": {},
                "curve_shape": "mixed curve",
                "rate_impulse": "neutral impulse",
                "portfolio_interpretation": "balanced",
            },
            "macro_catalyst_calendar": {"status": "partial", "events": []},
            "portfolio_lens": {"status": "partial", "summary": "generic", "buckets": {}},
            "policy_signals": {
                "status": "partial",
                "fed_bias": {"label": "hold", "confidence": "medium", "drivers": ["mixed inputs"], "missing": [], "risks": []},
                "ecb_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["ecb_policy_rate"], "risks": []},
                "inflation_pressure": {"label": "sticky", "confidence": "medium", "drivers": [], "missing": []},
                "labour_pressure": {"label": "balanced", "confidence": "medium", "drivers": [], "missing": []},
                "rates_pressure": {"label": "neutral", "confidence": "medium", "drivers": [], "missing": []},
                "portfolio_implications": ["Mixed macro posture."],
                "methodology_note": "Deterministic signal, not a forecast.",
            },
            "data_basis": {"macro_sources": "stub", "timezone": "Europe/Madrid", "freshness_note": "stub"},
        },
    )
    response = client.get("/ui/briefing/macro?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Macro Policy Dashboard" in html
    assert "Central Bank Policy" in html
    assert "Inflation Tracker" in html
    assert "Labour Market" in html
    assert "Rates & Yield Curve" in html
    assert "Next Macro Catalysts" in html
    assert "Portfolio Lens" in html
    assert "Data Basis" in html
    assert "Policy Signal" in html
    assert "not a forecast" in html
    assert "US CPI YoY" in html
    assert "%" in html


def test_macro_dashboard_api_route_schema(validation_test_settings, monkeypatch):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    monkeypatch.setattr(
        "app.web.app.build_macro_policy_dashboard",
        lambda **_kwargs: {
            "generated_at": "2026-05-10T08:00:00+00:00",
            "status": "partial",
            "central_bank_policy": {"status": "partial", "series": {}},
            "inflation_tracker": {"status": "partial", "series": {}},
            "labour_tracker": {"status": "partial", "series": {}},
            "rates_yield_curve_panel": {"status": "partial", "series": {}, "curve_shape": "mixed curve", "rate_impulse": "neutral", "portfolio_interpretation": "balanced"},
            "macro_catalyst_calendar": {"status": "partial", "events": []},
            "portfolio_lens": {"status": "partial", "summary": "generic", "buckets": {}},
            "policy_signals": {"status": "partial"},
            "data_basis": {"macro_sources": "stub", "timezone": "Europe/Madrid", "freshness_note": "stub"},
        },
    )
    response = client.get("/api/v1/profile/default_user/briefing/macro")
    assert response.status_code == 200
    payload = response.json()
    assert "central_bank_policy" in payload
    assert "inflation_tracker" in payload
    assert "labour_tracker" in payload
    assert "rates_yield_curve_panel" in payload
    assert "macro_catalyst_calendar" in payload
    assert "portfolio_lens" in payload
    assert "policy_signals" in payload
    assert "data_basis" in payload


def test_macro_dashboard_ui_handles_missing_fields_without_500(validation_test_settings, monkeypatch):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", lambda **_kwargs: {"status": "partial"})
    response = client.get("/ui/briefing/macro?profile=default_user")
    assert response.status_code == 200
    assert "Macro Policy Dashboard" in response.text


def test_macro_dashboard_api_handles_service_exception(validation_test_settings, monkeypatch):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    def _boom(**_kwargs):
        raise RuntimeError("macro boom")

    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", _boom)
    response = client.get("/api/v1/profile/default_user/briefing/macro")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert "central_bank_policy" in payload
