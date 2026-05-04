"""Phase 7A tests: Fixed Income Analytics."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.bonds.service import (
    _classify_quality,
    _get_duration,
    _get_ytm,
    _is_bond_holding,
    compute_bond_analytics,
    delete_bond_override,
    load_bond_overrides,
    load_bond_snapshot_history,
    save_bond_override,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "test_bonds.db"
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


class _FakeHolding:
    def __init__(self, symbol, weight_pct=None, bucket=None):
        self.symbol = symbol
        self.weight_pct = weight_pct
        self.bucket = bucket


# ── Unit tests: classification helpers ───────────────────────────────────────

def test_is_bond_holding_by_bucket():
    assert _is_bond_holding("AAPL", "fixed_income") is True
    assert _is_bond_holding("AAPL", "bonds") is True
    assert _is_bond_holding("AAPL", "equities") is False


def test_is_bond_holding_by_known_etf():
    assert _is_bond_holding("BND", None) is True
    assert _is_bond_holding("TLT", None) is True
    assert _is_bond_holding("AAPL", None) is False


def test_classify_quality_from_etf():
    assert _classify_quality("TLT", None) == "govt"
    assert _classify_quality("LQD", None) == "ig"
    assert _classify_quality("HYG", None) == "hy"


def test_classify_quality_from_override():
    override = {"credit_quality": "hy"}
    assert _classify_quality("BND", override) == "hy"


def test_get_duration_from_etf():
    dur, source = _get_duration("TLT", None)
    assert dur == 16.5
    assert "reference data" in source


def test_get_duration_from_override():
    override = {"modified_duration_yrs": 9.5, "ytm_override_pct": None, "credit_quality": None}
    dur, source = _get_duration("ANYTHING", override)
    assert dur == 9.5
    assert "override" in source


def test_get_duration_fallback_to_quality_default():
    dur, source = _get_duration("UNKNOWNETF", None)
    assert dur > 0
    assert "quality default" in source


def test_get_ytm_from_etf():
    ytm, source = _get_ytm("HYG", None)
    assert ytm == 7.8
    assert "reference data" in source


def test_get_ytm_from_override():
    override = {"ytm_override_pct": 6.0, "modified_duration_yrs": None, "credit_quality": None}
    ytm, source = _get_ytm("X", override)
    assert ytm == 6.0
    assert "override" in source


# ── compute_bond_analytics ────────────────────────────────────────────────────

def test_compute_no_bond_holdings(isolated_db):
    holdings = [_FakeHolding("AAPL", 60.0, "equities"), _FakeHolding("MSFT", 40.0, "equities")]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is False
    assert "No fixed income" in result["error"]


def test_compute_no_weighted_holdings(isolated_db):
    holdings = [_FakeHolding("BND", None, "fixed_income")]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is False
    assert "weight" in result["error"].lower()


def test_compute_single_known_etf(isolated_db):
    holdings = [_FakeHolding("BND", 30.0, "fixed_income"), _FakeHolding("AAPL", 70.0, "equities")]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is True
    assert result["bond_holding_count"] == 1
    assert result["total_bond_weight_pct"] == pytest.approx(30.0)
    assert result["portfolio_duration_yrs"] == pytest.approx(6.1)
    assert result["portfolio_ytm_pct"] == pytest.approx(4.7)
    assert result["rate_sensitivity_pct"] == pytest.approx(-6.1)
    assert "ig" in result["quality_distribution"]
    assert result["summary"] != ""


def test_compute_mixed_quality(isolated_db):
    holdings = [
        _FakeHolding("TLT", 20.0, "fixed_income"),   # govt
        _FakeHolding("LQD", 20.0, "fixed_income"),   # ig
        _FakeHolding("HYG", 10.0, "fixed_income"),   # hy
    ]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is True
    assert result["bond_holding_count"] == 3
    dist = result["quality_distribution"]
    assert "govt" in dist
    assert "ig" in dist
    assert "hy" in dist
    # Weights should sum to ~100
    assert sum(dist.values()) == pytest.approx(100.0, abs=0.1)


def test_compute_with_persist(isolated_db):
    holdings = [_FakeHolding("AGG", 25.0, "fixed_income")]
    result = compute_bond_analytics("test_user", holdings=holdings, persist=True)
    assert result["available"] is True
    history = load_bond_snapshot_history("test_user", limit=5)
    assert len(history) == 1
    assert history[0]["bond_holding_count"] == 1


def test_compute_bond_etf_detected_without_bucket(isolated_db):
    holdings = [_FakeHolding("VGLT", 15.0, None), _FakeHolding("AAPL", 85.0, "equities")]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is True
    assert result["bond_holding_count"] == 1


# ── Overrides CRUD ────────────────────────────────────────────────────────────

def test_save_and_load_override(isolated_db):
    save_bond_override("test_user", "BND", {"modified_duration_yrs": 5.5, "ytm_override_pct": 4.0, "credit_quality": "ig"})
    overrides = load_bond_overrides("test_user")
    assert "BND" in overrides
    assert overrides["BND"]["modified_duration_yrs"] == 5.5
    assert overrides["BND"]["ytm_override_pct"] == 4.0


def test_override_affects_analytics(isolated_db):
    save_bond_override("test_user", "BND", {"modified_duration_yrs": 2.0, "ytm_override_pct": 3.0, "credit_quality": "govt"})
    holdings = [_FakeHolding("BND", 100.0, "fixed_income")]
    result = compute_bond_analytics("test_user", holdings=holdings)
    assert result["available"] is True
    assert result["portfolio_duration_yrs"] == pytest.approx(2.0)
    assert result["portfolio_ytm_pct"] == pytest.approx(3.0)
    assert result["quality_distribution"].get("govt", 0) == pytest.approx(100.0)


def test_upsert_override(isolated_db):
    save_bond_override("test_user", "TLT", {"modified_duration_yrs": 14.0, "ytm_override_pct": 4.1, "credit_quality": "govt"})
    save_bond_override("test_user", "TLT", {"modified_duration_yrs": 10.0, "ytm_override_pct": 3.5, "credit_quality": "govt"})
    overrides = load_bond_overrides("test_user")
    assert overrides["TLT"]["modified_duration_yrs"] == 10.0


def test_delete_override(isolated_db):
    save_bond_override("test_user", "HYG", {"modified_duration_yrs": 3.0, "ytm_override_pct": 7.0, "credit_quality": "hy"})
    delete_bond_override("test_user", "HYG")
    overrides = load_bond_overrides("test_user")
    assert "HYG" not in overrides


def test_override_isolation_by_profile(isolated_db):
    save_bond_override("alice", "BND", {"modified_duration_yrs": 5.0, "ytm_override_pct": 4.0, "credit_quality": "ig"})
    save_bond_override("bob", "BND", {"modified_duration_yrs": 8.0, "ytm_override_pct": 5.0, "credit_quality": "ig"})
    alice_ov = load_bond_overrides("alice")
    bob_ov = load_bond_overrides("bob")
    assert alice_ov["BND"]["modified_duration_yrs"] == 5.0
    assert bob_ov["BND"]["modified_duration_yrs"] == 8.0


# ── History ───────────────────────────────────────────────────────────────────

def test_history_empty(isolated_db):
    history = load_bond_snapshot_history("no_such_user", limit=5)
    assert history == []


def test_history_respects_limit(isolated_db):
    holdings = [_FakeHolding("BND", 30.0, "fixed_income")]
    for _ in range(4):
        compute_bond_analytics("test_user", holdings=holdings, persist=True)
    history = load_bond_snapshot_history("test_user", limit=2)
    assert len(history) == 2
