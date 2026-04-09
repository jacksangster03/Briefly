"""Tests for briefing formatting and assembly."""

import pytest
from datetime import datetime

from app.briefing.formatter import TelegramFormatter
from app.briefing.templates import format_change, format_price_line, TELEGRAM_MAX_LENGTH
from app.schemas.briefings import MorningBriefing, MarketSetup, IntradayUpdate, BreakingAlert
from app.schemas.events import QuoteData, NormalisedEvent, MacroDataPoint, SectorSnapshot


class TestFormatHelpers:
    def test_format_change_positive(self):
        result = format_change(1.5, 0.75)
        assert result == "+1.50 (+0.75%)"

    def test_format_change_negative(self):
        result = format_change(-2.3, -1.1)
        assert result == "-2.30 (-1.10%)"

    def test_format_change_zero(self):
        result = format_change(0.0, 0.0)
        assert result == "+0.00 (+0.00%)"

    def test_format_price_line(self):
        result = format_price_line("S&P 500", 5234.50, 12.3, 0.24)
        assert "S&P 500" in result
        assert "5,234.50" in result
        assert "+0.24%" in result


class TestTelegramFormatter:
    def setup_method(self):
        self.formatter = TelegramFormatter()

    def test_morning_briefing_produces_output(self):
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 9, 12, 30),
            market_setup=MarketSetup(
                index_quotes=[
                    QuoteData(symbol="SPY", display_name="S&P 500",
                              current_price=5234.5, change=12.3, change_percent=0.24),
                ],
            ),
            top_themes=[
                NormalisedEvent(
                    title="Fed signals rate hold through Q3",
                    summary="FOMC minutes suggest patience on cuts.",
                    source="finnhub",
                    event_type="macro",
                    final_score=0.85,
                ),
            ],
            events_fetched=50,
            events_after_dedup=30,
            events_sent=5,
        )
        messages = self.formatter.format_morning_briefing(briefing)
        assert len(messages) >= 1
        assert "MORNING BRIEFING" in messages[0]
        assert "S&P 500" in messages[0]
        assert "Fed signals" in messages[0]

    def test_message_splitting(self):
        long_text = "A" * (TELEGRAM_MAX_LENGTH + 500)
        parts = TelegramFormatter._split_message(long_text)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= TELEGRAM_MAX_LENGTH

    def test_intraday_no_events(self):
        update = IntradayUpdate(
            hour_label="15:30",
            new_events=[],
            events_fetched=20,
            events_after_dedup=5,
            events_sent=0,
        )
        messages = self.formatter.format_intraday_update(update)
        assert len(messages) >= 1
        assert "No material new developments" in messages[0]

    def test_breaking_alert_format(self):
        evt = NormalisedEvent(
            title="FDA approves Lilly obesity drug",
            tickers=["LLY"],
            summary="Game-changing approval for weight management.",
            final_score=0.92,
            factual_confidence_score=0.95,
        )
        alert = BreakingAlert(
            event=evt,
            reason="FDA decision, high market cap, watchlist ticker",
        )
        messages = self.formatter.format_breaking_alert(alert)
        assert "BREAKING" in messages[0]
        assert "LLY" in messages[0]
        assert "FDA" in messages[0]


class TestSectorSnapshot:
    def test_sector_scan_format(self):
        formatter = TelegramFormatter()
        briefing = MorningBriefing(
            sector_scan=[
                SectorSnapshot(
                    sector_key="technology",
                    display_name="Technology",
                    etf_symbol="XLK",
                    etf_quote=QuoteData(
                        symbol="XLK", current_price=200.0,
                        change=1.5, change_percent=0.75,
                    ),
                    top_events=[
                        NormalisedEvent(
                            title="MSFT Azure revenue up 30%",
                            tickers=["MSFT"],
                            final_score=0.7,
                        ),
                    ],
                ),
            ],
            events_fetched=10,
            events_after_dedup=5,
            events_sent=1,
        )
        messages = formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "Technology" in full
        assert "XLK" in full
