from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.session_diagnosis import build_session_diagnosis
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import MacroDataPoint, QuoteData


def _briefing() -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime(2026, 5, 19, 6, 0, tzinfo=timezone.utc),
        session_key="morning",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500", current_price=7403.05, change=-5.3, change_percent=-0.07),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite", current_price=18600.0, change=-78.0, change_percent=-0.42),
                QuoteData(symbol="^RUT", display_name="Russell 2000", current_price=2090.0, change=-15.0, change_percent=-0.71),
                QuoteData(symbol="^STOXX50E", display_name="Euro STOXX 50", current_price=5200.0, change=39.0, change_percent=0.76),
            ],
            macro_quotes=[
                QuoteData(symbol="CL=F", display_name="WTI Crude", current_price=82.1, change=0.4, change_percent=0.49),
            ],
            treasury_10y=MacroDataPoint(series_id="DGS10", name="US 10Y", value=4.62, change=0.05),
        ),
        canonical_prices={
            "US": {"change_percent": -0.30},
            "EUROPE": {"change_percent": 0.55},
            "ASIA": {"change_percent": 0.18},
            "US10Y": {"value": 4.62, "change": 0.05},
            "WTI": {"change_percent": 0.49},
            "VIX": {"value": 17.8, "change_percent": -1.2},
        },
        quote_freshness={"VIX": {"freshness_state": "fresh"}},
    )


def test_morning_diagnosis_prefers_rates_and_regional_split_wording():
    briefing = _briefing()
    diagnosis = build_session_diagnosis(briefing)
    text = diagnosis.one_sentence_diagnosis.lower()
    assert "rates" in text
    assert "regionally split" in text
    assert "europe is firmer" in text

