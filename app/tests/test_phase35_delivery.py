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
from app.schemas.events import NormalisedEvent, PricePoint, QuoteData
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


def test_morning_chart_builder_builds_multiple_assets():
    class StubMarketData:
        def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d"):
            return [
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc), close=100.0),
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 2, tzinfo=timezone.utc), close=104.0),
                PricePoint(symbol=symbol, timestamp=datetime(2026, 4, 3, tzinfo=timezone.utc), close=106.0),
            ]

    profile = UserProfile(
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core"),
        ]
    )
    briefing = MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, change_percent=0.8),
                QuoteData(symbol="QQQ", display_name="Nasdaq 100", current_price=420.0, change_percent=-0.4),
            ]
        ),
        portfolio_quotes=[
            QuoteData(symbol="NVDA", display_name="Nvidia", current_price=101.0, change_percent=2.5),
            QuoteData(symbol="MSFT", display_name="Microsoft", current_price=99.0, change_percent=-0.6),
        ],
        portfolio_focus=[
            NormalisedEvent(title="Nvidia wins major order", tickers=["NVDA"], summary="Demand signal.")
        ],
    )

    charts = MorningChartBuilder(profile=profile, market_data=StubMarketData()).build(briefing)
    assert len(charts) >= 2
    assert {chart.key for chart in charts}.issuperset({"market_snapshot", "portfolio_movers"})


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
