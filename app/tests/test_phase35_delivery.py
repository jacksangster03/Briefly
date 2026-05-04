"""Phase 3.5 tests: chart rendering and rich morning delivery."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.chart_renderer import ChartRenderer
from app.briefing.email_formatter import EmailFormatter
from app.messaging.email import EmailMessenger
from app.messaging.telegram import TelegramMessenger
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.delivery import ChartAsset
from app.schemas.events import NormalisedEvent, PricePoint, QuoteData, SectorSnapshot
from app.schemas.portfolio import PortfolioHolding
from app.settings import Settings


def _sample_chart_asset() -> ChartAsset:
    return ChartAsset(
        key="market_snapshot",
        title="Market Snapshot",
        caption="Major index moves.",
        filename="market-snapshot.png",
        content_type="image/png",
        content_id="market-snapshot-cid",
        content=b"\x89PNG\r\n\x1a\nfake",
    )


def test_chart_renderer_returns_png_asset():
    renderer = ChartRenderer()
    asset = renderer.render_market_snapshot(
        [
            QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, change_percent=0.8),
            QuoteData(symbol="QQQ", display_name="Nasdaq 100", current_price=420.0, change_percent=-0.4),
        ]
    )
    assert asset is not None
    assert asset.content.startswith(b"\x89PNG")
    assert asset.filename.endswith(".png")
    assert asset.content_id


def test_chart_renderer_macro_and_sector_assets():
    renderer = ChartRenderer()

    macro_asset = renderer.render_macro_risk_strip(
        [
            QuoteData(symbol="TLT", display_name="20Y+ Treasury", current_price=86.0, change_percent=-0.8),
            QuoteData(symbol="GLD", display_name="Gold", current_price=240.0, change_percent=0.2),
            QuoteData(symbol="USO", display_name="WTI Crude", current_price=81.0, change_percent=1.4),
        ]
    )
    assert macro_asset is not None
    assert macro_asset.content.startswith(b"\x89PNG")

    sector_asset = renderer.render_sector_exposure_performance(
        [
            ("Technology", 0.52, 1.1),
            ("Financials", 0.21, -0.4),
            ("Healthcare", 0.14, 0.2),
        ]
    )
    assert sector_asset is not None
    assert sector_asset.content.startswith(b"\x89PNG")


def test_morning_chart_builder_builds_multiple_assets():
    class StubMarketData:
        def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d"):
            return [
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc), close=100.0),
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 2, tzinfo=timezone.utc), close=104.0),
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 3, tzinfo=timezone.utc), close=106.0),
            ]

    profile = UserProfile(
        delivery={"email_density_mode": "full"},
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core"),
        ],
        portfolio_sector_weights={"technology": 0.7},
    )
    briefing = MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, change_percent=0.8),
                QuoteData(symbol="QQQ", display_name="Nasdaq 100", current_price=420.0, change_percent=-0.4),
            ],
            macro_quotes=[
                QuoteData(symbol="TLT", display_name="20Y+ Treasury", current_price=86.0, change_percent=-0.8),
                QuoteData(symbol="GLD", display_name="Gold", current_price=240.0, change_percent=0.2),
                QuoteData(symbol="USO", display_name="WTI Crude", current_price=81.0, change_percent=1.4),
            ],
        ),
        portfolio_quotes=[
            QuoteData(symbol="NVDA", display_name="Nvidia", current_price=101.0, change_percent=2.5),
            QuoteData(symbol="MSFT", display_name="Microsoft", current_price=99.0, change_percent=-0.6),
        ],
        portfolio_focus=[
            NormalisedEvent(title="Nvidia wins major order", tickers=["NVDA"], summary="Demand signal.")
        ],
        sector_scan=[
            SectorSnapshot(
                sector_key="technology",
                display_name="Technology",
                etf_symbol="XLK",
                etf_quote=QuoteData(
                    symbol="XLK",
                    display_name="Technology",
                    current_price=222.0,
                    change_percent=1.1,
                ),
            )
        ],
    )

    charts = MorningChartBuilder(profile=profile, market_data=StubMarketData()).build(briefing)
    assert len(charts) >= 3
    chart_keys = {chart.key for chart in charts}
    assert "global_relative_performance" in chart_keys
    assert "cross_asset_impulse_strip" in chart_keys
    assert "holdings_excess_performance" in chart_keys
    assert briefing.morning_chart_bundle
    assert briefing.morning_chart_selection


def test_email_formatter_embeds_inline_chart_cids():
    formatter = EmailFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 12, 8, 45),
        session_mode="sunday",
        chart_assets=[_sample_chart_asset()],
        portfolio_focus=[
            NormalisedEvent(
                title="Nvidia supplier expands capacity",
                summary="Capacity supports AI server demand.",
                tickers=["NVDA"],
                cluster_size=3,
            )
        ],
    )

    rendered = formatter.format_morning_briefing(briefing)
    assert "Weekend Briefing" in rendered.subject
    assert "cid:market-snapshot-cid" in rendered.html_body
    assert "PORTFOLIO FOCUS" in rendered.plain_text


def test_email_formatter_uses_continuous_finance_canvas():
    formatter = EmailFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 12, 8, 45, tzinfo=timezone.utc),
        session_mode="sunday",
        chart_assets=[_sample_chart_asset()],
        morning_chart_selection=[{"chart_key": "market_snapshot", "role": "hero", "reason": "test"}],
        morning_chart_bundle={
            "regime_tags": ["risk_on", "rates_led"],
            "meta": {
                "profile_name": "default_user",
                "delivery_mode": "deterministic",
                "llm_shadow_mode": True,
                "data_confidence": "high",
            },
        },
        market_setup_analysis="Market tone is mixed with no single dominant impulse.",
    )

    rendered = formatter.format_morning_briefing(briefing)
    assert "background:#030A12" in rendered.html_body
    assert "background:#06111F" in rendered.html_body
    assert "font-size:20px" in rendered.html_body
    assert "#FF7A00" in rendered.html_body
    assert "SOURCE" in rendered.html_body
    assert "REGIME" in rendered.html_body
    assert "READ" in rendered.html_body
    assert "WHY IT MATTERS" in rendered.html_body
    assert "PORTFOLIO LENS" in rendered.html_body
    assert "TODAY'S TRIGGERS" in rendered.html_body
    assert "WHAT CHANGED" in rendered.html_body
    assert "Desk read:" in rendered.html_body
    assert "border-radius" not in rendered.html_body
    assert rendered.html_body.index("READ") < rendered.html_body.index("<img src=\"cid:market-snapshot-cid\"")
    assert '<strong style="color:#FF7A00;font-weight:800;">Setup read:</strong>' in rendered.html_body
    for stale in ("#44546A", "#46566A", "#4A586B", "#536176"):
        assert stale not in rendered.html_body


def test_email_formatter_desktop_and_narrow_width_snapshots():
    desktop = EmailFormatter("Europe/Madrid", content_width=680)
    narrow = EmailFormatter("Europe/Madrid", content_width=560)
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 12, 8, 45, tzinfo=timezone.utc),
        session_mode="sunday",
        chart_assets=[_sample_chart_asset()],
        morning_chart_selection=[{"chart_key": "market_snapshot", "role": "hero", "reason": "test"}],
        morning_chart_bundle={"regime_tags": ["mixed"], "meta": {"data_confidence": "high"}},
    )
    desktop_html = desktop.format_morning_briefing(briefing).html_body
    narrow_html = narrow.format_morning_briefing(briefing).html_body
    assert 'width="680"' in desktop_html
    assert "max-width:680px" in desktop_html
    assert 'width="560"' in narrow_html
    assert "max-width:560px" in narrow_html
    assert "max-width:640px;height:auto" in desktop_html
    assert "max-width:640px;height:auto" in narrow_html


def test_email_formatter_includes_quote_freshness_metadata():
    formatter = EmailFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 12, 8, 45, tzinfo=timezone.utc),
        session_mode="sunday",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(
                    symbol="SPY",
                    display_name="S&P 500",
                    current_price=500.0,
                    change_percent=0.8,
                    source="yfinance",
                    timestamp=datetime(2026, 4, 12, 8, 40, tzinfo=timezone.utc),
                ),
            ]
        ),
        watchlist_quotes=[
            QuoteData(
                symbol="NVDA",
                display_name="Nvidia",
                current_price=101.0,
                change_percent=2.5,
                source="finnhub",
                timestamp=datetime(2026, 4, 12, 8, 39, tzinfo=timezone.utc),
            ),
            QuoteData(
                symbol="MSFT",
                display_name="Microsoft",
                current_price=99.0,
                change_percent=-0.6,
                source="yfinance",
                timestamp=datetime(2026, 4, 12, 8, 38, tzinfo=timezone.utc),
            ),
        ],
    )

    rendered = formatter.format_morning_briefing(briefing)
    assert "Quotes as of" in rendered.html_body
    assert "sources: finnhub(1), yfinance(1)" in rendered.html_body


@patch("app.messaging.email.smtplib.SMTP")
def test_email_messenger_send_rich_attaches_inline_images(mock_smtp):
    settings = Settings(
        email_user="from@example.com",
        email_password="secret",
        email_to="to@example.com",
        dry_run=False,
    )
    messenger = EmailMessenger(settings)
    smtp = mock_smtp.return_value.__enter__.return_value

    result = messenger.send_rich(
        subject="Morning Briefing",
        plain_text="plain body",
        html_body="<html><body><img src=\"cid:market-snapshot-cid\"></body></html>",
        inline_assets=[_sample_chart_asset()],
    )

    assert result is True
    smtp.sendmail.assert_called_once()
    raw_message = smtp.sendmail.call_args.args[2]
    assert "Content-ID: <market-snapshot-cid>" in raw_message


@patch("app.messaging.telegram.requests.post")
def test_telegram_messenger_send_photo_uses_send_photo(mock_post):
    mock_post.return_value = MagicMock(ok=True)
    settings = Settings(
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        dry_run=False,
    )
    messenger = TelegramMessenger(settings)

    result = messenger.send_photo(_sample_chart_asset(), caption="Hero chart")

    assert result is True
    url = mock_post.call_args.args[0]
    assert url.endswith("/sendPhoto")
    assert "files" in mock_post.call_args.kwargs
