"""Phase 6.4 deterministic morning visuals engine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.chart_renderer import ChartRenderer
from app.briefing.morning_charts import build_morning_chart_bundle, selected_chart_specs
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import MacroDataPoint, PricePoint, QuoteData, SectorSnapshot
from app.schemas.portfolio import PortfolioHolding
from app.settings import Settings
from app.web.app import create_web_app


class _StubMarketData:
    def __init__(self, *, with_history: bool = True):
        self.with_history = with_history

    def get_price_history(self, symbol: str, period: str = "3mo", interval: str = "1d"):
        if not self.with_history:
            return []
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        return [
            PricePoint(symbol=symbol, timestamp=start + timedelta(days=i), close=100.0 + i)
            for i in range(30)
        ]

    def get_quotes(self, symbols: list[str]):
        return [
            QuoteData(symbol=symbol, display_name=symbol, current_price=100.0, change=1.0, change_percent=1.0)
            for symbol in symbols
        ]


def _sample_profile() -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        portfolio_sector_weights={"technology": 0.45, "healthcare": 0.25},
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=30.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="MSFT", weight_pct=20.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="AAPL", weight_pct=15.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="AMZN", weight_pct=10.0, bucket="satellite"),
            PortfolioHolding(profile_name="default_user", symbol="GOOGL", weight_pct=8.0, bucket="satellite"),
        ],
    )


def _sample_briefing() -> MorningBriefing:
    return MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7100, change=85, change_percent=1.2),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=24400, change=360, change_percent=1.5),
                QuoteData(symbol="^STOXX50E", display_name="EURO STOXX 50", current_price=6050, change=-40, change_percent=-0.65),
                QuoteData(symbol="^N225", display_name="Nikkei 225", current_price=41000, change=120, change_percent=0.3),
            ],
            macro_quotes=[
                QuoteData(symbol="^TNX", display_name="10Y US Treasury Yield", current_price=4.32, change=0.03, change_percent=0.7),
                QuoteData(symbol="CL=F", display_name="WTI Crude Oil (CL1:COM)", current_price=82.6, change=2.4, change_percent=3.0),
                QuoteData(symbol="GC=F", display_name="Gold (GC1:COM)", current_price=2400, change=-8.1, change_percent=-0.34),
            ],
        ),
        macro_context=[
            MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.32, change=0.03),
            MacroDataPoint(series_id="DGS2", name="US 2Y Treasury Yield", value=3.78, change=0.01),
            MacroDataPoint(series_id="T10Y2Y", name="10Y-2Y Yield Spread", value=0.54, change=0.01),
        ],
        portfolio_quotes=[
            QuoteData(symbol="NVDA", display_name="Nvidia", current_price=905, change=20.0, change_percent=2.3),
            QuoteData(symbol="MSFT", display_name="Microsoft", current_price=418, change=2.0, change_percent=0.5),
        ],
        earnings_relevance={
            "today": "2",
            "tomorrow": "3",
            "this_week": "7",
            "portfolio_overlap": "2",
            "watchlist_overlap": "4",
        },
        sector_scan=[
            SectorSnapshot(
                sector_key="technology",
                display_name="Technology",
                etf_symbol="XLK",
                etf_quote=QuoteData(symbol="XLK", display_name="Technology", current_price=220, change=2.0, change_percent=0.9),
            ),
        ],
    )


def test_morning_chart_bundle_contract_has_required_fields():
    bundle, selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    assert "regime_tags" in bundle
    assert bundle["charts"]
    for chart in bundle["charts"]:
        assert "chart_key" in chart
        assert "variant" in chart
        assert "available" in chart
        assert "priority" in chart
        assert "reason_if_hidden" in chart
        assert "series" in chart
        assert "annotations" in chart
        assert "meta" in chart
        assert "email_dimensions" in chart
    assert selected
    assert any(row["role"] == "hero" for row in selected)
    assert any(row["role"].startswith("micro_") for row in selected)
    chart_keys = {row["chart_key"] for row in bundle["charts"]}
    assert "breadth_leadership_panel" in chart_keys
    assert "rates_curve_micro_panel" in chart_keys
    assert "volatility_regime_card" in chart_keys
    assert "portfolio_concentration_risk_card" in chart_keys
    assert "earnings_relevance_strip" in chart_keys


def test_morning_chart_bundle_unavailable_when_history_missing():
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=False),
    )
    chart_map = {row["chart_key"]: row for row in bundle["charts"]}
    assert chart_map["global_relative_performance"]["available"] is False
    assert chart_map["global_relative_performance"]["reason_if_hidden"]
    assert chart_map["volatility_regime_card"]["available"] is False
    assert chart_map["volatility_regime_card"]["reason_if_hidden"]


def test_chart_builder_populates_bundle_and_assets():
    profile = _sample_profile()
    briefing = _sample_briefing()
    charts = MorningChartBuilder(profile=profile, market_data=_StubMarketData(with_history=True)).build(briefing)
    assert charts
    assert briefing.morning_chart_bundle
    assert briefing.morning_chart_selection


def test_chart_renderer_renders_available_morning_specs():
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    renderer = ChartRenderer()
    assets = [renderer.render_from_spec(spec) for spec in selected_chart_specs(bundle) if spec.get("available")]

    assert assets
    assert all(asset is not None for asset in assets)
    assert all(asset.content.startswith(b"\x89PNG") for asset in assets if asset is not None)
    assert any(asset.key == "global_relative_performance" for asset in assets if asset is not None)
    assert any(asset.key == "cross_asset_impulse_strip" for asset in assets if asset is not None)


def test_briefing_morning_charts_route_renders_preview(monkeypatch, validation_test_settings):
    app = create_web_app(validation_test_settings)
    sample_bundle = {
        "generated_at": "2026-04-30T08:30:00Z",
        "regime_tags": ["risk_on", "rates_led"],
        "summary": "Regime tags: risk_on, rates_led",
        "charts": [
            {
                "chart_key": "global_relative_performance",
                "variant": "5d_rebased",
                "available": True,
                "priority": 0.92,
                "reason_if_hidden": None,
                "title": "Global Equity Leadership",
                "caption": "test",
                "series": [{"name": "S&P 500", "x_5d": [0, 1], "y_5d": [100, 101]}],
                "annotations": [],
                "meta": {},
                "email_dimensions": {"width": 8.6, "height": 4.8},
            }
        ],
        "selected": [{"chart_key": "global_relative_performance", "role": "hero", "reason": "test"}],
    }

    monkeypatch.setattr(
        "app.web.app._build_morning_chart_preview",
        lambda settings, profile: {"profile": profile, "bundle": sample_bundle, "selection": sample_bundle["selected"], "assets": []},
    )

    client = TestClient(app)
    response = client.get("/ui/briefing/morning/charts?profile=default_user")
    assert response.status_code == 200
    assert "Briefing / Morning Charts" in response.text
    assert "morning-global-relative-chart" in response.text
    assert "morning-breadth-chart" in response.text
    assert "morning-rates-micro-chart" in response.text
    assert "morning-volatility-chart" in response.text
    assert "morning-concentration-chart" in response.text
    assert "morning-earnings-chart" in response.text
    assert "morning-chart-data" in response.text


def test_briefing_morning_charts_api_returns_bundle(monkeypatch, validation_test_settings):
    app = create_web_app(validation_test_settings)
    monkeypatch.setattr(
        "app.web.app._build_morning_chart_preview",
        lambda settings, profile: {"profile": profile, "bundle": {"charts": [], "selected": [], "regime_tags": []}},
    )
    client = TestClient(app)
    response = client.get("/api/v1/profile/default_user/briefing/morning/charts")
    assert response.status_code == 200
    payload = response.json()
    assert "bundle" in payload
    assert "profile" in payload
