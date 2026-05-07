from __future__ import annotations

from fastapi.testclient import TestClient

from app.web.app import create_web_app


def test_watchlist_ui_route_renders_controls(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/charts/watchlist?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Watchlist Chart Explorer" in html
    assert "Period" in html
    assert "Mode" in html
    assert "Benchmark" in html
    assert "symbols" in html.lower()
    assert "plotly" in html.lower()


def test_watchlist_api_returns_schema(monkeypatch, validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    def _stub_build(**kwargs):
        return {
            "profile": "default_user",
            "controls": {"period": "1M", "mode": "rebased", "benchmark": "SPY", "symbols": ["NVDA"]},
            "data_basis": {"label": "mixed", "line": "Data basis: mixed"},
            "series": [{"symbol": "NVDA", "available": True, "points": [{"date": "2026-05-01", "value": 100.0}]}],
            "summary": {"best_performer": {"symbol": "NVDA", "return_pct": 1.0}, "worst_performer": {"symbol": "NVDA", "return_pct": 1.0}, "dispersion": 0.0, "count_beating_benchmark": 1},
            "warnings": [],
        }

    monkeypatch.setattr("app.web.app.build_watchlist_chart_spec", _stub_build)
    response = client.get(
        "/api/v1/profile/default_user/briefing/charts/watchlist"
        "?period=1M&mode=rebased&benchmark=SPY&symbols=NVDA,MSFT&include_events=false"
    )
    assert response.status_code == 200
    payload = response.json()
    assert "data_basis" in payload
    assert "series" in payload
    assert "summary" in payload
    assert "warnings" in payload

