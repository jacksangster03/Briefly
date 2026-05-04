from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from app.briefing.morning_charts import build_morning_chart_bundle
from app.briefing.session_materiality import compute_materiality
from app.briefing.session_routing import resolve_session_window
from app.cli import cli
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import MacroDataPoint, QuoteData
from app.schemas.portfolio import PortfolioHolding


class _StubMarketData:
    def get_price_history(self, symbol: str, period: str = "3mo", interval: str = "1d"):
        return []

    def get_quotes(self, symbols: list[str]):
        return [
            QuoteData(symbol=symbol, display_name=symbol, current_price=100.0, change=1.0, change_percent=1.0)
            for symbol in symbols
        ]


def _sample_profile(*, density: str = "desk") -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        delivery={"email_density_mode": density},
        watchlist_primary=["NVDA", "MSFT", "AAPL", "AMD", "META"],
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="QQQ", weight_pct=40.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="ACWI", weight_pct=35.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="BND", weight_pct=25.0, bucket="hedge"),
        ],
    )


def _sample_briefing(session_key: str = "us_intraday_risk") -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime(2026, 5, 4, 15, 40, tzinfo=timezone.utc),
        session_key=session_key,
        session_title="US Intraday Risk Check",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7200, change=8, change_percent=0.11),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=25000, change=80, change_percent=0.32),
                QuoteData(symbol="^DJI", display_name="Dow Jones (DJIA)", current_price=49400, change=-60, change_percent=-0.12),
                QuoteData(symbol="^RUT", display_name="Russell 2000 (RUT)", current_price=2812, change=5, change_percent=0.18),
                QuoteData(symbol="^STOXX50E", display_name="EURO STOXX 50", current_price=5881, change=-45, change_percent=-0.76),
                QuoteData(symbol="^N225", display_name="Nikkei 225", current_price=59513, change=400, change_percent=0.68),
                QuoteData(symbol="^VIX", display_name="VIX", current_price=17.40, change=0.42, change_percent=2.47),
            ],
            macro_quotes=[
                QuoteData(symbol="CL=F", display_name="WTI Crude Oil (CL1:COM)", current_price=102.8, change=0.5, change_percent=0.49),
                QuoteData(symbol="BZ=F", display_name="Brent Crude", current_price=106.0, change=1.0, change_percent=0.96),
                QuoteData(symbol="GC=F", display_name="Gold (GC1:COM)", current_price=4638.0, change=-40, change_percent=-0.86),
                QuoteData(symbol="^TNX", display_name="10Y US Treasury Yield", current_price=4.40, change=0.04, change_percent=0.92),
            ],
        ),
        macro_context=[
            MacroDataPoint(series_id="DGS2", name="US 2Y Treasury Yield", value=3.88, change=0.01),
            MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.40, change=0.04),
            MacroDataPoint(series_id="DGS30", name="US 30Y Treasury Yield", value=4.98, change=0.02),
            MacroDataPoint(series_id="T10Y2Y", name="10Y-2Y Yield Spread", value=0.51, change=-0.01),
        ],
        watchlist_quotes=[
            QuoteData(symbol="AMD", display_name="AMD", current_price=180, change=-7, change_percent=-3.76),
            QuoteData(symbol="AMZN", display_name="AMZN", current_price=200, change=5, change_percent=2.73),
            QuoteData(symbol="MSFT", display_name="MSFT", current_price=420, change=2, change_percent=0.48),
            QuoteData(symbol="NVDA", display_name="NVDA", current_price=900, change=-2, change_percent=-0.22),
            QuoteData(symbol="AAPL", display_name="AAPL", current_price=220, change=1, change_percent=0.45),
        ],
        portfolio_quotes=[
            QuoteData(symbol="QQQ", display_name="QQQ", current_price=500, change=-1, change_percent=-0.2),
            QuoteData(symbol="ACWI", display_name="ACWI", current_price=110, change=-1.2, change_percent=-1.1),
            QuoteData(symbol="BND", display_name="BND", current_price=72, change=-0.4, change_percent=-0.6),
        ],
        what_changed_lines=[
            "VIX: 17.40 (+0.90)",
            "US 10Y: 4.40% (+0.06%)",
            "WTI: +0.49% (+1.10%)",
        ],
    )


def test_session_window_routing_boundaries():
    tz = "Europe/Madrid"
    assert resolve_session_window(now=datetime(2026, 5, 4, 5, 30, tzinfo=timezone.utc), timezone_name=tz).key == "morning"
    assert resolve_session_window(now=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc), timezone_name=tz).key == "europe_midday"
    assert resolve_session_window(now=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc), timezone_name=tz).key == "us_pre_open"
    assert resolve_session_window(now=datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc), timezone_name=tz).key == "us_intraday_risk"
    assert resolve_session_window(now=datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc), timezone_name=tz).key == "into_close"


def test_materiality_high_score_routes_to_breaking():
    briefing = _sample_briefing()
    briefing.session_quality_score = 0.8
    result = compute_materiality(
        briefing,
        previous={
            "session_quality": -0.2,
            "vix_level": 15.2,
            "us10y": 4.25,
            "wti_pct": -1.8,
            "brent_pct": -1.1,
            "us_avg_pct": -0.7,
            "eu_avg_pct": 0.3,
            "asia_avg_pct": -0.1,
            "portfolio_contrib_pct": 0.4,
        },
    )
    assert result.score >= 8
    assert result.decision == "breaking_alert"


def test_intraday_desk_stack_includes_required_session_cards():
    briefing = _sample_briefing("us_intraday_risk")
    profile = _sample_profile(density="desk")
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    keys = {row["chart_key"] for row in selected}
    assert bundle["meta"]["email_density_mode"] == "desk"
    assert 4 <= len(selected) <= 5
    assert "watchlist_movers_card" in keys
    assert "setup_confirmation_card" in keys
    assert "pnl_attribution_waterfall" in keys


def test_vix_174_is_watchful():
    briefing = _sample_briefing("us_intraday_risk")
    profile = _sample_profile(density="desk")
    bundle, _selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    chart_map = {row["chart_key"]: row for row in bundle["charts"]}
    assert chart_map["volatility_regime_card"]["meta"]["regime"] == "watchful"


def test_cli_morning_force_override_passes_force_flag(monkeypatch):
    called: dict = {}

    def _fake_run(settings, **kwargs):
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "morning", "--force-morning"])
    assert result.exit_code == 0, result.output
    assert called["kwargs"].get("force_morning") is True


def test_cli_brief_dispatches_to_session_brief(monkeypatch):
    called = {"count": 0}

    def _fake_run(settings, **kwargs):
        called["count"] += 1
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_session_brief", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "brief"])
    assert result.exit_code == 0, result.output
    assert called["count"] == 1
