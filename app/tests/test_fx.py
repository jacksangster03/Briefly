"""Phase 7D tests: Multi-Currency Portfolio Support."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.fx.rates import infer_currency
from app.fx.service import (
    compute_fx_exposure,
    load_fx_config,
    load_currency_exposures,
    save_fx_config,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "test_fx.db"
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
    def __init__(self, symbol, weight_pct):
        self.symbol = symbol
        self.weight_pct = weight_pct


def _mock_fetch_fx_rates(currency_pairs, lookback_days=30, force_refresh=False):
    """Return static rates without hitting yfinance."""
    static = {
        ("EUR", "USD"): [{"date": "2026-05-04", "rate": 1.13}],
        ("GBP", "USD"): [{"date": "2026-05-04", "rate": 1.28}],
        ("CHF", "USD"): [{"date": "2026-05-04", "rate": 1.09}],
        ("CAD", "USD"): [{"date": "2026-05-04", "rate": 0.74}],
        ("EUR", "EUR"): [{"date": "2026-05-04", "rate": 1.0}],
        ("GBP", "GBP"): [{"date": "2026-05-04", "rate": 1.0}],
        ("USD", "USD"): [{"date": "2026-05-04", "rate": 1.0}],
        ("USD", "EUR"): [{"date": "2026-05-04", "rate": 0.885}],
        ("GBP", "EUR"): [{"date": "2026-05-04", "rate": 1.133}],
    }
    return {pair: static.get(pair, []) for pair in currency_pairs}


# ── Currency inference ────────────────────────────────────────────────────────

def test_infer_usd_no_suffix():
    assert infer_currency("AAPL") == "USD"


def test_infer_gbp():
    assert infer_currency("SHEL.L") == "GBP"


def test_infer_eur_paris():
    assert infer_currency("AIR.PA") == "EUR"


def test_infer_eur_frankfurt():
    assert infer_currency("SAP.DE") == "EUR"


def test_infer_cad():
    assert infer_currency("ENB.TO") == "CAD"


def test_infer_aud():
    assert infer_currency("CBA.AX") == "AUD"


def test_infer_jpy():
    assert infer_currency("7203.T") == "JPY"


# ── FX Config ─────────────────────────────────────────────────────────────────

def test_default_config(isolated_db):
    cfg = load_fx_config("ghost_user")
    assert cfg["home_currency"] == "USD"
    assert cfg["hedge_policy"] == "unhedged"


def test_save_and_reload_config(isolated_db):
    save_fx_config("alice", {"home_currency": "EUR", "hedge_policy": "partial"})
    cfg = load_fx_config("alice")
    assert cfg["home_currency"] == "EUR"
    assert cfg["hedge_policy"] == "partial"


def test_save_config_rejects_unknown_currency(isolated_db):
    save_fx_config("bob", {"home_currency": "XYZ", "hedge_policy": "unhedged"})
    cfg = load_fx_config("bob")
    assert cfg["home_currency"] == "USD"


def test_save_config_rejects_unknown_hedge_policy(isolated_db):
    save_fx_config("carol", {"home_currency": "GBP", "hedge_policy": "super_hedge"})
    cfg = load_fx_config("carol")
    assert cfg["hedge_policy"] == "unhedged"


def test_save_config_upserts(isolated_db):
    save_fx_config("dan", {"home_currency": "EUR", "hedge_policy": "unhedged"})
    save_fx_config("dan", {"home_currency": "GBP", "hedge_policy": "full"})
    cfg = load_fx_config("dan")
    assert cfg["home_currency"] == "GBP"
    assert cfg["hedge_policy"] == "full"


# ── compute_fx_exposure ───────────────────────────────────────────────────────

def test_compute_empty_holdings(isolated_db):
    result = compute_fx_exposure("test_user", [])
    assert result["available"] is False


def test_compute_all_usd(isolated_db):
    holdings = [
        FakeHolding("AAPL", 60.0),
        FakeHolding("MSFT", 40.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    assert result["available"] is True
    assert result["foreign_weight_pct"] == 0.0
    assert result["hedge_recommendations"] == []


def test_compute_mixed_currencies(isolated_db):
    holdings = [
        FakeHolding("AAPL", 50.0),
        FakeHolding("SHEL.L", 30.0),
        FakeHolding("AIR.PA", 20.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    assert result["available"] is True
    assert result["foreign_weight_pct"] > 0.0
    currencies = {e["currency"] for e in result["currency_exposure"]}
    assert "GBP" in currencies
    assert "EUR" in currencies


def test_compute_exposure_sums_to_total(isolated_db):
    holdings = [
        FakeHolding("AAPL", 40.0),
        FakeHolding("SHEL.L", 35.0),
        FakeHolding("SAP.DE", 25.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    total = sum(e["weight_pct"] for e in result["currency_exposure"])
    assert abs(total - 100.0) < 0.01


def test_hedge_recommendations_full_policy(isolated_db):
    save_fx_config("test_user", {"home_currency": "USD", "hedge_policy": "full"})
    holdings = [
        FakeHolding("AAPL", 50.0),
        FakeHolding("SHEL.L", 50.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    assert len(result["hedge_recommendations"]) >= 1
    rec = next(r for r in result["hedge_recommendations"] if r["currency"] == "GBP")
    assert rec["recommended_hedge_pct"] == pytest.approx(50.0)


def test_hedge_recommendations_partial_policy(isolated_db):
    save_fx_config("test_user", {"home_currency": "USD", "hedge_policy": "partial"})
    holdings = [
        FakeHolding("SHEL.L", 40.0),
        FakeHolding("AAPL", 60.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    rec = next(r for r in result["hedge_recommendations"] if r["currency"] == "GBP")
    assert rec["recommended_hedge_pct"] == pytest.approx(20.0)


def test_hedge_recommendations_unhedged_policy(isolated_db):
    save_fx_config("test_user", {"home_currency": "USD", "hedge_policy": "unhedged"})
    holdings = [FakeHolding("SHEL.L", 100.0)]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="USD")
    assert result["hedge_recommendations"] == []


def test_compute_persists_exposures(isolated_db):
    holdings = [
        FakeHolding("AAPL", 70.0),
        FakeHolding("SHEL.L", 30.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        compute_fx_exposure("test_user", holdings, home_currency="USD")
    exposures = load_currency_exposures("test_user")
    symbols = {e["symbol"] for e in exposures}
    assert "AAPL" in symbols
    assert "SHEL.L" in symbols


def test_profile_isolation(isolated_db):
    holdings_alice = [FakeHolding("AAPL", 100.0)]
    holdings_bob = [FakeHolding("SHEL.L", 100.0)]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        compute_fx_exposure("alice", holdings_alice, home_currency="USD")
        compute_fx_exposure("bob", holdings_bob, home_currency="USD")
    alice_exp = load_currency_exposures("alice")
    bob_exp = load_currency_exposures("bob")
    assert all(e["symbol"] == "AAPL" for e in alice_exp)
    assert all(e["symbol"] == "SHEL.L" for e in bob_exp)


def test_eur_home_currency(isolated_db):
    holdings = [
        FakeHolding("AAPL", 50.0),
        FakeHolding("AIR.PA", 50.0),
    ]
    with patch("app.fx.service.fetch_fx_rates", side_effect=_mock_fetch_fx_rates):
        result = compute_fx_exposure("test_user", holdings, home_currency="EUR")
    assert result["home_currency"] == "EUR"
    eur_entry = next((e for e in result["currency_exposure"] if e["currency"] == "EUR"), None)
    assert eur_entry is not None
    assert not eur_entry["is_foreign"]
