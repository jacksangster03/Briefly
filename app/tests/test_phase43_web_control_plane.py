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
    assert "Market Briefing" in response.text
    assert "Watchlists" in response.text
    assert "Coverage" in response.text
    assert "Delivery" in response.text
    assert "Morning Composition" in response.text
    assert "Portfolio Workbench" in response.text
    assert "Audit" in response.text
    assert "Saved settings override your default profile values" in response.text
    assert "<h2>Market Briefing · Watchlists & Coverage Priorities</h2>" in response.text
    assert "<h2>Policy</h2>" not in response.text
    assert "<h2>Holdings</h2>" not in response.text
    assert "<h2>Risk &amp; Benchmark Analytics</h2>" not in response.text
    assert "Home Region Focus" in response.text
    assert "Region Weight — US" in response.text
    assert "Region Weight — LATAM" in response.text


def test_ui_home_renders_workspace_cards(client):
    response = client.get("/ui?profile=default_user")
    assert response.status_code == 200
    assert "Briefly Home" in response.text
    assert "What Matters Now" in response.text
    assert "Open Portfolio" in response.text
    assert "Review Risk" in response.text
    assert "Market Briefing" in response.text
    assert "Portfolio Workbench" in response.text
    assert "Audit & advanced controls" in response.text
    assert "/ui/briefing?profile=default_user" in response.text
    assert "/ui/portfolio?profile=default_user" in response.text
    assert "/ui/audit?profile=default_user" in response.text


def test_ui_workspace_routes_set_initial_module_and_section(client):
    briefing = client.get("/ui/briefing?profile=default_user")
    assert briefing.status_code == 200
    assert 'data-page-key="briefing_home"' in briefing.text
    assert 'data-initial-section="section-coverage"' in briefing.text

    portfolio = client.get("/ui/portfolio?profile=default_user")
    assert portfolio.status_code == 200
    assert 'data-page-key="portfolio_home"' in portfolio.text
    assert 'data-initial-section="section-overview"' in portfolio.text

    risk = client.get("/ui/portfolio/risk?profile=default_user")
    assert risk.status_code == 200
    assert 'data-page-key="portfolio_risk"' in risk.text
    assert 'data-initial-section="section-risk"' in risk.text

    simulation = client.get("/ui/portfolio/simulation?profile=default_user")
    assert simulation.status_code == 200
    assert 'data-page-key="portfolio_simulation"' in simulation.text
    assert 'data-initial-section="section-simulation"' in simulation.text

    audit = client.get("/ui/audit?profile=default_user")
    assert audit.status_code == 200
    assert 'data-page-key="audit_home"' in audit.text
    assert 'data-initial-section="section-audit"' in audit.text


def test_portfolio_root_is_summary_only(client):
    response = client.get("/ui/portfolio?profile=default_user")
    assert response.status_code == 200
    assert "<h2>Overview</h2>" in response.text
    assert "Top Actions" in response.text
    assert "<h2>Holdings</h2>" not in response.text
    assert "<h2>Policy</h2>" not in response.text
    assert "<h2>Allocation</h2>" not in response.text
    assert "<h2>Risk &amp; Benchmark Analytics</h2>" not in response.text
    assert "<h2>Simulation Lab</h2>" not in response.text


def test_builder_tabs_only_show_in_builder_routes(client):
    builder = client.get("/ui/portfolio/builder?profile=default_user")
    assert builder.status_code == 200
    assert "Builder tabs" in builder.text
    assert "<h2>Holdings</h2>" in builder.text
    assert "<h2>Policy</h2>" not in builder.text

    diagnostics = client.get("/ui/portfolio/diagnostics?profile=default_user")
    assert diagnostics.status_code == 200
    assert "Builder tabs" not in diagnostics.text


def test_builder_holdings_can_load_validation_preset(client):
    builder = client.get("/ui/portfolio/holdings?profile=default_user")
    assert builder.status_code == 200
    assert "Load Test Preset" in builder.text
    assert "allocation_drift_case" in builder.text

    response = client.post(
        "/ui/profile/default_user/holdings/load-preset",
        data={
            "preset_name": "allocation_drift_case",
            "ui_page": "portfolio_builder_holdings",
        },
    )
    assert response.status_code == 200
    assert "Loaded preset &#39;allocation_drift_case&#39;" in response.text
    assert "NVDA" in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    assert len(state["holdings"]) >= 5
    assert state["analysis"]["rebalance_proposal"]["available"] is True


def test_policy_and_allocation_incomplete_state_labels(client):
    policy = client.get("/ui/portfolio/policy?profile=default_user")
    assert policy.status_code == 200
    assert "unavailable" in policy.text.lower()

    allocation = client.get("/ui/portfolio/allocation?profile=default_user")
    assert allocation.status_code == 200
    assert "Target/min/max band not fully configured." in allocation.text


def test_cma_uses_canonical_asset_dropdowns(client):
    cma = client.get("/ui/portfolio/cma?profile=default_user")
    assert cma.status_code == 200
    assert '<select name="cma_asset_class"' in cma.text
    assert 'type="text" name="cma_asset_class"' not in cma.text


def test_ui_root_redirects_to_home(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/ui"


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
    assert "override_summaries" in state["metadata"]
    assert "delivery_summary" in state["metadata"]
    assert "validations" in state
    assert "analysis" in state
    assert "policy" in state
    assert "allocation" in state
    assert "benchmark" in state
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
    assert "policy_fit" in state["analysis"]
    assert "allocation_drift" in state["analysis"]
    assert "benchmark_summary" in state["analysis"]
    assert "health_checks" in state["analysis"]
    assert "data_quality" in state["analysis"]
    assert "ui_home" in state["analysis"]
    assert "what_matters_now" in state["analysis"]["ui_home"]
    assert "status_chips" in state["analysis"]["ui_home"]


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
    assert state["holdings"][0]["sector_label"] == "Semiconductors"
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
    assert state["analysis"]["confidence"]["status"] in {"partial", "low"}
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


def test_state_includes_cma_analytics_key(client):
    response = client.get("/api/v1/profile/default_user/state")
    assert response.status_code == 200
    data = response.json()
    assert "cma_analytics" in data["analysis"]
    assert "cma_entries" in data
    assert "cma_correlations" in data


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
    assert "Morning section visibility saved." in response.text
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
    assert "Coverage preferences saved." in response.text
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
    assert "Delivery preferences saved." in response.text
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


def test_analysis_confidence_and_override_summary_for_clean_portfolio(client):
    yaml_content = "\n".join(
        [
            "profile: default_user",
            "holdings:",
            "  - symbol: NVDA",
            "    weight_pct: 50.0",
            "  - symbol: MSFT",
            "    weight_pct: 50.0",
        ]
    ).encode("utf-8")
    response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["analysis"]["confidence"]["status"] == "high"
    assert state["metadata"]["delivery_summary"]["headline"].startswith("Morning:")


def test_api_policy_allocation_and_benchmark_roundtrip(client):
    policy_response = client.put(
        "/api/v1/profile/default_user/policy",
        json={
            "payload": {
                "investor_type": "family_office",
                "base_currency": "EUR",
                "investment_horizon_years": 10,
                "target_return_percent": 6.0,
                "max_volatility_percent": 12.0,
                "max_drawdown_percent": 18.0,
                "single_name_limit_percent": 10.0,
                "max_equity_percent": 70.0,
                "min_liquid_assets_percent": 10.0,
                "benchmark_policy": "60/30/10 policy mix",
                "rebalancing_policy": "quarterly or 5pp drift",
                "governance_review_frequency": "quarterly",
            }
        },
    )
    assert policy_response.status_code == 200
    assert policy_response.json()["investor_type"] == "family_office"

    allocation_response = client.put(
        "/api/v1/profile/default_user/allocation",
        json={
            "rows": [
                {"asset_class": "equities", "role": "growth", "target_weight_pct": 45, "min_weight_pct": 40, "max_weight_pct": 55},
                {"asset_class": "cash_liquidity", "role": "liquidity", "target_weight_pct": 15, "min_weight_pct": 10, "max_weight_pct": 20},
            ]
        },
    )
    assert allocation_response.status_code == 200
    assert len(allocation_response.json()["rows"]) >= 2

    benchmark_response = client.put(
        "/api/v1/profile/default_user/benchmark",
        json={"payload": {"benchmark_type": "market_index", "name": "ACWI", "base_symbol": "ACWI"}},
    )
    assert benchmark_response.status_code == 200
    assert benchmark_response.json()["base_symbol"] == "ACWI"

    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["policy"]["investor_type"] == "family_office"
    assert state["benchmark"]["base_symbol"] == "ACWI"
    assert state["metadata"]["policy_summary"]["headline"].startswith("Family Office")
    assert any(row["asset_class"] == "cash_liquidity" for row in state["allocation"]["targets"])


def test_policy_fit_flags_single_name_equity_and_liquidity_breaches(client):
    import_response = client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={
            "file": (
                "holdings.yaml",
                io.BytesIO(
                    "\n".join(
                        [
                            "profile: default_user",
                            "holdings:",
                            "  - symbol: NVDA",
                            "    weight_pct: 12.0",
                            "  - symbol: MSFT",
                            "    weight_pct: 10.0",
                            "  - symbol: JPM",
                            "    weight_pct: 10.0",
                        ]
                    ).encode("utf-8")
                ),
                "application/x-yaml",
            )
        },
    )
    assert import_response.status_code == 200

    client.put(
        "/api/v1/profile/default_user/policy",
        json={
            "payload": {
                "single_name_limit_percent": 10.0,
                "max_equity_percent": 25.0,
                "min_liquid_assets_percent": 80.0,
            }
        },
    )
    client.put(
        "/api/v1/profile/default_user/allocation",
        json={
            "rows": [
                {"asset_class": "equities", "role": "growth", "target_weight_pct": 20, "min_weight_pct": 10, "max_weight_pct": 25},
                {"asset_class": "cash_liquidity", "role": "liquidity", "target_weight_pct": 80, "min_weight_pct": 80, "max_weight_pct": 90},
            ]
        },
    )

    state = client.get("/api/v1/profile/default_user/state").json()
    breach_codes = {item["code"] for item in state["analysis"]["policy_fit"]["breaches"]}
    assert "single_name_limit_breach" in breach_codes
    assert "equity_max_breach" in breach_codes
    assert "liquidity_min_breach" in breach_codes
    assert "allocation_band_breach" in breach_codes
    assert state["analysis"]["policy_fit"]["status"] == "needs_attention"
    assert any(row["status"] in {"above_band", "below_band"} for row in state["analysis"]["allocation_drift"]["rows"])


def test_ui_htmx_save_policy_allocation_and_benchmark_render_success(client):
    policy_response = client.post(
        "/ui/profile/default_user/save/policy",
        data={
            "policy_investor_type": "family_office",
            "policy_base_currency": "EUR",
            "policy_investment_horizon_years": "10",
            "policy_target_return_percent": "6.0",
        },
        headers={"HX-Request": "true"},
    )
    assert policy_response.status_code == 200
    assert "Investor policy saved." in policy_response.text

    allocation_response = client.post(
        "/ui/profile/default_user/save/allocation",
        data={
            "allocation_asset_class": ["equities", "cash_liquidity"],
            "allocation_role": ["growth", "liquidity"],
            "allocation_target": ["45", "15"],
            "allocation_min": ["40", "10"],
            "allocation_max": ["55", "20"],
        },
        headers={"HX-Request": "true"},
    )
    assert allocation_response.status_code == 200
    assert "Strategic allocation targets saved." in allocation_response.text

    benchmark_response = client.post(
        "/ui/profile/default_user/save/benchmark",
        data={
            "benchmark_type": "market_index",
            "benchmark_name": "ACWI",
            "benchmark_base_symbol": "ACWI",
            "benchmark_components": "[]",
        },
        headers={"HX-Request": "true"},
    )
    assert benchmark_response.status_code == 200
    assert "Benchmark configuration saved." in benchmark_response.text


# ── Holdings editor (inline save) ─────────────────────────────────────────────

def test_ui_save_holdings_creates_positions_from_form(client):
    """HTMX editor form saves holdings via the new /holdings/save route."""
    response = client.post(
        "/ui/profile/default_user/holdings/save",
        data={
            "holding_symbol": ["NVDA", "AAPL"],
            "holding_weight": ["8.5", "6.5"],
            "holding_bucket": ["core", "core"],
        },
    )
    assert response.status_code == 200
    assert "Saved 2 holding" in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    symbols = [h["symbol"] for h in state["holdings"]]
    assert "NVDA" in symbols
    assert "AAPL" in symbols
    nvda = next(h for h in state["holdings"] if h["symbol"] == "NVDA")
    assert nvda["weight_pct"] == pytest.approx(8.5)
    assert nvda["bucket"] == "core"


def test_ui_save_holdings_preserves_existing_metadata(client):
    """Weight/bucket edits must not wipe shares and avg_cost from an earlier import."""
    import io as _io

    yaml_content = (
        "profile: default_user\n"
        "holdings:\n"
        "  - symbol: NVDA\n"
        "    weight_pct: 8.5\n"
        "    shares: 10.0\n"
        "    avg_cost: 750.00\n"
    ).encode("utf-8")
    client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("h.yaml", _io.BytesIO(yaml_content), "application/x-yaml")},
    )

    # Now update weight via the editor — shares/avg_cost should be preserved.
    client.post(
        "/ui/profile/default_user/holdings/save",
        data={
            "holding_symbol": ["NVDA"],
            "holding_weight": ["9.0"],
            "holding_bucket": ["core"],
        },
    )

    state = client.get("/api/v1/profile/default_user/state").json()
    nvda = next(h for h in state["holdings"] if h["symbol"] == "NVDA")
    assert nvda["weight_pct"] == pytest.approx(9.0)
    assert nvda["shares"] == pytest.approx(10.0)
    assert nvda["avg_cost"] == pytest.approx(750.0)


def test_ui_save_holdings_deduplicates_repeated_symbols(client):
    """Duplicate symbols in the form payload should be silently collapsed."""
    response = client.post(
        "/ui/profile/default_user/holdings/save",
        data={
            "holding_symbol": ["AAPL", "AAPL", "MSFT"],
            "holding_weight": ["5.0", "3.0", "7.0"],
            "holding_bucket": ["core", "core", "core"],
        },
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    symbols = [h["symbol"] for h in state["holdings"]]
    assert symbols.count("AAPL") == 1
    assert "MSFT" in symbols


def test_ui_save_holdings_accepts_empty_weight(client):
    """A blank weight field should save as None, not raise an error."""
    response = client.post(
        "/ui/profile/default_user/holdings/save",
        data={
            "holding_symbol": ["TSLA"],
            "holding_weight": [""],
            "holding_bucket": ["satellite"],
        },
    )
    assert response.status_code == 200

    state = client.get("/api/v1/profile/default_user/state").json()
    tsla = next((h for h in state["holdings"] if h["symbol"] == "TSLA"), None)
    assert tsla is not None
    assert tsla["weight_pct"] is None


def test_ui_save_holdings_clears_when_no_rows_submitted(client):
    """Submitting an empty form (no rows) replaces the snapshot with zero holdings."""
    client.post(
        "/ui/profile/default_user/holdings/save",
        data={"holding_symbol": ["NVDA"], "holding_weight": ["8.5"], "holding_bucket": ["core"]},
    )
    assert len(client.get("/api/v1/profile/default_user/state").json()["holdings"]) == 1

    response = client.post("/ui/profile/default_user/holdings/save", data={})
    assert response.status_code == 200
    state = client.get("/api/v1/profile/default_user/state").json()
    assert state["holdings"] == []


def test_state_includes_risk_analytics_key(client):
    """GET /api/v1/profile/default_user/state must include risk_analytics in analysis."""
    response = client.get("/api/v1/profile/default_user/state")
    assert response.status_code == 200
    data = response.json()
    assert "risk_analytics" in data["analysis"]
