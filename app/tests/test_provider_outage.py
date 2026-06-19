"""Provider outage handling tests for briefing safety surfaces."""

from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_charts import _geo_confirmation_ladder_spec, _oil_transmission_card_spec
from app.briefing.session_snapshot import snapshot_metrics
from app.schemas.briefings import MarketSetup, MorningBriefing


def test_formatter_shows_data_outage_banner_and_footer_outage_note():
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        market_data_outage=True,
        news_data_outage=True,
        events_fetched=0,
        events_after_dedup=0,
        events_sent=0,
    )
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "DATA OUTAGE / PROVIDER DEGRADED" in text
    assert "provider outage (raw fetch 0)" in text


def test_oil_transmission_suppressed_when_wti_and_brent_unavailable():
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
        quote_freshness={},
    )
    spec = _oil_transmission_card_spec(briefing, {})
    assert spec["available"] is False
    assert "WTI/Brent unavailable" in str(spec.get("reason_if_hidden", ""))


def test_geo_confirmation_suppressed_when_all_inputs_unavailable():
    briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
    spec = _geo_confirmation_ladder_spec(briefing, {})
    assert spec["available"] is False
    assert "unavailable" in str(spec.get("reason_if_hidden", "")).lower()


def test_snapshot_metrics_omit_portfolio_contrib_with_incomplete_quote_set():
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        morning_chart_bundle={
            "charts": [
                {
                    "chart_key": "pnl_attribution_waterfall",
                    "meta": {"total_contribution": 0.0},
                }
            ]
        },
        portfolio_quotes=[],
    )
    metrics = snapshot_metrics(briefing)
    assert "portfolio_contrib_pct" not in metrics
