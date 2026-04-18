"""Shared test fixtures for validation harness tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings


@pytest.fixture
def validation_isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "validation_test.db"
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
def validation_test_settings(tmp_path, validation_isolated_db):
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
                "  europe: 0.8",
                "  asia: 0.5",
                "sector_weights:",
                "  technology: 1.0",
                "  semiconductors: 0.9",
                "  healthcare: 0.8",
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
        "primary: [AAPL, MSFT, NVDA]\nsecondary: [ASML]\nmonitor: [GLD]\n"
    )
    (config_dir / "sectors.yaml").write_text(
        "\n".join(
            [
                "sectors:",
                "  technology:",
                "    etf: XLK",
                "    display_name: Technology",
                "    key_names: [AAPL, MSFT, NVDA, GOOGL, META]",
                "  semiconductors:",
                "    etf: SMH",
                "    display_name: Semiconductors",
                "    key_names: [NVDA, AMD, ASML]",
                "  healthcare:",
                "    etf: XLV",
                "    display_name: Healthcare",
                "    key_names: [LLY, NVO, JNJ]",
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
def stubbed_validation_risk_service(monkeypatch):
    from app.risk import service as risk_service

    def _stubbed_compute_risk_analytics(*args, **kwargs):
        return {
            "available": False,
            "error": "stubbed risk analytics for validation tests",
            "risk_summary": "stubbed",
            "lookback_label": "1 year (stubbed)",
            "benchmark_name": "ACWI",
            "data_completeness_pct": 100,
            "total_return_pct": 0.0,
            "benchmark_return_pct": 0.0,
            "active_return_pct": 0.0,
            "volatility_pct": 0.0,
            "benchmark_volatility_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "benchmark_max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "tracking_error_pct": 0.0,
            "information_ratio": 0.0,
            "sharpe_label": "moderate",
            "ir_label": "neutral",
            "rolling": {"30d": []},
            "total_return_display": "0.0%",
            "benchmark_return_display": "0.0%",
            "active_return_display": "+0.0%",
            "volatility_display": "0.0%",
            "benchmark_volatility_display": "0.0%",
            "max_drawdown_display": "0.0%",
            "benchmark_max_drawdown_display": "0.0%",
            "sharpe_display": "0.00",
            "sortino_display": "0.00",
            "tracking_error_display": "0.0%",
            "information_ratio_display": "0.00",
            "risk_free_rate_pct": 4.5,
        }

    monkeypatch.setattr(risk_service, "compute_risk_analytics", _stubbed_compute_risk_analytics)
    yield
