from __future__ import annotations

from unittest.mock import patch

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
    assert "Simple view" in html
    assert "mode=simple" in html
    assert "mode=expert" in html
    assert "Macro Signal Summary" in html
    assert "Inflation Tracker" in html
    assert "Labour Market" in html
    assert "Rates & Yield Curve" in html
    assert "Macro Catalyst Calendar" in html
    assert "Portfolio Macro Lens" in html
    assert "Data Quality & Freshness" in html
    assert "not a forecast" in html
    assert "Regional Policy Coverage" in html
    assert "United Kingdom" in html
    assert "Eurozone" in html
    assert "United States" in html
    assert "Spain" in html
    assert "Japan" in html
    assert "China" in html
    assert "ECB-linked country lens" in html
    assert "/ui?profile=default_user" in html
    assert "/ui/briefing?profile=default_user" in html


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


def test_macro_dashboard_expert_mode_renders_advanced_details(validation_test_settings, monkeypatch):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    monkeypatch.setattr(
        "app.web.app.build_macro_policy_dashboard",
        lambda **_kwargs: {
            "status": "partial",
            "policy_signals": {
                "status": "partial",
                "fed_bias": {"label": "hold", "confidence": "medium", "drivers": ["mixed inputs"], "missing": []},
                "ecb_bias": {"label": "uncertain", "confidence": "low", "drivers": ["labour confirmation unavailable"], "missing": ["euro_area_unemployment_rate"]},
            },
            "central_bank_policy": {"status": "partial", "series": {}},
            "inflation_tracker": {"status": "partial", "series": {}},
            "labour_tracker": {"status": "partial", "series": {}},
            "rates_yield_curve_panel": {"status": "partial", "series": {}},
            "macro_catalyst_calendar": {"status": "partial", "events": []},
            "portfolio_lens": {"status": "partial", "summary": "generic", "buckets": {}},
            "data_basis": {"macro_sources": "stub", "freshness_note": "stub"},
        },
    )
    response = client.get("/ui/briefing/macro?profile=default_user&mode=expert")
    assert response.status_code == 200
    assert "Expert view" in response.text
    assert "Expert Details" in response.text
    assert "Fed bias drivers" in response.text
    assert "ECB bias drivers" in response.text


def test_macro_dashboard_and_core_routes_still_render(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    assert client.get("/ui?profile=default_user").status_code == 200
    assert client.get("/ui/briefing?profile=default_user").status_code == 200
    assert client.get("/ui/briefing/macro?profile=default_user").status_code == 200


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


# ---------------------------------------------------------------------------
# FX Pulse API: simple mode returns expected keys
# ---------------------------------------------------------------------------

def _make_mock_panel():
    """Build a minimal FX panel stub for testing without live provider calls."""
    from datetime import datetime, timezone
    from app.fx.basket import FXInstrument
    from app.fx.panel import FXQuote

    def _q(label, symbol, base, quote, value, change):
        instr = FXInstrument(
            label=label, symbol=symbol, source="yfinance",
            base=base, quote=quote, is_dxy_proxy=False, optional=False, condition=None,
        )
        return FXQuote(
            instrument=instr, value=value, daily_change_pct=change,
            change_5d_pct=change * 2, source="yfinance",
            freshness="prior_close", status="ok",
            fetched_at=datetime.now(timezone.utc),
        )

    return [
        _q("EUR/USD", "EURUSD=X", "EUR", "USD", 1.105, 0.6),
        _q("Trade-weighted USD", "DTWEXBGS", "USD", "BASKET", 115.2, -0.3),
        _q("EUR/GBP", "EURGBP=X", "EUR", "GBP", 0.845, 0.2),
        _q("USD/JPY", "USDJPY=X", "USD", "JPY", 156.5, 0.7),
        _q("USD/CNH", "USDCNH=X", "USD", "CNH", 7.32, 0.6),
    ]


def test_fx_pulse_api_simple_mode_returns_required_keys(validation_test_settings):
    """Simple mode must return all documented keys without live provider calls."""
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    mock_panel = _make_mock_panel()

    with patch("app.fx.panel.fetch_fx_panel", return_value=mock_panel):
        response = client.get("/api/fx-pulse?profile=default_user&mode=simple")

    assert response.status_code == 200
    data = response.json()
    for key in ("usd_pressure", "eur_usd", "usd_jpy", "fx_materiality", "materiality_score", "profile_basket_label", "drivers", "missing"):
        assert key in data, f"Missing key in simple mode response: {key}"
    assert "quotes" not in data, "Simple mode must not include quotes array"


def test_fx_pulse_api_expert_mode_returns_quotes_array(validation_test_settings):
    """Expert mode must return a quotes array with source, freshness, status fields."""
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    mock_panel = _make_mock_panel()

    with patch("app.fx.panel.fetch_fx_panel", return_value=mock_panel):
        response = client.get("/api/fx-pulse?profile=default_user&mode=expert")

    assert response.status_code == 200
    data = response.json()
    assert "quotes" in data, "Expert mode must include quotes array"
    assert len(data["quotes"]) > 0
    first = data["quotes"][0]
    for field in ("label", "source", "freshness", "status"):
        assert field in first, f"Quote missing field: {field}"


def test_fx_pulse_api_degrades_gracefully_on_provider_failure(validation_test_settings):
    """The endpoint must return a valid JSON response even when the provider fails."""
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    def _fail(basket, settings_dict):
        raise RuntimeError("Provider unavailable")

    with patch("app.fx.panel.fetch_fx_panel", side_effect=_fail):
        response = client.get("/api/fx-pulse?profile=default_user")

    assert response.status_code == 200
    data = response.json()
    assert data.get("usd_pressure") == "unavailable"
    assert data.get("fx_materiality") == "low"


def test_macro_ui_includes_fx_pulse_card(validation_test_settings, monkeypatch):
    """The /ui/briefing/macro route must render the FX & Dollar Pulse card."""
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", lambda **_kw: {"status": "partial"})
    mock_panel = _make_mock_panel()

    with patch("app.fx.panel.fetch_fx_panel", return_value=mock_panel):
        response = client.get("/ui/briefing/macro?profile=default_user")

    assert response.status_code == 200
    html = response.text
    assert "FX" in html and "Dollar Pulse" in html or "FX &amp; Dollar Pulse" in html or "FX & Dollar Pulse" in html


def test_macro_ui_expert_mode_includes_fx_quotes_table(validation_test_settings, monkeypatch):
    """Expert mode of /ui/briefing/macro must render the FX instrument quotes table."""
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    monkeypatch.setattr("app.web.app.build_macro_policy_dashboard", lambda **_kw: {"status": "partial"})
    mock_panel = _make_mock_panel()

    with patch("app.fx.panel.fetch_fx_panel", return_value=mock_panel):
        response = client.get("/ui/briefing/macro?profile=default_user&mode=expert")

    assert response.status_code == 200
    html = response.text
    assert "Full instrument quotes" in html
