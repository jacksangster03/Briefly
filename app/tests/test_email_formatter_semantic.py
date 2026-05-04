from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import MacroDataPoint, QuoteData


def _sample_briefing() -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="SPX", display_name="S&P 500 (SPX)", current_price=7230.12, change_percent=0.29, source="yfinance"),
                QuoteData(symbol="COMP", display_name="Nasdaq Composite (COMP)", current_price=25114.44, change_percent=0.89, source="yfinance"),
                QuoteData(symbol="DJIA", display_name="Dow Jones (DJIA)", current_price=49499.27, change_percent=-0.31, source="yfinance"),
                QuoteData(symbol="VIX", display_name="VIX", current_price=17.4, change_percent=2.8, source="yfinance"),
                QuoteData(symbol="CL1", display_name="WTI Crude Oil (CL1:COM)", current_price=103.3, change_percent=1.2, source="yfinance"),
                QuoteData(symbol="GC1", display_name="Gold (GC1:COM)", current_price=4590.0, change_percent=-0.8, source="yfinance"),
            ]
        ),
        macro_context=[
            MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.40, change=-0.02, source="fred"),
            MacroDataPoint(series_id="DGS2", name="US 2Y Treasury Yield", value=3.88, change=-0.04, source="fred"),
            MacroDataPoint(series_id="T10Y2Y", name="10Y-2Y Yield Spread", value=0.51, change=-0.01, source="fred"),
        ],
        market_setup_analysis=(
            "Regional split is visible (US +0.33%, Europe -0.40%, Asia +0.96%), signaling divergence across major sessions."
        ),
        geo_risk_level="moderate",
    )


def test_colorize_does_not_recolor_10y2y_label():
    formatter = EmailFormatter("Europe/Madrid")
    formatter.enable_move_intensity_shading = True
    formatter._move_shading_baselines = formatter._build_move_shading_baselines(_sample_briefing())

    line = "10Y-2Y Yield Spread: 0.5100 (-0.0100)"
    rendered = formatter._colorize_structured_line(line, "Macro Context")

    assert "10Y-2Y Yield Spread" in rendered
    assert "10Y-<span" not in rendered
    assert "<span style=\"color:" in rendered


def test_session_quality_accent_applies_to_header_rule():
    formatter = EmailFormatter("Europe/Madrid")
    formatter.enable_session_quality_accents = True
    briefing = _sample_briefing()
    briefing.morning_chart_bundle = {"regime_tags": ["mixed"], "meta": {}}

    html_body = formatter._html_body(briefing, "<b>MARKET SETUP</b>\nS&P 500 (SPX): 7,230.12 +21.11 (+0.29%)", "Morning Briefing | Mon 04 May")

    # Session accent should be one of configured deterministic bucket colors.
    assert any(color in html_body for color in ["#5C1111", "#9B2C2C", "#2F3744", "#0F7A4A", "#16A34A"])
