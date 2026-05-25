from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.session_diagnosis import build_session_diagnosis
from app.briefing.morning_charts import _pnl_waterfall_spec
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


def _briefing(*, session_key: str = "morning") -> MorningBriefing:
    return MorningBriefing(generated_at=datetime.now(timezone.utc), session_key=session_key)


def test_no_mixed_tape_when_market_data_unavailable():
    briefing = _briefing()
    briefing.market_data_outage = True
    diag = build_session_diagnosis(briefing)
    assert diag.regime_label == "DATA DEGRADED"
    assert "mixed tape" not in diag.one_sentence_diagnosis.lower()


def test_trigger_board_active_watch_cooled():
    briefing = _briefing(session_key="us_pre_open")
    briefing.canonical_prices = {
        "US10Y": {"value": 4.52, "change": 0.04},
        "WTI": {"change_percent": -1.25},
        "VIX": {"value": 18.0, "change_percent": -0.8},
    }
    diag = build_session_diagnosis(briefing)
    board = diag.trigger_board
    assert board["active"]
    assert board["watch"] or board["cooled"]
    assert any("Rates: ACTIVE" in line for line in board["active"])
    assert any("COOLED" in line for line in board["cooled"])


def test_six_sessions_have_distinct_prefix():
    keys = [
        "morning",
        "europe_midday",
        "us_pre_open",
        "us_intraday_risk",
        "into_close",
        "closing_wrap",
    ]
    outputs = []
    for key in keys:
        briefing = _briefing(session_key=key)
        outputs.append(build_session_diagnosis(briefing).one_sentence_diagnosis)
    assert len(set(outputs)) == len(outputs)


def test_geo_headline_vs_market_confirmation_split_when_vix_missing():
    briefing = _briefing()
    briefing.geo_risk_level = "ELEVATED"
    briefing.quote_freshness = {"VIX": {"freshness_state": "unavailable"}}
    diag = build_session_diagnosis(briefing)
    assert "VIX unavailable" in " | ".join(diag.data_caveats)
    assert "geo_market_confirmation_missing_vix" in diag.rejected_drivers


def test_formatter_uses_trigger_board_labels():
    briefing = _briefing(session_key="closing_wrap")
    briefing.trigger_board = {
        "active": ["Rates: ACTIVE. US 10Y is 4.62%, already above 4.45%; valuation-sensitive growth and duration remain under pressure."],
        "watch": [],
        "cooled": [],
    }
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "TOMORROW TRIGGER BOARD" in text
    assert "Active triggers:" in text


def test_no_fake_zero_region_lines_when_data_missing():
    briefing = _briefing()
    briefing.market_data_outage = True
    briefing.market_setup = MarketSetup(index_quotes=[], macro_quotes=[])
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "US +0.00%" not in text
    assert "Europe +0.00%" not in text
    assert "Asia +0.00%" not in text


def test_vix_consistency_unavailable_message_present():
    briefing = _briefing()
    briefing.quote_freshness = {"VIX": {"freshness_state": "unavailable"}}
    briefing.market_setup = MarketSetup(
        index_quotes=[QuoteData(symbol="^VIX", display_name="VIX", current_price=0.0, change_percent=0.0)]
    )
    diag = build_session_diagnosis(briefing)
    assert any("VIX unavailable" in line for line in diag.data_caveats)


def test_positive_contribution_not_called_drag():
    briefing = _briefing()
    quotes = [
        QuoteData(symbol="AAA", display_name="AAA", current_price=10, change_percent=2.0),
        QuoteData(symbol="BBB", display_name="BBB", current_price=10, change_percent=0.5),
    ]
    from app.personalization.user_profile import UserProfile
    from app.schemas.portfolio import PortfolioHolding
    profile = UserProfile(
        portfolio_holdings=[
            PortfolioHolding(symbol="AAA", weight_pct=60.0),
            PortfolioHolding(symbol="BBB", weight_pct=40.0),
        ],
    )
    spec = _pnl_waterfall_spec(quotes, profile)
    caption = str(spec.get("caption") or "")
    assert "largest drag" not in caption.lower()


# ---------------------------------------------------------------------------
# Holiday-aware session diagnosis tests
# ---------------------------------------------------------------------------

def test_no_rates_score_in_user_facing_diagnosis():
    """The one_sentence_diagnosis must not contain 'rates score' text."""
    briefing = _briefing(session_key="us_pre_open")
    briefing.canonical_prices = {
        "US10Y": {"value": 4.52, "change": 0.04},
        "WTI": {"change_percent": -0.5},
    }
    diag = build_session_diagnosis(briefing)
    assert "rates score" not in diag.one_sentence_diagnosis.lower(), (
        f"'rates score' must not appear in user-facing output: {diag.one_sentence_diagnosis}"
    )


def test_mixed_signals_sentence_no_raw_score():
    """Mixed-signals fallback must not include raw internal score values."""
    briefing = _briefing(session_key="morning")
    # Deliberately set no canonical prices to trigger mixed fallback
    briefing.canonical_prices = {}
    diag = build_session_diagnosis(briefing)
    sentence = diag.one_sentence_diagnosis.lower()
    # Must not include patterns like "rates score +0.40" or "rates score -0.40"
    import re
    assert not re.search(r"rates score [+\-]?\d+\.\d+", sentence), (
        f"Raw score value found in diagnosis: {diag.one_sentence_diagnosis}"
    )


def test_geo_high_vix_unavailable_flagged_in_plain_language():
    """When geo risk is high and VIX unavailable, diagnosis mentions incomplete confirmation."""
    briefing = _briefing(session_key="morning")
    briefing.geo_risk_level = "HIGH"
    briefing.quote_freshness = {"VIX": {"freshness_state": "unavailable"}}
    briefing.canonical_prices = {}
    diag = build_session_diagnosis(briefing)
    # The mixed-signals sentence should mention incomplete confirmation
    sentence = diag.one_sentence_diagnosis.lower()
    assert "incomplete" in sentence or "unavailable" in sentence, (
        f"Expected incomplete confirmation mention: {diag.one_sentence_diagnosis}"
    )
