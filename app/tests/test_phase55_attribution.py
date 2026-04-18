"""Phase 5.5 tests: Brinson-Hood-Beebower Attribution (CMA-based)."""

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
    db_path = tmp_path / "phase55_test.db"
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

_PROFILE = "default_user"


def _cma_analytics(entries: list[dict]) -> dict:
    """Build a minimal cma_analytics dict with entries for testing."""
    return {
        "available": True,
        "entries": [
            {
                "asset_class": e["asset_class"],
                "expected_return_pct": e["expected_return_pct"],
                "expected_volatility_pct": 0.0,
            }
            for e in entries
        ],
    }


def _actual_alloc(items: list[tuple[str, float]]) -> list[dict]:
    return [{"asset_class": ac, "actual_pct": pct} for ac, pct in items]


def _target_alloc(items: list[tuple[str, float]]) -> list[dict]:
    return [{"asset_class": ac, "target_weight_pct": pct} for ac, pct in items]


# ── Service unit tests ────────────────────────────────────────────────────────

def test_allocation_effect_positive_for_overweight_high_return(isolated_db):
    """Overweighting a high-return asset class should yield a positive allocation effect."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 70.0), ("high_quality_bonds", 30.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    equity_row = next(r for r in result["rows"] if r["asset_class"] == "equities")
    assert equity_row["allocation_effect_pct"] > 0


def test_allocation_effect_negative_for_overweight_low_return(isolated_db):
    """Overweighting a low-return asset class (below benchmark average) yields negative effect."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "cash_liquidity", "expected_return_pct": 1.0},
    ])
    actual = _actual_alloc([("equities", 40.0), ("cash_liquidity", 60.0)])
    target = _target_alloc([("equities", 70.0), ("cash_liquidity", 30.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    cash_row = next(r for r in result["rows"] if r["asset_class"] == "cash_liquidity")
    assert cash_row["allocation_effect_pct"] < 0


def test_allocation_effect_zero_when_weights_equal(isolated_db):
    """When actual weight equals target weight, allocation effect is exactly zero."""
    from app.attribution.service import _brinson_decomposition

    segments = [
        {"asset_class": "equities", "label": "Equities", "role": "core", "w_portfolio": 0.6, "w_benchmark": 0.6, "r_expected": 8.0},
        {"asset_class": "bonds", "label": "Bonds", "role": "ballast", "w_portfolio": 0.4, "w_benchmark": 0.4, "r_expected": 3.0},
    ]
    r_b = 0.6 * 8.0 + 0.4 * 3.0
    rows = _brinson_decomposition(segments, r_b)

    for row in rows:
        assert abs(row["allocation_effect_pct"]) < 1e-9


def test_selection_effect_is_zero_in_v1(isolated_db):
    """Selection effect must be zero in v1 (CMA-based, R_p[i] == R_b[i])."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 65.0), ("high_quality_bonds", 35.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    for row in result["rows"]:
        assert abs(row["selection_effect_pct"]) < 1e-9
    assert abs(result["selection_effect_pct"]) < 1e-9


def test_interaction_effect_is_zero_in_v1(isolated_db):
    """Interaction effect must be zero in v1."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 65.0), ("high_quality_bonds", 35.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    for row in result["rows"]:
        assert abs(row["interaction_effect_pct"]) < 1e-9
    assert abs(result["interaction_effect_pct"]) < 1e-9


def test_total_effects_sum_to_active_return(isolated_db):
    """Sum of all effects must equal the active return within floating-point tolerance."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 9.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.5},
        {"asset_class": "gold", "expected_return_pct": 5.0},
    ])
    actual = _actual_alloc([("equities", 55.0), ("high_quality_bonds", 30.0), ("gold", 15.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 30.0), ("gold", 10.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    total_effects = (
        result["allocation_effect_pct"]
        + result["selection_effect_pct"]
        + result["interaction_effect_pct"]
    )
    assert abs(total_effects - result["active_return_pct"]) < 1e-5


def test_no_cma_returns_unavailable(isolated_db):
    """When cma_analytics.available is False, attribution must return unavailable."""
    from app.attribution.service import compute_attribution

    cma = {"available": False, "error": "No CMA configured", "entries": []}
    actual = _actual_alloc([("equities", 60.0)])
    target = _target_alloc([("equities", 60.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is False
    assert result["error"]


def test_no_allocation_returns_unavailable(isolated_db):
    """Empty actual allocation must return an unavailable block."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([{"asset_class": "equities", "expected_return_pct": 8.0}])
    result = compute_attribution(_PROFILE, [], [], cma, policy=None)
    assert result["available"] is False


def test_waterfall_has_five_rows(isolated_db):
    """The waterfall list must always have exactly 5 entries."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 70.0), ("high_quality_bonds", 30.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True
    assert len(result["waterfall"]) == 5


def test_top_contributor_is_largest_positive(isolated_db):
    """top_contributor should match the asset class with the largest positive allocation effect."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 10.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 2.0},
        {"asset_class": "gold", "expected_return_pct": 6.0},
    ])
    actual = _actual_alloc([("equities", 70.0), ("high_quality_bonds", 20.0), ("gold", 10.0)])
    target = _target_alloc([("equities", 50.0), ("high_quality_bonds", 40.0), ("gold", 10.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    positive_rows = [r for r in result["rows"] if r["allocation_effect_pct"] > 0]
    if positive_rows:
        best = max(positive_rows, key=lambda r: r["allocation_effect_pct"])
        assert result["top_contributor"] == best["asset_class"]


def test_top_detractor_is_largest_negative(isolated_db):
    """top_detractor should match the asset class with the largest negative allocation effect."""
    from app.attribution.service import compute_attribution

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 10.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 2.0},
        {"asset_class": "cash_liquidity", "expected_return_pct": 0.5},
    ])
    actual = _actual_alloc([("equities", 30.0), ("high_quality_bonds", 20.0), ("cash_liquidity", 50.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 30.0), ("cash_liquidity", 10.0)])

    result = compute_attribution(_PROFILE, actual, target, cma, policy=None)
    assert result["available"] is True

    negative_rows = [r for r in result["rows"] if r["allocation_effect_pct"] < 0]
    if negative_rows:
        worst = min(negative_rows, key=lambda r: r["allocation_effect_pct"])
        assert result["top_detractor"] == worst["asset_class"]


def test_attribution_persisted_in_db(isolated_db):
    """compute_attribution with persist=True must create an AttributionSnapshot row."""
    from app.attribution.service import compute_attribution
    from app.db.models import AttributionSnapshot
    from app.db.session import get_session

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 65.0), ("high_quality_bonds", 35.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    compute_attribution(_PROFILE, actual, target, cma, policy=None, persist=True)

    with get_session() as session:
        count = session.query(AttributionSnapshot).filter_by(profile_name=_PROFILE).count()
    assert count == 1


def test_attribution_history_returns_recent(isolated_db):
    """Two persisted computes should appear in history with 2 entries."""
    from app.attribution.service import compute_attribution, load_attribution_history

    cma = _cma_analytics([
        {"asset_class": "equities", "expected_return_pct": 8.0},
        {"asset_class": "high_quality_bonds", "expected_return_pct": 3.0},
    ])
    actual = _actual_alloc([("equities", 65.0), ("high_quality_bonds", 35.0)])
    target = _target_alloc([("equities", 60.0), ("high_quality_bonds", 40.0)])

    compute_attribution(_PROFILE, actual, target, cma, policy=None, persist=True)
    compute_attribution(_PROFILE, actual, target, cma, policy=None, persist=True)

    history = load_attribution_history(_PROFILE, limit=10)
    assert len(history) == 2
    assert "active_return_pct" in history[0]
    assert "allocation_effect_pct" in history[0]


# ── API integration tests ─────────────────────────────────────────────────────

def test_get_attribution_api_returns_200(client):
    resp = client.get("/api/v1/profile/default_user/attribution")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


def test_get_attribution_api_unavailable_no_cma(client):
    resp = client.get("/api/v1/profile/default_user/attribution")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False


def test_post_attribution_generate_returns_200(client):
    resp = client.post("/api/v1/profile/default_user/attribution/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


def test_get_attribution_history_returns_list(client):
    resp = client.get("/api/v1/profile/default_user/attribution/history")
    assert resp.status_code == 200
    data = resp.json()
    assert "history" in data
    assert isinstance(data["history"], list)


def test_htmx_generate_attribution_renders_page(client):
    resp = client.post("/ui/profile/default_user/attribution/generate")
    assert resp.status_code == 200


# ── Control-plane tests ───────────────────────────────────────────────────────

def test_state_includes_attribution_key(client):
    resp = client.get("/api/v1/profile/default_user/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "attribution" in data["analysis"]
