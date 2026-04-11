"""Tests for briefing formatting and assembly."""

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_generator import MorningBriefingGenerator
from app.briefing.templates import format_change, format_price_line, TELEGRAM_MAX_LENGTH
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing, MarketSetup, IntradayUpdate, BreakingAlert
from app.schemas.events import QuoteData, NormalisedEvent, MacroDataPoint, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import InstrumentDef, SectorDef, SectorUniverse


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
        self.formatter = TelegramFormatter("Europe/Madrid")

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
            published_at=datetime(2026, 4, 9, 15, 9, tzinfo=timezone.utc),
        )
        alert = BreakingAlert(
            generated_at=datetime(2026, 4, 9, 17, 30),
            event=evt,
            reason="FDA decision, high market cap, watchlist ticker",
            market_context=[
                QuoteData(
                    symbol="SPY",
                    display_name="S&P 500",
                    current_price=5234.5,
                    previous_close=5222.2,
                    change=12.3,
                    change_percent=0.24,
                )
            ],
        )
        messages = self.formatter.format_breaking_alert(alert)
        assert "BREAKING" in messages[0]
        assert "Eli Lilly (LLY)" in messages[0]
        assert "FDA" in messages[0]
        assert "17:09 CEST" in messages[0]
        assert "S&P 500 (SPY): 5,234.50, +0.24% vs prior close 5,222.20" in messages[0]
        assert "Score:" not in messages[0]
        assert "Confidence:" not in messages[0]

    def test_intraday_event_includes_local_time_and_company_name(self):
        evt = NormalisedEvent(
            title="Amazon files 8-K after logistics update",
            tickers=["AMZN"],
            summary="Official filing details expanded delivery partnerships.",
            published_at=datetime(2026, 4, 9, 15, 9, tzinfo=timezone.utc),
            cluster_size=3,
            source="sec_edgar",
        )
        update = IntradayUpdate(
            hour_label="17:00",
            new_events=[evt],
            events_fetched=10,
            events_after_dedup=2,
            events_sent=1,
        )
        messages = self.formatter.format_intraday_update(update)
        full = "\n".join(messages)
        assert "Amazon (AMZN)" in full
        assert "17:09 CEST" in full

    def test_intraday_event_suppresses_duplicate_summary(self):
        """If the summary is just the title repeated, don't echo it."""
        evt = NormalisedEvent(
            title="Oando plans $750 million drilling campaign",
            summary="Oando plans $750 million drilling campaign - Reuters",
            tickers=[],
            source="newsapi",
            event_type="headline",
        )
        update = IntradayUpdate(
            hour_label="14:00",
            new_events=[evt],
            events_fetched=1,
            events_after_dedup=1,
            events_sent=1,
        )
        messages = self.formatter.format_intraday_update(update)
        full = "\n".join(messages)
        # Title shows once (bold wrapped), summary line must NOT re-echo it
        assert full.count("Oando plans $750 million drilling campaign") == 1

    def test_intraday_event_strips_cluster_suffix(self):
        """The [+N related] suffix clustering appends shouldn't leak into output."""
        evt = NormalisedEvent(
            title="Chip sector rally lifts semis",
            summary="Wafer pricing pressure eases heading into Q3. [+3 related]",
            tickers=["NVDA"],
            source="finnhub",
            event_type="market_news",
        )
        update = IntradayUpdate(
            hour_label="14:00",
            new_events=[evt],
            events_fetched=1,
            events_after_dedup=1,
            events_sent=1,
        )
        messages = self.formatter.format_intraday_update(update)
        full = "\n".join(messages)
        assert "[+" not in full
        assert "Wafer pricing pressure eases" in full

    def test_breaking_alert_suppresses_duplicate_summary(self):
        """Breaking alerts must never repeat the headline as the body."""
        evt = NormalisedEvent(
            title="Oando plans $750 million drilling campaign",
            summary="Oando plans $750 million drilling campaign",
            tickers=[],
            final_score=0.9,
            factual_confidence_score=0.9,
            published_at=datetime(2026, 4, 9, 15, 9, tzinfo=timezone.utc),
        )
        alert = BreakingAlert(
            event=evt,
            reason="High-impact energy event",
            market_context=[],
        )
        messages = self.formatter.format_breaking_alert(alert)
        full = "\n".join(messages)
        # Title appears exactly once (in the bold headline line)
        assert full.count("Oando plans $750 million drilling campaign") == 1

    def test_weekend_intraday_header_and_lede(self):
        update = IntradayUpdate(
            session_mode="saturday",
            hour_label="18:11",
            new_events=[],
            events_fetched=10,
            events_after_dedup=5,
            events_sent=0,
        )
        messages = self.formatter.format_intraday_update(update)
        full = "\n".join(messages)
        assert "WEEKEND UPDATE" in full
        assert "Weekend briefing" in full
        assert "No material weekend developments" in full

    def test_weekend_morning_uses_weekend_sections(self):
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 11, 8, 45),  # Saturday
            session_mode="saturday",
            market_setup=MarketSetup(
                index_quotes=[
                    QuoteData(symbol="SPY", display_name="S&P 500", current_price=5234.5, change=12.3, change_percent=0.24),
                ],
            ),
            top_themes=[
                NormalisedEvent(
                    title="Oil rises as Strait of Hormuz disruptions persist",
                    summary="Shipping and insurance costs climb across the energy complex.",
                    event_type="macro_release",
                ),
            ],
            watchlist_quotes=[
                QuoteData(symbol="NVDA", display_name="Nvidia", current_price=100.0, change=2.0, change_percent=2.0),
            ],
        )
        messages = self.formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "WEEKEND BRIEFING — SATURDAY" in full
        assert "LAST CLOSE (FRIDAY)" in full
        assert "WEEKEND DEVELOPMENTS" in full
        assert "WHAT TO WATCH NEXT WEEK" in full

    def test_weekend_breaking_context_uses_friday_label(self):
        evt = NormalisedEvent(
            title="Saudi Arabia says attacks cut oil output",
            summary="Output and pipeline flow disruption could pressure crude benchmarks.",
            factual_confidence_score=0.8,
        )
        alert = BreakingAlert(
            generated_at=datetime(2026, 4, 11, 12, 0),  # Saturday
            event=evt,
            reason="Widely reported energy supply disruption.",
            market_context=[
                QuoteData(
                    symbol="SPY",
                    display_name="S&P 500",
                    current_price=5234.5,
                    previous_close=5222.2,
                    change=12.3,
                    change_percent=0.24,
                )
            ],
        )
        messages = self.formatter.format_breaking_alert(alert)
        full = "\n".join(messages)
        assert "Friday prior close" in full
        assert "Score:" not in full
        assert "Confidence:" not in full


class TestSectorSnapshot:
    def test_sector_scan_format(self):
        formatter = TelegramFormatter("Europe/Madrid")
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

    def test_sector_scan_shows_freshness_and_collapses_empty_sectors(self):
        formatter = TelegramFormatter("Europe/Madrid")
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 11, 19, 0, tzinfo=timezone.utc),
            session_mode="saturday",
            sector_scan=[
                SectorSnapshot(
                    sector_key="technology",
                    display_name="Technology",
                    etf_symbol="XLK",
                    etf_quote=QuoteData(
                        symbol="XLK",
                        current_price=200.0,
                        change=1.5,
                        change_percent=0.75,
                        source="yfinance",
                        timestamp=datetime(2026, 4, 11, 18, 59, tzinfo=timezone.utc),
                    ),
                    top_events=[
                        NormalisedEvent(
                            title="MSFT Azure revenue up 30%",
                            tickers=["MSFT"],
                            final_score=0.7,
                        ),
                    ],
                ),
                SectorSnapshot(
                    sector_key="semiconductors",
                    display_name="Semiconductors",
                    etf_symbol="SMH",
                    etf_quote=QuoteData(
                        symbol="SMH",
                        current_price=260.0,
                        change=2.1,
                        change_percent=0.82,
                        source="yfinance",
                        timestamp=datetime(2026, 4, 11, 18, 58, tzinfo=timezone.utc),
                    ),
                    top_events=[],
                ),
            ],
        )
        messages = formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "As of" in full
        assert "via yfinance" in full
        assert "vs Friday close" in full
        assert "No high-trust developments:" in full
        assert "<b>Semiconductors</b>" not in full

    def test_watchlist_quotes_include_freshness_summary(self):
        formatter = TelegramFormatter("Europe/Madrid")
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 11, 19, 0, tzinfo=timezone.utc),
            session_mode="saturday",
            watchlist_quotes=[
                QuoteData(
                    symbol="NVDA",
                    display_name="Nvidia",
                    current_price=100.0,
                    change=2.0,
                    change_percent=2.0,
                    source="yfinance",
                    timestamp=datetime(2026, 4, 11, 18, 59, tzinfo=timezone.utc),
                ),
                QuoteData(
                    symbol="MSFT",
                    display_name="Microsoft",
                    current_price=50.0,
                    change=-0.2,
                    change_percent=-0.4,
                    source="finnhub",
                    timestamp=datetime(2026, 4, 11, 18, 57, tzinfo=timezone.utc),
                ),
            ],
        )
        messages = formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "Quotes as of" in full
        assert "sources: finnhub(1), yfinance(1)" in full
        assert "vs Friday close" in full


class _DummyMarketData:
    def get_quotes(self, symbols):
        return []


class _DummyNewsData:
    def fetch_all(self, watchlist=None):
        return []


class _DummyMacroData:
    def get_morning_macro(self):
        return []

    def get_treasury_yields(self):
        return None, None


class TestMorningSectorSelection:
    def test_sector_scan_skips_macro_bleed_with_single_loose_ticker(self):
        generator = MorningBriefingGenerator(
            settings=Settings(),
            profile=UserProfile(sector_weights={"semiconductors": 1.0}),
            universe=SectorUniverse(
                sectors=[
                    SectorDef(
                        key="semiconductors",
                        etf="SMH",
                        display_name="Semiconductors",
                        key_names=["NVDA", "AMD", "ASML"],
                    )
                ],
                indices=[InstrumentDef(symbol="SPY", display="S&P 500")],
                macro_instruments=[],
            ),
            market_data=_DummyMarketData(),
            news_data=_DummyNewsData(),
            macro_data=_DummyMacroData(),
        )

        snapshots = generator._build_sector_scan(
            [
                NormalisedEvent(
                    title="Powell says no rate hike needed to fight oil shock",
                    summary="Inflation and energy remain the core macro driver.",
                    tickers=["NVDA"],
                    sectors=["semiconductors"],
                    source="finnhub",
                    event_type="market_news",
                    final_score=0.9,
                ),
                NormalisedEvent(
                    title="ASML set to report Q1 earnings next week",
                    summary="Guidance focus remains on EUV demand.",
                    tickers=["ASML"],
                    sectors=["semiconductors"],
                    source="finnhub",
                    event_type="company_news",
                    final_score=0.8,
                ),
            ]
        )

        titles = [event.title for event in snapshots[0].top_events]
        assert "ASML set to report Q1 earnings next week" in titles
        assert "Powell says no rate hike needed to fight oil shock" not in titles
