"""Tests for quantstats tearsheet generation and UI route."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.settings import Settings
from app.web.app import create_web_app


def test_build_tearsheet_html_returns_none_without_series(monkeypatch):
    from app.risk import tearsheet

    monkeypatch.setattr(tearsheet, "_build_return_series", lambda *_args, **_kwargs: None)
    out = tearsheet.build_tearsheet_html("default_user", lookback_days=252)
    assert out is None


def test_build_tearsheet_html_uses_quantstats(monkeypatch):
    from app.risk import tearsheet

    mock_series = {
        "benchmark_symbol": "SPY",
        "dates": ["2026-01-02", "2026-01-03", "2026-01-04"],
        "portfolio_returns": [0.01, -0.005, 0.004],
        "benchmark_returns": [0.008, -0.004, 0.003],
    }
    monkeypatch.setattr(tearsheet, "_build_return_series", lambda *_args, **_kwargs: mock_series)

    class _MockReports:
        @staticmethod
        def html(*_args, **_kwargs):
            return "<html><body>OK</body></html>"

    class _MockQs:
        reports = _MockReports()

    class _MockPd:
        class Series:
            def __init__(self, *_args, **_kwargs):
                pass

        @staticmethod
        def to_datetime(values):
            return values

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "quantstats":
            return _MockQs
        if name == "pandas":
            return _MockPd
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = tearsheet.build_tearsheet_html("default_user", lookback_days=252)
    assert out is not None
    assert "OK" in out


def test_ui_tearsheet_route_returns_404_when_unavailable(monkeypatch):
    app = create_web_app(Settings(dry_run=True))
    client = TestClient(app)

    monkeypatch.setattr("app.web.app.build_tearsheet_html", lambda *_args, **_kwargs: None)
    response = client.get("/ui/tearsheet?profile=default_user")
    assert response.status_code == 404
    assert "Tearsheet unavailable" in response.text


def test_ui_tearsheet_route_returns_html_when_available(monkeypatch):
    app = create_web_app(Settings(dry_run=True))
    client = TestClient(app)

    monkeypatch.setattr("app.web.app.build_tearsheet_html", lambda *_args, **_kwargs: "<html>TS</html>")
    response = client.get("/ui/tearsheet?profile=default_user")
    assert response.status_code == 200
    assert "TS" in response.text
