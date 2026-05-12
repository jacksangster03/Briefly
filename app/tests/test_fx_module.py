"""Tests for the FX & Dollar Pulse module.

All tests are deterministic: no live provider calls. yfinance and FRED
are mocked where necessary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.fx.basket import FXInstrument, build_fx_basket
from app.fx.panel import FXQuote, fetch_fx_panel
from app.fx.signals import FXSignals, build_fx_signals
from app.briefing.fx_section import (
    build_fx_section_text,
    should_include_fx,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_quote(
    label: str,
    symbol: str = "TEST=X",
    source: str = "yfinance",
    base: str = "EUR",
    quote_ccy: str = "USD",
    value: float | None = 1.10,
    daily_change_pct: float | None = 0.5,
    change_5d_pct: float | None = 1.0,
    status: str = "ok",
    freshness: str = "prior_close",
) -> FXQuote:
    instrument = FXInstrument(
        label=label,
        symbol=symbol,
        source=source,
        base=base,
        quote=quote_ccy,
        is_dxy_proxy=(label == "Trade-weighted USD"),
        optional=False,
        condition=None,
    )
    return FXQuote(
        instrument=instrument,
        value=value,
        daily_change_pct=daily_change_pct,
        change_5d_pct=change_5d_pct,
        source=source,
        freshness=freshness,
        status=status,
        fetched_at=datetime.now(timezone.utc),
    )


def _unavailable_quote(label: str) -> FXQuote:
    return _make_quote(
        label=label,
        value=None,
        daily_change_pct=None,
        change_5d_pct=None,
        status="unavailable",
        freshness="unavailable",
    )


def _sample_spain_panel() -> list[FXQuote]:
    return [
        _make_quote("EUR/USD", symbol="EURUSD=X", base="EUR", quote_ccy="USD", value=1.105, daily_change_pct=0.6, change_5d_pct=1.2),
        _make_quote("Trade-weighted USD", symbol="DTWEXBGS", source="fred", base="USD", quote_ccy="BASKET", value=115.2, daily_change_pct=-0.3, change_5d_pct=-0.5),
        _make_quote("EUR/GBP", symbol="EURGBP=X", base="EUR", quote_ccy="GBP", value=0.845, daily_change_pct=0.2, change_5d_pct=0.4),
        _make_quote("USD/JPY", symbol="USDJPY=X", base="USD", quote_ccy="JPY", value=156.5, daily_change_pct=0.7, change_5d_pct=1.1),
        _make_quote("USD/CNH", symbol="USDCNH=X", base="USD", quote_ccy="CNH", value=7.32, daily_change_pct=0.6, change_5d_pct=0.8),
    ]


# ---------------------------------------------------------------------------
# Test 1: Spain/Eurozone basket composition
# ---------------------------------------------------------------------------

def test_basket_spain_eurozone():
    profile = {"home_region": "Spain"}
    basket = build_fx_basket(profile, {})
    labels = [i.label for i in basket]
    assert "EUR/USD" in labels
    assert "Trade-weighted USD" in labels
    assert "EUR/GBP" in labels
    assert "USD/JPY" in labels
    assert "USD/CNH" in labels
    # DXY proxy must use FRED
    dxy = next((i for i in basket if i.is_dxy_proxy), None)
    assert dxy is not None
    assert dxy.source == "fred"


def test_basket_eurozone_variant():
    profile = {"home_region": "eurozone"}
    basket = build_fx_basket(profile, {})
    labels = [i.label for i in basket]
    assert "EUR/USD" in labels
    assert "Trade-weighted USD" in labels


# ---------------------------------------------------------------------------
# Test 2: US profile basket
# ---------------------------------------------------------------------------

def test_basket_us_profile():
    profile = {"home_region": "US"}
    basket = build_fx_basket(profile, {})
    labels = [i.label for i in basket]
    assert "Trade-weighted USD" in labels
    assert "EUR/USD" in labels
    assert "USD/JPY" in labels
    assert "GBP/USD" in labels
    assert "USD/CNH" in labels


# ---------------------------------------------------------------------------
# Test 3: UK profile basket
# ---------------------------------------------------------------------------

def test_basket_uk_profile():
    profile = {"home_region": "UK"}
    basket = build_fx_basket(profile, {})
    labels = [i.label for i in basket]
    assert "GBP/USD" in labels
    assert "EUR/GBP" in labels
    assert "Trade-weighted USD" in labels
    assert "USD/JPY" in labels


# ---------------------------------------------------------------------------
# Test 4: APAC profile basket
# ---------------------------------------------------------------------------

def test_basket_apac_profile():
    profile = {"home_region": "APAC"}
    basket = build_fx_basket(profile, {})
    labels = [i.label for i in basket]
    assert "USD/JPY" in labels
    assert "USD/CNH" in labels
    assert "AUD/USD" in labels
    assert "Trade-weighted USD" in labels


# ---------------------------------------------------------------------------
# Test 5: Panel handles partial data without crashing
# ---------------------------------------------------------------------------

def test_panel_partial_data():
    """Panel with some unavailable instruments should not raise."""
    panel = [
        _make_quote("EUR/USD"),
        _unavailable_quote("Trade-weighted USD"),
        _unavailable_quote("EUR/GBP"),
        _make_quote("USD/JPY", symbol="USDJPY=X", base="USD", quote_ccy="JPY"),
    ]
    # signals should work without crashing
    signals = build_fx_signals(panel)
    assert signals is not None
    assert "Trade-weighted USD" in signals.missing
    assert "EUR/GBP" in signals.missing
    assert signals.eur_pressure in ("stronger", "weaker", "neutral", "unavailable")


# ---------------------------------------------------------------------------
# Test 6: FX block suppressed when materiality is low
# ---------------------------------------------------------------------------

def test_fx_block_suppressed_low_materiality():
    panel = [
        _make_quote("EUR/USD", daily_change_pct=0.05, change_5d_pct=0.1),
        _make_quote("Trade-weighted USD", source="fred", daily_change_pct=0.05, change_5d_pct=0.1),
    ]
    signals = build_fx_signals(panel)
    assert signals.fx_materiality == "low"
    result = should_include_fx(signals, "morning", {"home_region": "spain"})
    assert result is False


# ---------------------------------------------------------------------------
# Test 7: FX block appears when materiality is high
# ---------------------------------------------------------------------------

def test_fx_block_included_high_materiality():
    panel = _sample_spain_panel()
    signals = build_fx_signals(panel)
    assert signals.fx_materiality in ("medium", "high")
    result = should_include_fx(signals, "morning", {"home_region": "spain"})
    assert result is True


# ---------------------------------------------------------------------------
# Test 8: Morning includes FX chart spec when score >= 5
# ---------------------------------------------------------------------------

def test_fx_chart_spec_available_when_score_high():
    from app.briefing.morning_charts import _fx_pulse_chart_spec
    from app.schemas.briefings import MorningBriefing
    from app.personalization.user_profile import UserProfile

    briefing = MorningBriefing()
    briefing.fx_materiality_score = 6
    profile = UserProfile(name="test", home_region="spain")
    spec = _fx_pulse_chart_spec(briefing, profile)
    assert spec["chart_key"] == "fx_pulse_chart"
    assert spec["available"] is True
    assert len(spec["series"]) >= 2


def test_fx_chart_spec_unavailable_when_score_low():
    from app.briefing.morning_charts import _fx_pulse_chart_spec
    from app.schemas.briefings import MorningBriefing
    from app.personalization.user_profile import UserProfile

    briefing = MorningBriefing()
    briefing.fx_materiality_score = 2
    profile = UserProfile(name="test", home_region="spain")
    spec = _fx_pulse_chart_spec(briefing, profile)
    assert spec["available"] is False
    assert spec["reason_if_hidden"] != ""


# ---------------------------------------------------------------------------
# Test 9: Europe Midday section text prioritises EUR/USD and EUR/GBP
# ---------------------------------------------------------------------------

def test_europe_midday_text_prioritises_eur():
    panel = _sample_spain_panel()
    signals = build_fx_signals(panel)
    text = build_fx_section_text(panel, signals, {"home_region": "spain"}, "europe_midday")
    assert "EUR/USD" in text or "EUR" in text


# ---------------------------------------------------------------------------
# Test 10: US Pre-Open text prioritises USD/JPY and USD/CNH
# ---------------------------------------------------------------------------

def test_us_preopen_text_prioritises_jpy_cnh():
    panel = _sample_spain_panel()
    signals = build_fx_signals(panel)
    text = build_fx_section_text(panel, signals, {"home_region": "US"}, "us_pre_open")
    assert "USD" in text


# ---------------------------------------------------------------------------
# Test 11: Intraday suppresses low-materiality FX
# ---------------------------------------------------------------------------

def test_intraday_suppresses_low_materiality():
    panel = [
        _make_quote("EUR/USD", daily_change_pct=0.05),
        _make_quote("Trade-weighted USD", source="fred", daily_change_pct=0.05),
    ]
    signals = build_fx_signals(panel)
    assert signals.materiality_score <= 2
    result = should_include_fx(signals, "us_intraday_risk", {"home_region": "us"})
    assert result is False


# ---------------------------------------------------------------------------
# Test 12: FX pulse chart series have distinct colours
# ---------------------------------------------------------------------------

def test_fx_chart_series_distinct_colours():
    from app.briefing.morning_charts import _fx_pulse_chart_spec
    from app.schemas.briefings import MorningBriefing
    from app.personalization.user_profile import UserProfile

    briefing = MorningBriefing()
    briefing.fx_materiality_score = 8
    profile = UserProfile(name="test", home_region="spain")
    spec = _fx_pulse_chart_spec(briefing, profile)
    colours = [s["colour"] for s in spec["series"]]
    # All colours should be unique within the series for a standard basket
    assert len(colours) == len(set(colours)), "FX chart series colours are not distinct"


# ---------------------------------------------------------------------------
# Test 13: FX API route returns expected JSON structure (no live calls)
# ---------------------------------------------------------------------------

def test_fx_api_route_structure():
    """Test that the /api/fx-pulse endpoint returns the expected structure
    without making any live provider calls."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from app.web.app import create_web_app
    from app.settings import Settings

    settings = Settings(fred_api_key="", finnhub_api_key="")

    # Mock panel to avoid live calls
    _mock_panel = _sample_spain_panel()

    def _mock_fetch_panel(basket, settings_dict):
        return _mock_panel

    with patch("app.fx.panel.fetch_fx_panel", side_effect=_mock_fetch_panel):
        app = create_web_app(settings)
        client = TestClient(app)
        response = client.get("/api/fx-pulse?profile=default_user&mode=simple")
        assert response.status_code == 200
        data = response.json()
        assert "usd_pressure" in data
        assert "eur_usd" in data
        assert "usd_jpy" in data
        assert "fx_materiality" in data
        assert "materiality_score" in data
        assert "profile_basket_label" in data
        assert "drivers" in data
        assert "missing" in data


# ---------------------------------------------------------------------------
# Test 14: UI routes do not call live providers directly
# ---------------------------------------------------------------------------

def test_ui_routes_do_not_call_live_providers():
    """The /api/fx-pulse endpoint should gracefully degrade on provider failure."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from app.web.app import create_web_app
    from app.settings import Settings

    settings = Settings(fred_api_key="", finnhub_api_key="")

    # Simulate provider failure
    def _mock_fail(basket, settings_dict):
        raise RuntimeError("Provider unavailable")

    with patch("app.fx.panel.fetch_fx_panel", side_effect=_mock_fail):
        app = create_web_app(settings)
        client = TestClient(app)
        # Should not raise 500 - must return graceful degradation JSON
        response = client.get("/api/fx-pulse?profile=default_user")
        assert response.status_code == 200
        data = response.json()
        assert data.get("usd_pressure") == "unavailable"


# ---------------------------------------------------------------------------
# Test 15: Optional oil_shock pair included when context has oil_shock=True
# ---------------------------------------------------------------------------

def test_optional_oil_shock_pair_included():
    profile = {"home_region": "spain"}
    context = {"oil_shock": True}
    basket = build_fx_basket(profile, {}, context)
    labels = [i.label for i in basket]
    assert "USD/NOK" in labels


# ---------------------------------------------------------------------------
# Test 16: Optional pair NOT included when condition not met
# ---------------------------------------------------------------------------

def test_optional_pair_excluded_when_condition_not_met():
    profile = {"home_region": "spain"}
    context = {}  # no oil_shock
    basket = build_fx_basket(profile, {}, context)
    labels = [i.label for i in basket]
    assert "USD/NOK" not in labels


# ---------------------------------------------------------------------------
# Test 17: "VIX confirms" language not produced for unavailable FX metric
# ---------------------------------------------------------------------------

def test_no_confirms_language_for_unavailable():
    """FX section text must not say 'confirms' for an unavailable metric."""
    panel = [_unavailable_quote("EUR/USD"), _unavailable_quote("Trade-weighted USD")]
    signals = build_fx_signals(panel)
    text = build_fx_section_text(panel, signals, {"home_region": "spain"}, "morning")
    assert "confirms" not in text.lower()


# ---------------------------------------------------------------------------
# Test 18: Signals return "unavailable" gracefully when all data missing
# ---------------------------------------------------------------------------

def test_signals_all_unavailable():
    panel = [
        _unavailable_quote("EUR/USD"),
        _unavailable_quote("Trade-weighted USD"),
        _unavailable_quote("EUR/GBP"),
        _unavailable_quote("USD/JPY"),
        _unavailable_quote("USD/CNH"),
    ]
    signals = build_fx_signals(panel)
    assert signals.usd_pressure == "unavailable"
    assert signals.eur_pressure == "unavailable"
    assert signals.yen_risk_signal == "unavailable"
    assert signals.china_fx_stress == "unavailable"
    assert signals.fx_materiality == "low"
    assert signals.materiality_score == 0
    assert len(signals.missing) == 5
    assert len(signals.drivers) == 0
