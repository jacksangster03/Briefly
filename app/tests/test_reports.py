"""Phase 7B tests: PDF Report Generation."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.reports.service import (
    delete_report,
    generate_report,
    get_report_filepath,
    list_reports,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "test_reports.db"
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
def minimal_state():
    """Minimal state dict that won't cause render errors."""
    return {
        "profile": "test_user",
        "policy": {"base_currency": "EUR", "investor_type": "individual"},
        "benchmark": {"name": "ACWI", "base_symbol": "ACWI"},
        "holdings": [
            {"symbol": "AAPL", "weight_pct": 30.0, "bucket": "equities", "sector_override": "technology"},
            {"symbol": "BND",  "weight_pct": 20.0, "bucket": "fixed_income", "sector_override": None},
            {"symbol": "MSFT", "weight_pct": 50.0, "bucket": "equities", "sector_override": "technology"},
        ],
        "analysis": {
            "kpis": {"total_weight_display": "100.0%"},
            "holdings_totals": {"weighted_positions": 3, "holdings_weight_total_display": "100.0%"},
            "risk_analytics": {"available": False, "error": "No benchmark data"},
            "attribution": {"available": False, "error": "No CMA"},
            "bonds_analytics": {
                "available": True,
                "summary": "Bond sleeve is 20%.",
                "bond_holding_count": 1,
                "total_bond_weight_pct": 20.0,
                "portfolio_duration_yrs": 6.1,
                "portfolio_duration_display": "6.1",
                "portfolio_ytm_pct": 4.7,
                "portfolio_ytm_display": "+4.70%",
                "rate_sensitivity_pct": -6.1,
                "rate_sensitivity_display": "-6.1%",
                "quality_distribution": {"ig": 100.0},
                "maturity_distribution": {},
                "holdings_detail": [],
                "computed_at": "2026-05-05T10:00:00Z",
            },
            "cma_analytics": {"available": False, "error": "No CMA"},
            "scenario_stress": {"scenarios": []},
        },
    }


# ── Renderer ──────────────────────────────────────────────────────────────────

def test_renderer_produces_bytes(tmp_path, minimal_state):
    from app.reports.renderer import render_pdf
    output = tmp_path / "test.pdf"
    render_pdf(
        filepath=output,
        profile_name="test_user",
        title="Test Report",
        sections=["cover", "holdings", "risk", "bonds"],
        state=minimal_state,
    )
    assert output.exists()
    assert output.stat().st_size > 1000  # non-trivial PDF


def test_renderer_cover_only(tmp_path, minimal_state):
    from app.reports.renderer import render_pdf
    output = tmp_path / "cover_only.pdf"
    render_pdf(
        filepath=output,
        profile_name="test_user",
        title="Cover Only",
        sections=["cover"],
        state=minimal_state,
    )
    assert output.exists()
    assert output.stat().st_size > 500


def test_renderer_all_sections(tmp_path, minimal_state):
    from app.reports.renderer import render_pdf
    output = tmp_path / "all_sections.pdf"
    render_pdf(
        filepath=output,
        profile_name="test_user",
        title="Full Report",
        sections=["cover", "holdings", "risk", "attribution", "bonds", "scenarios", "cma"],
        state=minimal_state,
    )
    assert output.exists()
    assert output.stat().st_size > 1000


# ── generate_report ───────────────────────────────────────────────────────────

def test_generate_report_success(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    result = generate_report(
        profile_name="test_user",
        state=minimal_state,
        sections=["cover", "holdings"],
        title="Test Report",
    )
    assert result["available"] is True
    assert result["report_id"] > 0
    assert result["file_size_bytes"] > 0


def test_generate_report_persists_to_db(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    generate_report(profile_name="test_user", state=minimal_state, title="R1")
    generate_report(profile_name="test_user", state=minimal_state, title="R2")

    reports = list_reports("test_user", limit=10)
    assert len(reports) == 2
    titles = {r["title"] for r in reports}
    assert "R1" in titles
    assert "R2" in titles


def test_list_reports_empty(isolated_db):
    assert list_reports("nobody") == []


def test_list_reports_respects_limit(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    for i in range(5):
        generate_report(profile_name="test_user", state=minimal_state, title=f"R{i}")

    reports = list_reports("test_user", limit=3)
    assert len(reports) == 3


def test_get_report_filepath(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    result = generate_report(profile_name="test_user", state=minimal_state, title="FP Test")
    filepath = get_report_filepath(result["report_id"])
    assert filepath is not None
    assert filepath.exists()
    assert filepath.suffix == ".pdf"


def test_get_report_filepath_missing_id(isolated_db):
    assert get_report_filepath(99999) is None


def test_delete_report(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    result = generate_report(profile_name="test_user", state=minimal_state, title="To Delete")
    report_id = result["report_id"]

    deleted = delete_report(report_id, "test_user")
    assert deleted is True

    # Should no longer appear in list
    reports = list_reports("test_user")
    assert all(r["report_id"] != report_id for r in reports)


def test_delete_wrong_profile(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    result = generate_report(profile_name="alice", state=minimal_state, title="Alice Report")
    deleted = delete_report(result["report_id"], "bob")
    assert deleted is False


def test_default_sections_used_when_none(isolated_db, minimal_state, monkeypatch, tmp_path):
    import app.reports.service as svc
    monkeypatch.setattr(svc, "_REPORTS_DIR", tmp_path / "reports")

    result = generate_report(profile_name="test_user", state=minimal_state)
    assert result["available"] is True
    assert "cover" in result["sections"]
