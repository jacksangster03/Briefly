from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent, QuoteData


def test_market_setup_marks_ftse_closed_on_uk_holiday():
    formatter = TelegramFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 4, 8, 30, tzinfo=timezone.utc),
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(
                    symbol="^FTSE",
                    display_name="FTSE 100",
                    current_price=10363.9,
                    change=-14.92,
                    change_percent=-0.14,
                    source="yfinance",
                )
            ]
        ),
    )
    rendered = formatter._format_market_setup(briefing)
    assert "[closed, prior close" in rendered


def test_company_label_suppresses_mismatched_ticker():
    formatter = TelegramFormatter("Europe/Madrid")
    evt = NormalisedEvent(
        title="How The SPIE investment story is evolving with fresh targets",
        summary="French engineering group SPIE receives new analyst target updates.",
        tickers=["JPM"],
        source="newsapi",
        source_type="news",
    )
    assert formatter._company_label(evt) == ""


def test_company_label_keeps_valid_match():
    formatter = TelegramFormatter("Europe/Madrid")
    evt = NormalisedEvent(
        title="JPMorgan raises net interest income guidance",
        summary="JPMorgan executives highlighted resilient client activity and balance-sheet strength.",
        tickers=["JPM"],
        source="newsapi",
        source_type="news",
    )
    label = formatter._company_label(evt)
    assert "JPM" in label


def test_company_label_suppressed_when_confidence_low():
    formatter = TelegramFormatter("Europe/Madrid")
    evt = NormalisedEvent(
        title="JPMorgan raises net interest income guidance",
        summary="JPMorgan highlighted resilient client activity.",
        tickers=["JPM"],
        source="newsapi",
        source_type="news",
        raw_data={"symbol_confidence": 0.55},
    )
    assert formatter._company_label(evt) == ""


def test_themes_section_shows_clean_fallback_when_empty():
    formatter = TelegramFormatter("Europe/Madrid")
    rendered = formatter._format_themes_for_mode([], session_mode="weekday")
    assert "TOP THEMES" in rendered
    assert "No high-confidence portfolio/watchlist themes passed relevance and source-quality filters this cycle." in rendered


def test_provider_health_note_is_human_readable_not_raw_counts():
    formatter = TelegramFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 4, 8, 30, tzinfo=timezone.utc),
        market_setup=MarketSetup(),
        data_freshness={"Provider Health": "alpha_vantage:0, finnhub:100, fmp:0, gdelt:0"},
    )
    rendered = "\n".join(formatter.format_morning_briefing(briefing))
    assert "Provider notes:" in rendered
    assert "alpha_vantage:0" not in rendered
