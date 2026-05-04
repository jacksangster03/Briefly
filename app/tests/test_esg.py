"""Phase 7C tests: ESG/SRI Scoring."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.esg.exclusion_taxonomy import ALL_SCREENS, get_exclusion_flags
from app.esg.service import (
    compute_portfolio_esg,
    fetch_esg_scores,
    load_esg_config,
    load_esg_snapshot_history,
    save_esg_config,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "test_esg.db"
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


class FakeHolding:
    def __init__(self, symbol, weight_pct, bucket="equities", sector_override=None):
        self.symbol = symbol
        self.weight_pct = weight_pct
        self.bucket = bucket
        self.sector_override = sector_override


def _mock_fetch_esg(symbol, sector_override=None):
    """Deterministic mock: returns fixed scores by symbol, no yfinance call."""
    scores = {
        "AAPL": {"overall": 72.0, "e_score": 68.0, "s_score": 70.0, "g_score": 78.0,
                 "controversy_level": 1, "provider": "yfinance", "confidence": "high",
                 "source_note": "mock"},
        "MSFT": {"overall": 80.0, "e_score": 82.0, "s_score": 79.0, "g_score": 79.0,
                 "controversy_level": 0, "provider": "yfinance", "confidence": "high",
                 "source_note": "mock"},
        "MO": {"overall": 20.0, "e_score": 18.0, "s_score": 22.0, "g_score": 20.0,
               "controversy_level": 4, "provider": "yfinance", "confidence": "high",
               "source_note": "mock"},
        "BND": {"overall": None, "e_score": None, "s_score": None, "g_score": None,
                "controversy_level": None, "provider": "none", "confidence": "none",
                "source_note": "fixed_income_etf"},
    }
    return scores.get(symbol.upper(), {
        "overall": 50.0, "e_score": 50.0, "s_score": 50.0, "g_score": 50.0,
        "controversy_level": None, "provider": "sector_fallback", "confidence": "low",
        "source_note": "fallback",
    })


# ── Exclusion taxonomy ────────────────────────────────────────────────────────

def test_exclusion_known_tobacco():
    flags = get_exclusion_flags("MO")
    assert "tobacco" in flags


def test_exclusion_known_weapons():
    flags = get_exclusion_flags("LMT")
    assert "weapons" in flags


def test_exclusion_coal():
    flags = get_exclusion_flags("BTU")
    assert "thermal_coal" in flags


def test_exclusion_clean_symbol():
    flags = get_exclusion_flags("AAPL")
    assert flags == []


def test_exclusion_sector_fallback():
    flags = get_exclusion_flags("UNKNWN", sector="Tobacco")
    assert "tobacco" in flags


def test_all_screens_populated():
    assert len(ALL_SCREENS) >= 5
    assert "tobacco" in ALL_SCREENS
    assert "weapons" in ALL_SCREENS


# ── ESG config ────────────────────────────────────────────────────────────────

def test_default_config_returned_when_none(isolated_db):
    cfg = load_esg_config("ghost_user")
    assert "active_screens" in cfg
    assert isinstance(cfg["active_screens"], list)
    assert "tobacco" in cfg["active_screens"]


def test_save_and_reload_config(isolated_db):
    save_esg_config("alice", {"active_screens": ["tobacco", "gambling"]})
    cfg = load_esg_config("alice")
    assert set(cfg["active_screens"]) == {"tobacco", "gambling"}


def test_save_config_rejects_unknown_screens(isolated_db):
    save_esg_config("bob", {"active_screens": ["tobacco", "invalid_screen"]})
    cfg = load_esg_config("bob")
    assert "invalid_screen" not in cfg["active_screens"]
    assert "tobacco" in cfg["active_screens"]


def test_save_config_upserts(isolated_db):
    save_esg_config("carol", {"active_screens": ["tobacco"]})
    save_esg_config("carol", {"active_screens": ["gambling", "weapons"]})
    cfg = load_esg_config("carol")
    assert set(cfg["active_screens"]) == {"gambling", "weapons"}


# ── fetch_esg_scores ──────────────────────────────────────────────────────────

def test_fetch_esg_caches_result(isolated_db):
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result1 = fetch_esg_scores([("AAPL", "technology")], "test_user")
        result2 = fetch_esg_scores([("AAPL", "technology")], "test_user")
    assert result1["AAPL"]["overall"] == result2["AAPL"]["overall"]


def test_fetch_esg_force_refresh(isolated_db):
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg) as mock:
        fetch_esg_scores([("AAPL", None)], "test_user", force_refresh=True)
        fetch_esg_scores([("AAPL", None)], "test_user", force_refresh=True)
    assert mock.call_count == 2


def test_fetch_esg_exclusion_flags_stored(isolated_db):
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = fetch_esg_scores([("MO", "tobacco")], "test_user", force_refresh=True)
    assert "tobacco" in result["MO"]["exclusion_flags"]


# ── compute_portfolio_esg ─────────────────────────────────────────────────────

def test_compute_empty_holdings(isolated_db):
    result = compute_portfolio_esg("test_user", [], persist=False)
    assert result["available"] is False


def test_compute_basic(isolated_db):
    holdings = [
        FakeHolding("AAPL", 60.0, sector_override="technology"),
        FakeHolding("MSFT", 40.0, sector_override="technology"),
    ]
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = compute_portfolio_esg("test_user", holdings, persist=False)
    assert result["available"] is True
    assert result["weighted_overall"] is not None
    assert result["weighted_overall"] > 0
    assert result["exclusion_count"] == 0


def test_compute_flags_excluded_holding(isolated_db):
    holdings = [
        FakeHolding("AAPL", 80.0, sector_override="technology"),
        FakeHolding("MO", 20.0, sector_override="tobacco"),
    ]
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = compute_portfolio_esg("test_user", holdings, persist=False)
    assert result["exclusion_count"] >= 1
    mo_detail = next(h for h in result["holdings_detail"] if h["symbol"] == "MO")
    assert "tobacco" in mo_detail["exclusion_flags"]


def test_compute_coverage_pct_with_no_data(isolated_db):
    holdings = [
        FakeHolding("AAPL", 80.0, sector_override="technology"),
        FakeHolding("BND", 20.0, bucket="fixed_income"),
    ]
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = compute_portfolio_esg("test_user", holdings, persist=False)
    assert result["coverage_pct"] < 100.0


def test_compute_persists_snapshot(isolated_db):
    holdings = [FakeHolding("AAPL", 100.0, sector_override="technology")]
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        compute_portfolio_esg("test_user", holdings, persist=True)
    history = load_esg_snapshot_history("test_user")
    assert len(history) == 1
    assert history[0]["weighted_overall"] is not None


def test_compute_sri_label_strong(isolated_db):
    holdings = [
        FakeHolding("AAPL", 50.0, sector_override="technology"),
        FakeHolding("MSFT", 50.0, sector_override="technology"),
    ]
    save_esg_config("test_user", {"active_screens": ["tobacco", "weapons", "thermal_coal"]})
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = compute_portfolio_esg("test_user", holdings, persist=False)
    assert result["sri_alignment_label"] in {"Strong", "Partial"}


def test_compute_sri_label_weak_due_to_exclusion(isolated_db):
    holdings = [
        FakeHolding("MO", 100.0, sector_override="tobacco"),
    ]
    save_esg_config("test_user", {"active_screens": ["tobacco"]})
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        result = compute_portfolio_esg("test_user", holdings, persist=False)
    assert result["sri_alignment_label"] == "Weak"


def test_profile_isolation(isolated_db):
    holdings = [FakeHolding("AAPL", 100.0)]
    with patch("app.esg.service.fetch_esg_for_symbol", side_effect=_mock_fetch_esg):
        compute_portfolio_esg("alice", holdings, persist=True)
    history_alice = load_esg_snapshot_history("alice")
    history_bob = load_esg_snapshot_history("bob")
    assert len(history_alice) == 1
    assert len(history_bob) == 0
