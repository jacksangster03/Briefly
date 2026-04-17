"""Phase 4.3 tests: FastAPI + HTMX Briefly control center."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings
from app.web.control_plane_service import _display_label
from app.web.app import create_web_app


@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "phase43_test.db"
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
                "coverage_weights:",
                "  us: 1.0",
                "sector_weights:",
                "  technology: 1.0",
                "  semiconductors: 0.8",
                "delivery:",
                "  morning_brief_time: '08:45'",
                "  hourly_updates: true",
                "  breaking_alerts: true",
                "  quiet_hours_start: '23:00'",
                "  quiet_hours_end: '07:00'",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text(
        "primary: [AAPL, MSFT]\nsecondary: [NVDA]\nmonitor: [AMD]\n"
    )
    (config_dir / "sectors.yaml").write_text(
        "\n".join(
            [
                "sectors:",
                "  technology:",
                "    etf: XLK",
                "    display_name: Technology",
                "    key_names: [AAPL, MSFT, NVDA]",
                "  semiconductors:",
                "    etf: SMH",
                "    display_name: Semiconductors",
                "    key_names: [NVDA, AMD, INTC]",
                "indices:",
                "  - { symbol: SPY, display: \"S&P 500\" }",
                "  - { symbol: QQQ, display: \"Nasdaq 100\" }",
                "macro_instruments:",
                "  - { symbol: TLT, display: \"20Y+ Treasury\" }",
                "  - { symbol: GLD, display: Gold }",
            ]
        )
    )
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


def test_ui_settings_page_renders(client):
    response = client.get("/ui/settings?profile=default_user")
    assert response.status_code == 200
    assert "Briefly" in response.text
    assert "Portfolio Analyzer" in response.text
    assert "Saved values are persisted in SQLite as" in response.text
    assert "Executive Summary" in response.text
    assert "Coverage Alignment" in response.text
    assert "What Will Drive Tomorrow" in response.text
    assert "Scenario Stress Tests" in response.text
    assert "Holdings Data Quality" in response.text
    assert "Top Holdings Concentration" in response.text
    assert "Sector Exposure vs Coverage Weight" in response.text
    assert "Analyzer Warnings" in response.text
    assert "Portfolio Exposure" in response.text
    assert "Editorial Coverage Weight" in response.text
    assert "Home Region Focus" in response.text
    assert "Briefing Impact Preview" in response.text
    assert "Region Weight — US" in response.text
    assert "Region Weight — LATAM" in response.text


def test_display_label_formats_acronyms():
    assert _display_label("us") == "US"
    assert _display_label("latam") == "LATAM"
    assert _display_label("global_macro") == "Global Macro"
    assert _display_label("ai") == "AI"
    assert _display_label("ibex") == "IBEX"


def test_api_put_preferences_and_state_roundtrip(client):
    response = client.put(
        "/api/v1/profile/default_user/preferences",
        json={
            "updates": {
                "watchlist.primary": ["TSLA", "AMZN"],
                "delivery.morning_channels": ["email"],
                "sections.morning.watchlist": False,
                "sections.global_news": False,
                "delivery.intraday_global_risk_enabled": False,
            }
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"]["watchlist.primary"] == ["TSLA", "AMZN"]

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["effective"]["watchlist"]["primary"] == ["TSLA", "AMZN"]
    assert state["effective"]["delivery"]["morning_channels"] == ["email"]
    assert state["effective"]["delivery"]["intraday_global_risk_enabled"] is False
    assert state["effective"]["sections"]["global_news"] is False
    assert state["effective"]["sections"]["watchlist"] is False
    assert "metadata" in state
    assert "validations" in state
    assert "analysis" in state
    assert "kpis" in state["analysis"]
    assert "chart_max" in state["analysis"]
    assert "holdings_totals" in state["analysis"]
    assert "top_positions" in state["analysis"]
    assert "concentration" in state["analysis"]
    assert "sector_exposure" in state["analysis"]
    assert "region_emphasis" in state["analysis"]
    assert "bucket_allocation" in state["analysis"]
    assert "warnings" in state["analysis"]
    assert "timing" in state["analysis"]
    assert "executive_summary" in state["analysis"]
    assert "alignment_findings" in state["analysis"]
    assert "briefing_influence" in state["analysis"]
    assert "scenario_stress" in state["analysis"]
    assert "health_checks" in state["analysis"]
    assert "data_quality" in state["analysis"]


def test_api_put_preferences_rejects_invalid_values(client):
    bad_channel = client.put(
        "/api/v1/profile/default_user/preferences",
        json={"updates": {"delivery.morning_channels": ["sms"]}},
    )
    assert bad_channel.status_code == 400

    bad_key = client.put(
        "/api/v1/profile/default_user/preferences",
        json={"updates": {"delivery.unknown_key": "x"}},
    )
    assert bad_key.status_code == 400


def test_api_delete_preference_reverts_to_defaults(client):
    set_resp = client.put(
        "/api/v1/profile/default_user/preferences",
        json={"updates": {"watchlist.primary": ["TSLA"]}},
    )
    assert set_resp.status_code == 200

    delete_resp = client.delete("/api/v1/profile/default_user/preferences/watchlist.primary")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["removed"] is True

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["effective"]["watchlist"]["primary"] == ["AAPL", "MSFT"]


def test_api_holdings_import_yaml_and_reject_bad_csv(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "as_of_date: 2026-04-12",
            "holdings:",
            "  - symbol: NVDA",
            "    weight_pct: 8.5",
            "  - symbol: MSFT",
            "    weight_pct: 6.2",
        ]
    ).encode("utf-8")

    response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert response.status_code == 200
    assert response.json()["imported_count"] == 2

    state = client.get("/api/v1/profile/default_user/state").json()
    assert len(state["holdings"]) == 2
    assert state["analysis"]["holdings_totals"]["total_weight_pct"] == pytest.approx(14.7)
    assert state["analysis"]["holdings_totals"]["gap_to_100_pct"] == pytest.approx(85.3)
    assert state["analysis"]["top_positions"][0]["symbol"] == "NVDA"
    assert state["analysis"]["top_positions"][0]["sector_label"] == "Semiconductors"
    assert state["analysis"]["concentration"]["top1_weight_pct"] == pytest.approx(8.5)
    assert state["analysis"]["concentration"]["top3_weight_pct"] == pytest.approx(14.7)
    assert state["analysis"]["concentration"]["tail_weight_pct"] == pytest.approx(0.0)
    assert state["analysis"]["kpis"]["top_sector_label"] == "Semiconductors"
    assert state["analysis"]["kpis"]["top_region_label"] == "US"
    assert "NVDA is the anchor holding at 8.50%" in state["analysis"]["executive_summary"]
    assert "Tomorrow's brief is set to lead with US context" in state["analysis"]["briefing_impact_preview"]
    assert "will lean on US context" in state["analysis"]["briefing_influence"]["summary"]

    coverage_fit = next(
        item for item in state["analysis"]["health_checks"] if item["key"] == "coverage_fit"
    )
    assert coverage_fit["status"] in {"mixed", "needs_attention", "strong"}

    bad_csv = b"symbol,weight_pct\n,8.2\n"
    bad_response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("bad.csv", io.BytesIO(bad_csv), "text/csv")},
    )
    assert bad_response.status_code == 400


def test_api_followables_search_filters_by_query_and_kind(client):
    response = client.get("/api/v1/followables/search", params={"q": "nv", "kind": "stock"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert any(item["symbol"] == "NVDA" for item in results)
    assert all(item["kind"] == "stock" for item in results)


def test_ui_htmx_save_sections_persists_and_returns_partial(client):
    response = client.post(
        "/ui/profile/default_user/save/sections",
        data={
            "section_market_setup": "on",
            "section_macro_context": "on",
            "section_global_news": "on",
            "section_top_themes": "on",
            "section_portfolio_focus": "on",
            "section_sector_scan": "on",
            # intentionally omit section_watchlist => false
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Morning section visibility saved to DB overrides." in response.text
    assert "Saved at" in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["effective"]["sections"]["watchlist"] is False
    assert state["effective"]["sections"]["global_news"] is True


def test_ui_htmx_save_coverage_persists_watchlist_and_region_focus(client):
    response = client.post(
        "/ui/profile/default_user/save/coverage",
        data={
            "watchlist_primary": ["TSLA", "NVDA"],
            "watchlist_secondary": ["AMZN"],
            "watchlist_monitor": ["AAPL"],
            "coverage_home_region": "us",
            "region_weight__us": "1.4",
            "region_weight__europe": "0.6",
            "sector_weight__technology": "1.1",
            "sector_weight__semiconductors": "0.9",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Coverage preferences saved to DB overrides." in response.text
    assert "Saved at" in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["effective"]["watchlist"]["primary"] == ["TSLA", "NVDA"]
    assert state["effective"]["coverage"]["home_region"] == "us"
    assert state["effective"]["coverage"]["region_weights"]["us"] == pytest.approx(1.4)
    assert state["effective"]["coverage"]["region_weights"]["europe"] == pytest.approx(0.6)


def test_ui_htmx_save_delivery_persists_channel_mix_and_llm_toggles(client):
    response = client.post(
        "/ui/profile/default_user/save/delivery",
        data={
            "delivery_morning_channels": ["telegram", "email"],
            "delivery_intraday_channels": ["telegram", "email"],
            "delivery_breaking_channels": ["telegram"],
            "delivery_morning_brief_time": "08:45",
            "delivery_quiet_hours_start": "23:00",
            "delivery_quiet_hours_end": "07:00",
            "delivery_hourly_updates": "on",
            "delivery_breaking_alerts": "on",
            # leave intraday global risk off intentionally
            "delivery_llm_email_morning": "on",
            # leave shadow off intentionally
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Delivery preferences saved to DB overrides." in response.text
    assert "Saved at" in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["effective"]["delivery"]["morning_channels"] == ["telegram", "email"]
    assert state["effective"]["delivery"]["intraday_channels"] == ["telegram", "email"]
    assert state["effective"]["delivery"]["breaking_channels"] == ["telegram"]
    assert state["effective"]["delivery"]["intraday_global_risk_enabled"] is False
    assert state["effective"]["delivery"]["llm_email_morning"] is True
    assert state["effective"]["delivery"]["llm_shadow_mode"] is False


def test_state_exposes_validation_warnings_when_delivery_channels_missing(client):
    state = client.get("/api/v1/profile/default_user/state").json()
    warnings = state["validations"]
    assert any("No channels enabled for morning briefing delivery." in item["message"] for item in warnings)
    analyzer_warnings = state["analysis"]["warnings"]
    assert any(item["code"] == "delivery_missing_morning" for item in analyzer_warnings)


def test_analyzer_warning_strip_hides_when_issues_are_cleared(client):
    response = client.put(
        "/api/v1/profile/default_user/preferences",
        json={
            "updates": {
                "delivery.morning_channels": ["email"],
                "delivery.intraday_channels": ["email"],
                "delivery.breaking_channels": ["telegram"],
            }
        },
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["analysis"]["warnings"] == []

    page = client.get("/ui/settings?profile=default_user")
    assert page.status_code == 200
    assert "<h3>Analyzer Warnings</h3>" not in page.text


def test_analysis_alignment_finding_flags_undercovered_sector(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "holdings:",
            "  - symbol: NVDA",
            "    weight_pct: 12.0",
            "  - symbol: AMD",
            "    weight_pct: 10.0",
        ]
    ).encode("utf-8")
    import_response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert import_response.status_code == 200

    pref_response = client.put(
        "/api/v1/profile/default_user/preferences",
        json={"updates": {"sector.weights": {"technology": 1.2, "semiconductors": 0.1}}},
    )
    assert pref_response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    findings = state["analysis"]["alignment_findings"]
    assert any(item["code"] == "undercovered_sector" for item in findings)
    assert any("Semiconductors" in item["message"] for item in findings)


def test_analysis_watchlist_support_and_delivery_health_checks(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "holdings:",
            "  - symbol: TSLA",
            "    weight_pct: 12.0",
            "  - symbol: AMZN",
            "    weight_pct: 8.0",
            "  - symbol: NVDA",
            "    weight_pct: 6.0",
        ]
    ).encode("utf-8")
    import_response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert import_response.status_code == 200

    pref_response = client.put(
        "/api/v1/profile/default_user/preferences",
        json={
            "updates": {
                "delivery.morning_channels": ["telegram", "email"],
                "delivery.intraday_channels": ["telegram", "email"],
                "delivery.breaking_channels": ["telegram"],
            }
        },
    )
    assert pref_response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    findings = state["analysis"]["alignment_findings"]
    assert any(item["code"] == "weak_watchlist_support" for item in findings)

    checks = {item["key"]: item for item in state["analysis"]["health_checks"]}
    assert checks["watchlist_support"]["status"] == "needs_attention"
    assert checks["delivery_readiness"]["status"] == "strong"


def test_analysis_scenario_stress_identifies_semiconductor_drawdown(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "holdings:",
            "  - symbol: NVDA",
            "    weight_pct: 12.0",
            "  - symbol: AMD",
            "    weight_pct: 10.0",
            "  - symbol: MSFT",
            "    weight_pct: 8.0",
        ]
    ).encode("utf-8")
    response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    stress = state["analysis"]["scenario_stress"]
    assert "Static sector-shock test" in stress["methodology"]

    semis = next(item for item in stress["scenarios"] if item["key"] == "semis_down_10")
    assert semis["estimated_portfolio_impact_pct"] < 0
    assert semis["impact_class"] == "negative"
    assert any(item["symbol"] == "NVDA" for item in semis["top_holdings"])
    assert any(item["label"] == "Semiconductors" for item in semis["top_sectors"])


def test_analysis_data_quality_flags_duplicates_missing_weights_and_sector_gaps(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "holdings:",
            "  - symbol: AAPL",
            "    weight_pct: 7.0",
            "  - symbol: AAPL",
            "  - symbol: ZZZZ",
            "    weight_pct: 5.0",
        ]
    ).encode("utf-8")
    response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    quality = state["analysis"]["data_quality"]
    issue_codes = {item["code"] for item in quality["issues"]}
    assert "missing_weights" in issue_codes
    assert "duplicate_symbols" in issue_codes
    assert "unlabeled_buckets" in issue_codes
    assert "sector_inference_gaps" in issue_codes
    assert quality["duplicate_symbols"][0]["symbol"] == "AAPL"
    assert "ZZZZ" in quality["inferred_sector_gaps"]
