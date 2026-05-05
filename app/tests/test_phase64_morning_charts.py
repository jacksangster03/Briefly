"""Phase 6.4 deterministic morning visuals engine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
pytest.importorskip("httpx")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient
from PIL import Image

from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.chart_renderer import ChartRenderer
from app.briefing.morning_charts import build_morning_chart_bundle, selected_chart_specs
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.delivery import ChartAsset
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
        delivery={"email_density_mode": "full"},
        portfolio_sector_weights={"technology": 0.45, "healthcare": 0.25},
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=30.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="MSFT", weight_pct=20.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="AAPL", weight_pct=15.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="AMZN", weight_pct=10.0, bucket="satellite"),
            PortfolioHolding(profile_name="default_user", symbol="GOOGL", weight_pct=8.0, bucket="satellite"),
        ],
    )


def _sample_profile_desk() -> UserProfile:
    profile = _sample_profile()
    profile.delivery["email_density_mode"] = "desk"
    return profile


def _sample_briefing() -> MorningBriefing:
    return MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7100, change=85, change_percent=1.2),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=24400, change=360, change_percent=1.5),
                QuoteData(symbol="^STOXX50E", display_name="EURO STOXX 50", current_price=6050, change=-40, change_percent=-0.65),
                QuoteData(symbol="^N225", display_name="Nikkei 225", current_price=41000, change=120, change_percent=0.3),
                QuoteData(symbol="^DJI", display_name="Dow Jones (DJIA)", current_price=49000, change=180, change_percent=0.4),
                QuoteData(symbol="^RUT", display_name="Russell 2000 (RUT)", current_price=2800, change=40, change_percent=1.4),
                QuoteData(symbol="^FTSE", display_name="FTSE 100", current_price=8700, change=-20, change_percent=-0.2),
                QuoteData(symbol="^GDAXI", display_name="DAX", current_price=24000, change=-120, change_percent=-0.5),
                QuoteData(symbol="^FCHI", display_name="CAC 40", current_price=8200, change=20, change_percent=0.25),
                QuoteData(symbol="^HSI", display_name="Hang Seng", current_price=26000, change=-200, change_percent=-0.75),
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
    assert len(selected) >= 6
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


def test_chart_density_mode_desk_selects_three_to_five_cards():
    bundle, selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile_desk(),
        market_data_service=_StubMarketData(with_history=True),
    )
    assert bundle["meta"]["email_density_mode"] == "desk"
    assert 3 <= len(selected) <= 5
    selected_keys = {row["chart_key"] for row in selected}
    assert "pnl_attribution_waterfall" in selected_keys
    assert "portfolio_concentration_risk_card" in selected_keys


def test_chart_stack_energy_geo_prefers_geo_modules():
    briefing = _sample_briefing()
    # Force oil-shock context.
    briefing.market_setup.macro_quotes[1].change_percent = 4.2
    briefing.market_setup.index_quotes.append(
        QuoteData(symbol="^VIX", display_name="VIX", current_price=21.0, change=1.2, change_percent=6.0)
    )
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    assert bundle["meta"]["chart_stack_key"] == "energy_geo"
    selected_keys = {row["chart_key"] for row in selected}
    assert {"geo_confirmation_ladder", "oil_transmission_card"} & selected_keys


def test_global_chart_series_are_limited_for_email_readability():
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    chart_map = {row["chart_key"]: row for row in bundle["charts"]}
    assert len(chart_map["global_relative_performance"]["series"]) <= 7
    assert chart_map["global_relative_performance"]["caption"]


def test_chart_builder_populates_bundle_and_assets():
    profile = _sample_profile()
    briefing = _sample_briefing()
    charts = MorningChartBuilder(profile=profile, market_data=_StubMarketData(with_history=True)).build(briefing)
    assert len(charts) >= 6
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


def test_chart_renderer_global_shows_full_x_axis_labels_and_avoids_clipped_text(monkeypatch):
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    spec = next(item for item in selected_chart_specs(bundle) if item.get("chart_key") == "global_relative_performance")

    captured: dict = {}

    def _capture(self, fig, *, key: str, title: str, caption: str, filename: str):
        fig.canvas.draw()
        ax = fig.axes[0]
        labels = [tick.get_text() for tick in ax.get_xticklabels()]
        captured["labels"] = labels
        captured["texts"] = [text.get_text() for text in ax.texts]
        # Ensure text artists are within figure canvas bounds.
        renderer = fig.canvas.get_renderer()
        fw, fh = fig.canvas.get_width_height()
        for text in ax.texts:
            box = text.get_window_extent(renderer=renderer)
            assert box.x0 >= -2
            assert box.y0 >= -2
            assert box.x1 <= fw + 2
            assert box.y1 <= fh + 2
        buf = BytesIO()
        fig.savefig(buf, format="png")
        return ChartAsset(
            key=key,
            title=title,
            caption=caption,
            filename=filename,
            content_type="image/png",
            content_id="test-cid",
            content=buf.getvalue(),
        )

    monkeypatch.setattr(ChartRenderer, "_to_asset", _capture, raising=False)
    asset = ChartRenderer().render_global_relative_from_spec(spec)
    assert asset is not None
    assert any(label == "Today" for label in captured["labels"])
    assert len([label for label in captured["labels"] if label]) >= 5
    joined = " | ".join(captured["texts"])
    assert "Nasdaq" in joined or "NASDAQ" in joined
    endpoint_labels = [text for text in captured["texts"] if "%" in text and "(" in text]
    assert len(endpoint_labels) >= 2


def test_chart_renderer_breadth_separates_breadth_pct_from_factor_axis(monkeypatch):
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    spec = next(item for item in selected_chart_specs(bundle) if item.get("chart_key") == "breadth_leadership_panel")
    captured: dict = {}

    def _capture(self, fig, *, key: str, title: str, caption: str, filename: str):
        captured["axes_count"] = len(fig.axes)
        captured["xlabel_bottom"] = fig.axes[-1].get_xlabel()
        buf = BytesIO()
        fig.savefig(buf, format="png")
        return ChartAsset(
            key=key,
            title=title,
            caption=caption,
            filename=filename,
            content_type="image/png",
            content_id="test-cid",
            content=buf.getvalue(),
        )

    monkeypatch.setattr(ChartRenderer, "_to_asset", _capture, raising=False)
    asset = ChartRenderer().render_breadth_leadership_from_spec(spec)
    assert asset is not None
    assert captured["axes_count"] == 2
    assert "Leadership factor signal" in captured["xlabel_bottom"]


def test_chart_renderer_cross_asset_uses_normalized_axis_and_no_in_chart_title(monkeypatch):
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    spec = next(item for item in selected_chart_specs(bundle) if item.get("chart_key") == "cross_asset_impulse_strip")
    assert "Units are" not in str(spec.get("caption") or "")
    assert "normalised for comparison" in str(spec.get("caption") or "")
    captured: dict = {}

    def _capture(self, fig, *, key: str, title: str, caption: str, filename: str):
        ax = fig.axes[0]
        captured["xlabel"] = ax.get_xlabel()
        captured["title"] = ax.get_title()
        buf = BytesIO()
        fig.savefig(buf, format="png")
        png = buf.getvalue()
        # Lightweight snapshot sanity check: rendered chart is non-empty and decodable.
        image = Image.open(BytesIO(png))
        assert image.size[0] > 1200
        assert image.size[1] > 700
        return ChartAsset(
            key=key,
            title=title,
            caption=caption,
            filename=filename,
            content_type="image/png",
            content_id="test-cid",
            content=png,
        )

    monkeypatch.setattr(ChartRenderer, "_to_asset", _capture, raising=False)
    asset = ChartRenderer().render_cross_asset_impulse_from_spec(spec)
    assert asset is not None
    assert "Normalized impulse score" in captured["xlabel"]
    assert captured["title"] == ""


def test_chart_renderer_event_annotation_uses_explicit_non_overlapping_summary(monkeypatch):
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    chart_map = {row["chart_key"]: row for row in (bundle.get("charts") or [])}
    spec = chart_map["event_linked_annotated_trend"]
    captured: dict = {}

    def _capture(self, fig, *, key: str, title: str, caption: str, filename: str):
        ax = fig.axes[0]
        captured["texts"] = [text.get_text() for text in ax.texts]
        buf = BytesIO()
        fig.savefig(buf, format="png")
        return ChartAsset(
            key=key,
            title=title,
            caption=caption,
            filename=filename,
            content_type="image/png",
            content_id="test-cid",
            content=buf.getvalue(),
        )

    monkeypatch.setattr(ChartRenderer, "_to_asset", _capture, raising=False)
    asset = ChartRenderer().render_event_linked_from_spec(spec)
    assert asset is not None
    text_block = "\n".join(captured["texts"])
    assert "Event:" in text_block
    assert "Full-period move:" in text_block
    assert "Event: event" not in text_block


def test_oil_transmission_marks_brent_unavailable_when_missing():
    bundle, _selected = build_morning_chart_bundle(
        briefing=_sample_briefing(),
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    chart_map = {row["chart_key"]: row for row in (bundle.get("charts") or [])}
    spec = chart_map["oil_transmission_card"]
    assert "Brent unavailable" in str(spec.get("caption") or "")
    brent_row = next((row for row in spec.get("series", []) if str(row.get("name")) == "Brent"), {})
    assert brent_row.get("value") is None


def test_pnl_caption_all_negative_has_no_positive_offset_phrase():
    briefing = _sample_briefing()
    briefing.portfolio_quotes = [
        QuoteData(symbol="NVDA", display_name="Nvidia", current_price=905, change=-8.0, change_percent=-0.9),
        QuoteData(symbol="MSFT", display_name="Microsoft", current_price=418, change=-3.0, change_percent=-0.7),
    ]
    bundle, _selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=_sample_profile(),
        market_data_service=_StubMarketData(with_history=True),
    )
    chart_map = {row["chart_key"]: row for row in (bundle.get("charts") or [])}
    caption = str((chart_map.get("pnl_attribution_waterfall") or {}).get("caption") or "")
    assert "no displayed sleeve offset the decline" in caption
    assert "(0/2." not in caption


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
