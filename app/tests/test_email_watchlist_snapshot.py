from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.email_formatter import EmailFormatter
from app.personalization.user_profile import UserProfile
from app.personalization.preferences_service import normalize_preference_value
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import PricePoint, QuoteData


class _StubMarketData:
    def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d"):
        series = {
            "NVDA": [100.0, 102.0, 105.0],
            "MSFT": [100.0, 101.0, 103.0],
            "AAPL": [100.0, 99.5, 98.0],
        }.get(symbol, [100.0, 100.0, 100.0])
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        return [
            PricePoint(symbol=symbol, timestamp=base, close=series[0], source="stub"),
            PricePoint(symbol=symbol, timestamp=base.replace(day=2), close=series[1], source="stub"),
            PricePoint(symbol=symbol, timestamp=base.replace(day=3), close=series[2], source="stub"),
        ]

    def get_quotes(self, symbols):
        return [QuoteData(symbol=s, display_name=s, current_price=100.0, change_percent=1.0) for s in symbols]


def _profile(*, watchlist_snapshot_enabled: bool = True) -> UserProfile:
    return UserProfile(
        name="default_user",
        watchlist_primary=["NVDA", "MSFT", "AAPL"],
        morning_section_flags={"watchlist_snapshot": watchlist_snapshot_enabled},
        delivery={"email_density_mode": "full"},
    )


def _briefing(session_key: str) -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime(2026, 5, 7, 8, 30, tzinfo=timezone.utc),
        session_mode="weekday",
        session_key=session_key,
        session_title="Morning Briefing" if session_key == "morning" else "US Intraday Risk Check",
        market_setup=MarketSetup(
            index_quotes=[QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, change_percent=0.8)],
            macro_quotes=[QuoteData(symbol="TLT", display_name="20Y+ Treasury", current_price=87.0, change_percent=-0.4)],
        ),
    )


def test_morning_includes_watchlist_snapshot_and_links():
    profile = _profile(watchlist_snapshot_enabled=True)
    briefing = _briefing("morning")
    briefing.chart_assets = MorningChartBuilder(profile=profile, market_data=_StubMarketData()).build(briefing)
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
    assert "Watchlist Performance Snapshot" in html
    assert "Open 1D" in html
    assert "Open 1M" in html
    assert "Open 1Y" in html
    assert "Open 5Y" in html
    assert "Full explorer" in html


def test_non_morning_excludes_full_snapshot_module_by_default():
    profile = _profile(watchlist_snapshot_enabled=True)
    briefing = _briefing("us_intraday_risk")
    briefing.chart_assets = MorningChartBuilder(profile=profile, market_data=_StubMarketData()).build(briefing)
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
    assert "Watchlist Performance Snapshot" not in html
    assert "Open Watchlist Explorer" in html


def test_snapshot_disabled_removes_morning_module():
    profile = _profile(watchlist_snapshot_enabled=False)
    briefing = _briefing("morning")
    briefing.chart_assets = MorningChartBuilder(profile=profile, market_data=_StubMarketData()).build(briefing)
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
    assert "Watchlist Performance Snapshot" not in html


def test_email_has_no_script_or_plotly_js():
    profile = _profile(watchlist_snapshot_enabled=True)
    briefing = _briefing("morning")
    briefing.chart_assets = MorningChartBuilder(profile=profile, market_data=_StubMarketData()).build(briefing)
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body.lower()
    assert "<script" not in html
    assert "plotly" not in html


def test_snapshot_failure_falls_back_without_blocking(monkeypatch):
    profile = _profile(watchlist_snapshot_enabled=True)
    briefing = _briefing("morning")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.briefing.chart_builder.build_watchlist_snapshot_spec", _boom)
    briefing.chart_assets = MorningChartBuilder(profile=profile, market_data=_StubMarketData()).build(briefing)
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
    assert "Watchlist Performance Snapshot unavailable: insufficient price history." in html


def test_watchlist_snapshot_preference_key_supported():
    assert normalize_preference_value("sections.morning.watchlist_snapshot", True) is True
    assert normalize_preference_value("sections.morning.watchlist_snapshot", False) is False
