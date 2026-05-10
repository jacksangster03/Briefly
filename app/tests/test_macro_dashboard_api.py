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
                        "transformation": "fred_units_pc1",
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
                "ecb_bias": {"label": "uncertain", "confidence": "low", "drivers": ["Eurozone data incomplete"], "missing": ["eurozone_hicp"], "risks": []},
                "inflation_pressure": {"label": "sticky", "confidence": "medium", "drivers": [], "missing": []},
                "labour_pressure": {"label": "balanced", "confidence": "medium", "drivers": [], "missing": []},
                "rates_pressure": {"label": "neutral", "confidence": "medium", "drivers": [], "missing": []},
                "portfolio_implications": ["Mixed macro posture."],
                "regions": {
                    "us": {"region_label": "United States", "status": "ok", "policy_bias": {"label": "hold", "confidence": "medium", "missing": []}},
                    "eurozone": {"region_label": "Eurozone", "status": "partial", "policy_bias": {"label": "uncertain", "confidence": "low", "missing": ["eurozone_hicp"]}},
                    "uk": {"region_label": "United Kingdom", "status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "missing": ["not_wired"]}},
                    "japan": {"region_label": "Japan", "status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "missing": ["not_wired"]}},
                    "china": {"region_label": "China", "status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "missing": ["not_wired"]}},
                    "spain": {"region_label": "Spain", "scope": "country_lens", "status": "partial", "policy_bias": {"label": "uncertain", "confidence": "low", "missing": ["country_specific_policy_inputs"]}},
                },
                "global_summary": {"status": "partial", "fed_bias": "hold", "ecb_bias": "uncertain", "note": "regional"},
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
    assert "Eurozone data incomplete" in html
    assert "Regional Coverage" in html
    assert "United Kingdom" in html
    assert "Spain" in html
    assert "Japan" in html
    assert "China" in html
    assert "US CPI YoY" in html
    assert "fred_units_pc1" in html
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
            "policy_signals": {
                "status": "partial",
                "fed_bias": {"label": "hold", "confidence": "medium", "drivers": [], "missing": [], "risks": []},
                "ecb_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["eurozone_hicp"], "risks": []},
                "regions": {"us": {"status": "ok", "policy_bias": {"label": "hold"}}, "eurozone": {"status": "partial", "policy_bias": {"label": "uncertain"}}},
                "global_summary": {"status": "partial", "fed_bias": "hold", "ecb_bias": "uncertain"},
            },
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
    assert "fed_bias" in payload["policy_signals"]
    assert "ecb_bias" in payload["policy_signals"]
    assert "regions" in payload["policy_signals"]
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


def test_api_payload_raw_index_fallback_not_labelled_yoy(validation_test_settings, monkeypatch):
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
                    "uk_cpi": {
                        "label": "UK CPI index level",
                        "value": 136.1,
                        "unit": "index",
                        "value_kind": "index_level",
                        "transformation": "raw_index_fallback",
                    }
                },
            },
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
    uk = response.json()["inflation_tracker"]["series"]["uk_cpi"]
    assert uk["label"] == "UK CPI index level"
    assert uk["value_kind"] == "index_level"
    assert uk["transformation"] == "raw_index_fallback"
