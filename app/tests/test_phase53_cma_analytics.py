"""Phase 5.3 tests: CMA Builder + Expected Portfolio Analytics."""

from __future__ import annotations

import io
import math
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings
from app.web.app import create_web_app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "phase53_test.db"
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
        "\n".join([
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
        ])
    )
    (config_dir / "watchlists.example.yaml").write_text(
        "primary: [AAPL, MSFT]\nsecondary: [NVDA]\nmonitor: [AMD]\n"
    )
    (config_dir / "sectors.yaml").write_text(
        "\n".join([
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
        ])
    )
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


# ── Service unit tests ─────────────────────────────────────────────────────────

def test_expected_return_weighted_correctly():
    """60% equities (7.5%) + 40% bonds (3.5%) = 5.9%."""
    from app.cma.service import compute_expected_portfolio_return

    weights = {"equities": 0.6, "high_quality_bonds": 0.4}
    cma_entries = {
        "equities": {"expected_return_pct": 7.5, "expected_volatility_pct": 16.0},
        "high_quality_bonds": {"expected_return_pct": 3.5, "expected_volatility_pct": 5.0},
    }
    result = compute_expected_portfolio_return(weights, cma_entries)
    assert result == pytest.approx(5.9, rel=1e-6)


def test_expected_volatility_two_assets():
    """Verify two-asset portfolio variance formula."""
    from app.cma.service import compute_expected_portfolio_volatility

    weights = {"equities": 0.6, "bonds": 0.4}
    cma_entries = {
        "equities": {"expected_volatility_pct": 16.0},
        "bonds": {"expected_volatility_pct": 5.0},
    }
    asset_classes = ["equities", "bonds"]
    corr_matrix = [[1.0, 0.2], [0.2, 1.0]]

    result = compute_expected_portfolio_volatility(weights, cma_entries, corr_matrix, asset_classes)

    sigma_e = 0.16
    sigma_b = 0.05
    w_e = 0.6
    w_b = 0.4
    rho = 0.2
    expected_var = (w_e**2 * sigma_e**2 + w_b**2 * sigma_b**2 + 2 * w_e * w_b * rho * sigma_e * sigma_b)
    expected_vol = math.sqrt(expected_var) * 100.0
    assert result == pytest.approx(expected_vol, rel=1e-4)


def test_expected_sharpe_correct():
    """(E[R] - rf) / E[vol]."""
    from app.cma.service import compute_expected_portfolio_return, compute_expected_portfolio_volatility

    weights = {"equities": 0.6, "bonds": 0.4}
    cma_entries = {
        "equities": {"expected_return_pct": 7.5, "expected_volatility_pct": 16.0},
        "bonds": {"expected_return_pct": 3.5, "expected_volatility_pct": 5.0},
    }
    asset_classes = ["equities", "bonds"]
    corr_matrix = [[1.0, 0.2], [0.2, 1.0]]

    exp_ret = compute_expected_portfolio_return(weights, cma_entries)
    exp_vol = compute_expected_portfolio_volatility(weights, cma_entries, corr_matrix, asset_classes)
    rf = 4.5
    sharpe = (exp_ret - rf) / exp_vol
    expected_sharpe = (5.9 - 4.5) / exp_vol
    assert exp_ret == pytest.approx(5.9, rel=1e-6)
    assert sharpe == pytest.approx(expected_sharpe, rel=1e-4)


def test_correlation_matrix_symmetry(isolated_db):
    """Saving (a,b) and (b,a) both work; diagonal is 1.0."""
    from app.cma.service import build_correlation_matrix, save_cma_correlations

    save_cma_correlations("test_sym", [
        {"asset_class_a": "equities", "asset_class_b": "bonds", "correlation": 0.3},
    ])

    from app.cma.service import load_cma_correlations
    corrs = load_cma_correlations("test_sym")
    asset_classes = ["equities", "bonds"]
    matrix = build_correlation_matrix(asset_classes, corrs)

    assert matrix[0][0] == pytest.approx(1.0)
    assert matrix[1][1] == pytest.approx(1.0)
    assert matrix[0][1] == pytest.approx(0.3)
    assert matrix[1][0] == pytest.approx(0.3)


def test_policy_gap_positive_when_above_target(isolated_db):
    """E[Rp] > target_return_pct yields positive return gap."""
    from app.cma.service import save_cma_entries, compute_cma_analytics

    save_cma_entries("test_pos", [
        {"asset_class": "equities", "expected_return_pct": 9.0, "expected_volatility_pct": 16.0},
    ])
    actual_alloc = [{"asset_class": "equities", "actual_pct": 100.0}]
    target_alloc = [{"asset_class": "equities", "target_weight_pct": 100.0}]
    policy = {"target_return_percent": 7.0, "max_volatility_percent": 20.0}

    result = compute_cma_analytics("test_pos", actual_alloc, target_alloc, policy)
    assert result["available"] is True
    assert result["policy_gap"]["return_gap_pct"] > 0
    assert result["policy_gap"]["return_meets_target"] is True


def test_policy_gap_negative_when_below_target(isolated_db):
    """E[Rp] < target_return_pct yields negative return gap and breach in policy_fit."""
    from app.cma.service import save_cma_entries, compute_cma_analytics

    save_cma_entries("test_neg", [
        {"asset_class": "equities", "expected_return_pct": 5.0, "expected_volatility_pct": 16.0},
    ])
    actual_alloc = [{"asset_class": "equities", "actual_pct": 100.0}]
    target_alloc = [{"asset_class": "equities", "target_weight_pct": 100.0}]
    policy = {"target_return_percent": 8.0, "max_volatility_percent": 20.0}

    result = compute_cma_analytics("test_neg", actual_alloc, target_alloc, policy)
    assert result["available"] is True
    assert result["policy_gap"]["return_gap_pct"] < 0
    assert result["policy_gap"]["return_meets_target"] is False


def test_saa_gap_returns_shortfall(isolated_db):
    """Actual weights differ from SAA: expected returns differ."""
    from app.cma.service import save_cma_entries, compute_cma_analytics

    save_cma_entries("test_saa", [
        {"asset_class": "equities", "expected_return_pct": 8.0, "expected_volatility_pct": 16.0},
        {"asset_class": "bonds", "expected_return_pct": 3.0, "expected_volatility_pct": 5.0},
    ])
    actual_alloc = [
        {"asset_class": "equities", "actual_pct": 40.0},
        {"asset_class": "bonds", "actual_pct": 60.0},
    ]
    target_alloc = [
        {"asset_class": "equities", "target_weight_pct": 70.0},
        {"asset_class": "bonds", "target_weight_pct": 30.0},
    ]
    result = compute_cma_analytics("test_saa", actual_alloc, target_alloc, None)

    assert result["available"] is True
    actual_ret = result["actual"]["expected_return_pct"]
    saa_ret = result["saa"]["expected_return_pct"]
    assert abs(result["saa_gap"]["return_shortfall_pct"] - (actual_ret - saa_ret)) < 0.01
    assert actual_ret < saa_ret


def test_no_cma_returns_unavailable_block(isolated_db):
    """No CMA entries yields available=False, no crash."""
    from app.cma.service import compute_cma_analytics

    result = compute_cma_analytics("test_empty", [], [], None)
    assert result["available"] is False
    assert result["error"] is not None
    assert "entries" in result


def test_cma_save_load_roundtrip(isolated_db):
    """Save 3 entries, load back, values match."""
    from app.cma.service import save_cma_entries, load_cma_entries

    rows = [
        {"asset_class": "equities", "expected_return_pct": 7.5, "expected_volatility_pct": 16.0, "notes": "MSCI World"},
        {"asset_class": "gold", "expected_return_pct": 4.0, "expected_volatility_pct": 18.0, "notes": ""},
        {"asset_class": "cash_liquidity", "expected_return_pct": 4.5, "expected_volatility_pct": 0.5, "notes": ""},
    ]
    save_cma_entries("test_roundtrip", rows)
    loaded = load_cma_entries("test_roundtrip")
    loaded_by_ac = {row["asset_class"]: row for row in loaded}

    assert len(loaded) == 3
    assert loaded_by_ac["equities"]["expected_return_pct"] == pytest.approx(7.5)
    assert loaded_by_ac["equities"]["expected_volatility_pct"] == pytest.approx(16.0)
    assert loaded_by_ac["equities"]["notes"] == "MSCI World"
    assert loaded_by_ac["gold"]["expected_return_pct"] == pytest.approx(4.0)
    assert loaded_by_ac["cash_liquidity"]["expected_volatility_pct"] == pytest.approx(0.5)


def test_correlation_save_load_roundtrip(isolated_db):
    """Save 2 correlation pairs, load back, values match."""
    from app.cma.service import save_cma_correlations, load_cma_correlations

    rows = [
        {"asset_class_a": "equities", "asset_class_b": "bonds", "correlation": -0.1},
        {"asset_class_a": "equities", "asset_class_b": "gold", "correlation": 0.05},
    ]
    save_cma_correlations("test_corr_rt", rows)
    loaded = load_cma_correlations("test_corr_rt")

    assert len(loaded) == 2
    corr_map = {(r["asset_class_a"], r["asset_class_b"]): r["correlation"] for r in loaded}
    assert corr_map[("equities", "bonds")] == pytest.approx(-0.1)
    assert corr_map[("equities", "gold")] == pytest.approx(0.05)


# ── API integration tests ──────────────────────────────────────────────────────

def test_get_cma_api_returns_200(client):
    response = client.get("/api/v1/profile/default_user/cma")
    assert response.status_code == 200
    payload = response.json()
    assert "cma_analytics" in payload
    assert "profile" in payload


def test_get_cma_api_available_false_no_entries(client):
    response = client.get("/api/v1/profile/default_user/cma")
    assert response.status_code == 200
    payload = response.json()
    assert payload["cma_analytics"]["available"] is False


def test_put_cma_api_saves_entries(client):
    response = client.put(
        "/api/v1/profile/default_user/cma",
        json={
            "rows": [
                {"asset_class": "equities", "expected_return_pct": 7.5, "expected_volatility_pct": 16.0},
                {"asset_class": "high_quality_bonds", "expected_return_pct": 3.5, "expected_volatility_pct": 5.0},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert len(data["rows"]) == 2

    check = client.get("/api/v1/profile/default_user/cma")
    assert check.json()["cma_analytics"]["available"] is True


def test_htmx_save_cma_renders_page(client):
    response = client.post(
        "/ui/profile/default_user/save/cma",
        data={
            "cma_asset_class": ["equities", "high_quality_bonds"],
            "cma_expected_return": ["7.5", "3.5"],
            "cma_expected_vol": ["16.0", "5.0"],
            "cma_notes": ["", ""],
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "CMA assumptions saved." in response.text


def test_htmx_save_cma_correlations_renders_page(client):
    client.put(
        "/api/v1/profile/default_user/cma",
        json={
            "rows": [
                {"asset_class": "equities", "expected_return_pct": 7.5, "expected_volatility_pct": 16.0},
                {"asset_class": "bonds", "expected_return_pct": 3.5, "expected_volatility_pct": 5.0},
            ]
        },
    )
    response = client.post(
        "/ui/profile/default_user/save/cma-correlations",
        data={"corr__equities__bonds": "0.15"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "CMA correlations saved." in response.text


def test_state_includes_cma_analytics_key(client):
    response = client.get("/api/v1/profile/default_user/state")
    assert response.status_code == 200
    data = response.json()
    assert "cma_analytics" in data["analysis"]
    assert "cma_entries" in data
    assert "cma_correlations" in data
