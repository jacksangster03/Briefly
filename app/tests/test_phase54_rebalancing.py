"""Phase 5.4 tests: Rebalancing & Implementation Engine."""

from __future__ import annotations

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
    db_path = tmp_path / "phase54_test.db"
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
        "profiles:\n  default_user:\n    timezone: UTC\n    home_region: us\n"
        "    morning_brief_time: '07:00'\n    watchlist_primary: []\n"
        "    watchlist_secondary: []\n    watchlist_monitor: []\n"
    )
    (config_dir / "watchlists.example.yaml").write_text("watchlists: {}\n")
    (config_dir / "sectors.yaml").write_text(
        "sectors:\n  technology:\n    etf: XLK\n    display_name: Technology\n    key_names: [AAPL, MSFT, NVDA]\n"
    )
    return Settings(configs_dir=str(config_dir), dry_run=True)


@pytest.fixture
def client(test_settings):
    app = create_web_app(test_settings)
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _drift_row(asset_class, actual, target, min_pct, max_pct, status):
    return {
        "asset_class": asset_class,
        "label": asset_class.replace("_", " ").title(),
        "actual_pct": actual,
        "target_pct": target,
        "min_pct": min_pct,
        "max_pct": max_pct,
        "drift_pct": (actual - target) if actual is not None and target is not None else None,
        "status": status,
    }


_DEFAULT_CONFIG = {
    "method": "drift_threshold",
    "drift_threshold_pct": 5.0,
    "frequency": "quarterly",
    "portfolio_value": None,
    "transaction_cost_bps": 10.0,
    "min_trade_pct": 0.5,
    "tax_aware": False,
    "notes": "",
}

_PROFILE = "default_user"


# ── Service unit tests ────────────────────────────────────────────────────────

def test_trade_above_band_is_sell(isolated_db):
    from app.rebalancing.service import _build_trade_list
    rows = [_drift_row("equities", actual=60.0, target=50.0, min_pct=45.0, max_pct=55.0, status="above_band")]
    trades = _build_trade_list(rows, _DEFAULT_CONFIG, [])
    t = trades[0]
    assert t["direction"] == "sell"
    assert t["trade_pct"] == pytest.approx(5.0, abs=0.01)
    assert not t["skipped"]


def test_trade_below_band_is_buy(isolated_db):
    from app.rebalancing.service import _build_trade_list
    rows = [_drift_row("equities", actual=30.0, target=50.0, min_pct=40.0, max_pct=55.0, status="below_band")]
    trades = _build_trade_list(rows, _DEFAULT_CONFIG, [])
    t = trades[0]
    assert t["direction"] == "buy"
    assert t["trade_pct"] == pytest.approx(10.0, abs=0.01)
    assert not t["skipped"]


def test_trade_within_band_is_hold(isolated_db):
    from app.rebalancing.service import _build_trade_list
    rows = [_drift_row("equities", actual=50.0, target=50.0, min_pct=45.0, max_pct=55.0, status="within_band")]
    trades = _build_trade_list(rows, _DEFAULT_CONFIG, [])
    t = trades[0]
    assert t["direction"] == "hold"
    assert t["skipped"] is True


def test_unconfigured_row_is_skipped(isolated_db):
    from app.rebalancing.service import _build_trade_list
    rows = [_drift_row("alternatives", actual=None, target=None, min_pct=None, max_pct=None, status="unconfigured")]
    trades = _build_trade_list(rows, _DEFAULT_CONFIG, [])
    assert trades[0]["skipped"] is True


def test_min_trade_filter_skips_small_trades(isolated_db):
    from app.rebalancing.service import _build_trade_list
    config = {**_DEFAULT_CONFIG, "min_trade_pct": 5.0}
    rows = [_drift_row("equities", actual=57.0, target=50.0, min_pct=45.0, max_pct=56.0, status="above_band")]
    trades = _build_trade_list(rows, config, [])
    t = trades[0]
    assert t["skipped"] is True
    assert "below minimum" in (t["skip_reason"] or "")


def test_turnover_is_half_of_gross(isolated_db):
    from app.rebalancing.service import _compute_turnover
    trades = [
        {"skipped": False, "trade_direction_pct": 10.0},
        {"skipped": False, "trade_direction_pct": -10.0},
    ]
    assert _compute_turnover(trades) == pytest.approx(10.0, abs=0.01)


def test_cost_estimation_no_portfolio_value(isolated_db):
    from app.rebalancing.service import _estimate_cost
    cost = _estimate_cost(turnover_pct=10.0, cost_bps=10.0, portfolio_value=None)
    assert cost["estimated_cost_bps"] == pytest.approx(2.0, abs=0.01)
    assert cost["estimated_cost_value"] is None


def test_cost_estimation_with_portfolio_value(isolated_db):
    from app.rebalancing.service import _estimate_cost
    cost = _estimate_cost(turnover_pct=10.0, cost_bps=10.0, portfolio_value=100_000.0)
    assert cost["estimated_cost_value"] == pytest.approx(20.0, abs=1.0)
    assert cost["estimated_cost_display"] is not None


def test_status_rebalance_needed_when_high_priority(isolated_db):
    from app.rebalancing.service import _determine_status
    trades = [
        {"skipped": False, "priority": "high"},
    ]
    assert _determine_status(trades, _DEFAULT_CONFIG) == "rebalance_needed"


def test_status_within_tolerance_when_all_hold(isolated_db):
    from app.rebalancing.service import compute_rebalance_proposal
    rows = [
        _drift_row("equities", actual=50.0, target=50.0, min_pct=45.0, max_pct=55.0, status="within_band"),
        _drift_row("high_quality_bonds", actual=25.0, target=25.0, min_pct=20.0, max_pct=30.0, status="within_band"),
    ]
    proposal = compute_rebalance_proposal(_PROFILE, rows, [], _DEFAULT_CONFIG)
    assert proposal["available"] is True
    assert proposal["status"] == "within_tolerance"


def test_no_allocation_returns_unavailable(isolated_db):
    from app.rebalancing.service import compute_rebalance_proposal
    proposal = compute_rebalance_proposal(_PROFILE, [], [], _DEFAULT_CONFIG)
    assert proposal["available"] is False
    assert "no_allocation" in proposal["status"]


def test_config_save_load_roundtrip(isolated_db):
    from app.rebalancing.service import load_rebalancing_config, save_rebalancing_config
    payload = {
        "method": "hybrid",
        "drift_threshold_pct": 7.5,
        "frequency": "semi_annual",
        "portfolio_value": 250000.0,
        "transaction_cost_bps": 15.0,
        "min_trade_pct": 1.0,
        "tax_aware": True,
        "notes": "Test config",
    }
    save_rebalancing_config(_PROFILE, payload)
    loaded = load_rebalancing_config(_PROFILE)
    assert loaded["method"] == "hybrid"
    assert loaded["drift_threshold_pct"] == pytest.approx(7.5)
    assert loaded["portfolio_value"] == pytest.approx(250000.0)
    assert loaded["tax_aware"] is True


def test_proposal_persisted_in_db(isolated_db):
    from app.db.models import RebalanceProposal
    from app.db.session import get_session
    from app.rebalancing.service import compute_rebalance_proposal
    rows = [_drift_row("equities", actual=60.0, target=50.0, min_pct=45.0, max_pct=55.0, status="above_band")]
    compute_rebalance_proposal(_PROFILE, rows, [], _DEFAULT_CONFIG, trigger_type="manual", persist=True)
    with get_session() as session:
        count = session.query(RebalanceProposal).filter_by(profile_name=_PROFILE).count()
    assert count == 1


def test_proposal_history_returns_recent(isolated_db):
    from app.rebalancing.service import compute_rebalance_proposal, load_proposal_history
    rows = [_drift_row("equities", actual=60.0, target=50.0, min_pct=45.0, max_pct=55.0, status="above_band")]
    compute_rebalance_proposal(_PROFILE, rows, [], _DEFAULT_CONFIG, persist=True)
    compute_rebalance_proposal(_PROFILE, rows, [], _DEFAULT_CONFIG, persist=True)
    history = load_proposal_history(_PROFILE, limit=10)
    assert len(history) == 2
    assert history[0]["status"] == "rebalance_needed"


def test_holding_drill_down_maps_to_equities(isolated_db):
    from app.rebalancing.service import compute_rebalance_proposal
    holdings = [
        {"symbol": "AAPL", "weight_pct": 30.0},
        {"symbol": "NVDA", "weight_pct": 20.0},
    ]
    rows = [_drift_row("equities", actual=60.0, target=50.0, min_pct=45.0, max_pct=55.0, status="above_band")]
    proposal = compute_rebalance_proposal(_PROFILE, rows, holdings, _DEFAULT_CONFIG)
    equity_trade = next((t for t in proposal["trades"] if t["asset_class"] == "equities"), None)
    assert equity_trade is not None
    assert len(equity_trade["holdings"]) > 0
    assert equity_trade["holdings"][0]["symbol"] == "AAPL"


# ── API integration tests ─────────────────────────────────────────────────────

def test_get_rebalancing_api_returns_200(client):
    resp = client.get("/api/v1/profile/default_user/rebalancing")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


def test_get_rebalancing_api_has_status_field(client):
    resp = client.get("/api/v1/profile/default_user/rebalancing")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "available" in data


def test_post_generate_rebalance_returns_200(client):
    resp = client.post("/api/v1/profile/default_user/rebalancing/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_get_rebalancing_history_returns_list(client):
    resp = client.get("/api/v1/profile/default_user/rebalancing/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "history" in data
    assert isinstance(data["history"], list)


def test_htmx_save_rebalancing_config_renders_page(client):
    resp = client.post(
        "/ui/profile/default_user/save/rebalancing-config",
        data={
            "rebalance_method": "drift_threshold",
            "rebalance_drift_threshold_pct": "5.0",
            "rebalance_frequency": "quarterly",
            "rebalance_transaction_cost_bps": "10",
            "rebalance_min_trade_pct": "0.5",
        },
    )
    assert resp.status_code == 200
    assert b"Rebalancing configuration saved" in resp.content


def test_htmx_generate_rebalance_renders_page(client):
    resp = client.post("/ui/profile/default_user/rebalance/generate")
    assert resp.status_code == 200


# ── Control-plane tests ───────────────────────────────────────────────────────

def test_state_includes_rebalance_proposal_key(client):
    resp = client.get("/api/v1/profile/default_user/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "rebalance_proposal" in data["analysis"]
