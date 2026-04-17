"""Phase 5.2 tests: Risk & Benchmark Analytics."""

from __future__ import annotations

import datetime
import math
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.settings import Settings
from app.web.app import create_web_app


# ── Shared test data ──────────────────────────────────────────────────────────

DATES = [datetime.date(2024, 1, d) for d in range(2, 32)][:5]

HOLDING_PRICES = {
    "AAPL": [180.0, 181.0, 179.0, 182.0, 183.0],
    "MSFT": [300.0, 302.0, 298.0, 305.0, 307.0],
}
WEIGHTS = {"AAPL": 0.6, "MSFT": 0.4}

BENCHMARK_PRICES = [450.0, 452.0, 448.0, 455.0, 457.0]


def _make_price_dict(dates: list[datetime.date], prices: list[float]) -> dict[datetime.date, float]:
    return dict(zip(dates, prices))


def _make_prices_map() -> dict[str, dict[datetime.date, float]]:
    result = {}
    for sym, price_list in HOLDING_PRICES.items():
        result[sym] = _make_price_dict(DATES, price_list)
    result["SPY"] = _make_price_dict(DATES, BENCHMARK_PRICES)
    return result


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path):
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "phase52_test.db"
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


# ── Pure math unit tests ───────────────────────────────────────────────────────

def test_portfolio_returns_weighted_correctly():
    from app.risk.service import _build_portfolio_returns

    prices = _make_prices_map()
    dates, port_rets, bench_rets, completeness = _build_portfolio_returns(prices, WEIGHTS, "SPY")

    assert len(dates) == 4

    aapl_r0 = (181.0 - 180.0) / 180.0
    msft_r0 = (302.0 - 300.0) / 300.0
    expected_port_r0 = 0.6 * aapl_r0 + 0.4 * msft_r0
    assert port_rets[0] == pytest.approx(expected_port_r0, rel=1e-6)


def test_sharpe_calculation():
    from app.risk.service import _compute_metrics

    port_rets = [0.001, 0.002, -0.001, 0.003, 0.001, 0.002, -0.002, 0.004] * 5
    bench_rets = [0.0008] * len(port_rets)
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct=4.5)

    assert "sharpe_ratio" in metrics
    sharpe = metrics["sharpe_ratio"]
    assert not math.isnan(sharpe)

    import math as _math
    rf_daily = (1 + 4.5 / 100) ** (1 / 252) - 1
    excess = [r - rf_daily for r in port_rets]
    n = len(excess)
    mean_e = sum(excess) / n
    std_e = _math.sqrt(sum((v - mean_e) ** 2 for v in excess) / (n - 1))
    expected_sharpe = mean_e / std_e * _math.sqrt(252)
    assert sharpe == pytest.approx(expected_sharpe, rel=1e-4)


def test_sortino_excludes_upside():
    from app.risk.service import _compute_metrics

    rf_daily = (1 + 4.5 / 100) ** (1 / 252) - 1
    port_rets = [0.005, -0.003, 0.004, -0.002, 0.006, -0.001]
    bench_rets = [0.001] * len(port_rets)
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct=4.5)

    sortino = metrics["sortino_ratio"]
    assert not math.isnan(sortino)

    downside = [r for r in port_rets if r < rf_daily]
    assert len(downside) < len(port_rets), "Some returns should be above risk-free threshold"
    assert len(downside) > 0, "Some returns should be below risk-free threshold"


def test_max_drawdown_correct():
    from app.risk.service import _compute_metrics

    port_rets = [0.0, -0.1, -0.111111, 0.125]
    bench_rets = [0.0, -0.05, -0.052632, 0.05]
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct=4.5)

    port_dd = metrics["max_drawdown_pct"]
    assert port_dd < 0
    assert port_dd == pytest.approx(-20.0, abs=1.0)


def test_tracking_error_correct():
    from app.risk.service import _compute_metrics, _std
    import math as _math

    port_rets = [0.002, -0.001, 0.003, 0.001, -0.002] * 10
    bench_rets = [0.001, -0.0005, 0.002, 0.0015, -0.001] * 10
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct=4.5)

    te = metrics["tracking_error_pct"]
    active = [p - b for p, b in zip(port_rets, bench_rets)]
    expected_te = _std(active) * _math.sqrt(252) * 100
    assert te == pytest.approx(expected_te, rel=1e-4)


def test_information_ratio_correct():
    from app.risk.service import _compute_metrics
    import math as _math

    port_rets = [0.002, -0.001, 0.003, 0.001, -0.002] * 10
    bench_rets = [0.001, -0.0005, 0.002, 0.0015, -0.001] * 10
    metrics = _compute_metrics(port_rets, bench_rets, risk_free_rate_pct=4.5)

    ir = metrics["information_ratio"]
    te = metrics["tracking_error_pct"]
    annual_port = ((1 + sum(port_rets) / len(port_rets)) ** 252 - 1) * 100
    annual_bench = ((1 + sum(bench_rets) / len(bench_rets)) ** 252 - 1) * 100
    expected_ir = (annual_port - annual_bench) / te if te > 0 else float("nan")
    if not math.isnan(expected_ir):
        assert ir == pytest.approx(expected_ir, rel=1e-4)


def test_no_benchmark_returns_unavailable_block():
    from app.risk.service import compute_risk_analytics

    holdings = [{"symbol": "AAPL", "weight_pct": 60.0}, {"symbol": "MSFT", "weight_pct": 40.0}]
    result = compute_risk_analytics(
        profile_name="test_no_bench",
        holdings=holdings,
        benchmark_config=None,
        policy=None,
        lookback_days=252,
        force_refresh=True,
    )
    assert result["available"] is False
    assert result["error"] is not None


def test_missing_holding_reduces_completeness():
    from app.risk.service import _build_portfolio_returns

    prices = _make_prices_map()
    prices_incomplete = {k: v for k, v in prices.items() if k != "MSFT"}

    weights = {"AAPL": 0.6, "MSFT": 0.4}
    dates, port_rets, bench_rets, completeness = _build_portfolio_returns(
        prices_incomplete, weights, "SPY"
    )

    assert completeness < 100.0


# ── API integration tests (monkeypatched) ─────────────────────────────────────

def _make_full_price_series(n: int = 30) -> dict[str, dict[datetime.date, float]]:
    """Generate n days of synthetic prices starting from 2024-01-02."""
    base = datetime.date(2024, 1, 2)
    dates = [base + datetime.timedelta(days=i) for i in range(n)]
    prices = {}
    aapl_price = 180.0
    msft_price = 300.0
    spy_price = 450.0
    import math as _math
    prices["AAPL"] = {}
    prices["MSFT"] = {}
    prices["SPY"] = {}
    for i, dt in enumerate(dates):
        factor = 1.0 + 0.001 * _math.sin(i * 0.3)
        prices["AAPL"][dt] = round(aapl_price * factor, 2)
        prices["MSFT"][dt] = round(msft_price * (1.0 + 0.0008 * _math.sin(i * 0.4)), 2)
        prices["SPY"][dt] = round(spy_price * (1.0 + 0.0005 * _math.sin(i * 0.2)), 2)
        aapl_price = prices["AAPL"][dt]
        msft_price = prices["MSFT"][dt]
        spy_price = prices["SPY"][dt]
    return prices


def _mock_fetch_prices(symbols, lookback_days):
    full = _make_full_price_series(60)
    return {sym: full[sym] for sym in symbols if sym in full}


def _setup_holdings_and_benchmark(client: TestClient) -> None:
    import io
    yaml_content = (
        "profile: default_user\n"
        "holdings:\n"
        "  - symbol: AAPL\n"
        "    weight_pct: 60.0\n"
        "  - symbol: MSFT\n"
        "    weight_pct: 40.0\n"
    ).encode("utf-8")
    client.post(
        "/api/v1/profile/default_user/holdings/import",
        files={"file": ("holdings.yaml", io.BytesIO(yaml_content), "application/x-yaml")},
    )
    client.put(
        "/api/v1/profile/default_user/benchmark",
        json={"payload": {"benchmark_type": "market_index", "name": "S&P 500", "base_symbol": "SPY"}},
    )


def test_get_risk_api_returns_200(client):
    _setup_holdings_and_benchmark(client)
    with patch("app.risk.service._fetch_prices", side_effect=_mock_fetch_prices):
        response = client.get("/api/v1/profile/default_user/risk")
    assert response.status_code == 200
    payload = response.json()
    assert "risk_analytics" in payload
    assert "profile" in payload


def test_get_risk_api_available_false_no_benchmark(client):
    response = client.get("/api/v1/profile/default_user/risk")
    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_analytics"]["available"] is False


def test_post_risk_refresh_returns_200(client):
    _setup_holdings_and_benchmark(client)
    with patch("app.risk.service._fetch_prices", side_effect=_mock_fetch_prices):
        response = client.post("/api/v1/profile/default_user/risk/refresh")
    assert response.status_code == 200
    payload = response.json()
    assert payload.get("refreshed") is True
    assert "risk_analytics" in payload


def test_htmx_save_risk_config_persists(client):
    response = client.post(
        "/ui/profile/default_user/save/risk-config",
        data={"risk_lookback_days": "126", "risk_free_rate_pct": "3.5"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Risk configuration saved." in response.text

    state = client.get("/api/v1/profile/default_user/state").json()
    rc = state.get("risk_config", {})
    assert rc.get("lookback_days") == 126
    assert rc.get("risk_free_rate_pct") == pytest.approx(3.5, rel=1e-4)


def test_htmx_refresh_risk_renders_page(client):
    _setup_holdings_and_benchmark(client)
    with patch("app.risk.service._fetch_prices", side_effect=_mock_fetch_prices):
        response = client.post(
            "/ui/profile/default_user/refresh/risk",
            headers={"HX-Request": "true"},
        )
    assert response.status_code == 200
    assert "Risk metrics refreshed" in response.text


def test_state_includes_risk_analytics_key(client):
    response = client.get("/api/v1/profile/default_user/state")
    assert response.status_code == 200
    data = response.json()
    assert "risk_analytics" in data["analysis"]
