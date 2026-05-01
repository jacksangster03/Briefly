"""Tests for briefing formatting and assembly."""

from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_generator import MorningBriefingGenerator
from app.briefing.global_news_selector import build_market_relevance_note
from app.briefing.market_setup_interpreter import interpret_market_setup
from app.briefing.templates import format_change, format_price_line, TELEGRAM_MAX_LENGTH
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing, MarketSetup, IntradayUpdate, BreakingAlert
from app.schemas.events import QuoteData, NormalisedEvent, MacroDataPoint, SectorSnapshot, EarningsEvent
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
            market_setup_analysis="Market tone is broadly risk-on with supportive breadth and easing commodity pressure.",
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
        assert "Setup read:" in messages[0]
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

    def test_morning_includes_global_news_section(self):
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 13, 12, 30),
            global_news=[
                NormalisedEvent(
                    title="Oil rises as Strait of Hormuz disruptions persist",
                    summary="Shipping and insurance costs climb across the energy complex.",
                    event_type="geopolitical",
                    final_score=0.82,
                    cluster_size=4,
                )
            ],
            events_fetched=20,
            events_after_dedup=12,
            events_sent=3,
        )
        messages = self.formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "GLOBAL NEWS & GEOPOLITICS" in full
        assert "Why market-relevant:" in full
        assert "Oil rises as Strait of Hormuz disruptions persist" in full

    def test_global_news_relevance_note_dedup_uses_alternates(self):
        event = NormalisedEvent(
            title="Iran reopens Strait of Hormuz",
            summary="Shipping lanes and tanker risk remain in focus.",
            event_type="geopolitical",
        )
        used: set[str] = set()
        first = build_market_relevance_note(event, used_notes=used)
        second = build_market_relevance_note(event, used_notes=used)
        assert first.startswith("Why market-relevant:")
        assert second.startswith("Why market-relevant:")
        assert first != second

    def test_morning_market_setup_uses_display_label_not_provider_symbol(self):
        briefing = MorningBriefing(
            market_setup=MarketSetup(
                index_quotes=[
                    QuoteData(
                        symbol="^GSPC",
                        display_name="S&P 500 (SPX)",
                        current_price=7100.0,
                        change=50.0,
                        change_percent=0.71,
                    ),
                ],
            ),
        )
        messages = self.formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "S&P 500 (SPX)" in full
        assert "^GSPC" not in full

    def test_email_formatter_includes_setup_analysis(self):
        briefing = MorningBriefing(
            generated_at=datetime(2026, 4, 9, 12, 30),
            market_setup=MarketSetup(
                index_quotes=[
                    QuoteData(symbol="SPY", display_name="S&P 500 (SPX)", current_price=5234.5, change=12.3, change_percent=0.24),
                ],
            ),
            market_setup_analysis="Market tone is mixed with no single dominant impulse.",
        )
        email = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing)
        assert "Setup read:" in email.html_body
        assert "single dominant impulse" in email.html_body

    def test_market_setup_analysis_mentions_divergence_and_vol_regime(self):
        setup = MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7100, change=84, change_percent=1.2),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=24000, change=360, change_percent=1.5),
                QuoteData(symbol="^STOXX50E", display_name="EURO STOXX 50", current_price=6057, change=-80, change_percent=-1.3),
                QuoteData(symbol="^DAX", display_name="DAX", current_price=24700, change=-220, change_percent=-0.9),
                QuoteData(symbol="^VIX", display_name="VIX", current_price=19.5, change=2.0, change_percent=11.5),
            ],
            macro_quotes=[
                QuoteData(symbol="CL=F", display_name="WTI Crude Oil (CL1:COM)", current_price=82.5, change=4.5, change_percent=5.96),
                QuoteData(symbol="GC=F", display_name="Gold (GC1:COM)", current_price=4800.0, change=-2.0, change_percent=-0.04),
            ],
        )
        macro = [
            MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.32, change=0.03),
            MacroDataPoint(series_id="UST2Y", name="US 2Y Treasury Yield", value=3.78, change=0.02),
            MacroDataPoint(series_id="SPREAD", name="10Y-2Y Yield Spread", value=0.55, change=0.01),
        ]
        global_news = [
            NormalisedEvent(
                title="Iran reopens Strait of Hormuz",
                summary="Shipping risk remains elevated.",
                event_type="geopolitical",
            )
        ]
        result = interpret_market_setup(setup, macro, global_news=global_news)
        text = result.narrative.lower()
        assert "regional split" in text
        assert "volatility regime" in text
        assert "oil-led" in text or "geopolitics-driven" in text

    def test_earnings_section_uses_company_name_and_grouping(self):
        from datetime import date, timedelta
        today = date.today()
        tomorrow = today + timedelta(days=1)
        briefing = MorningBriefing(
            earnings_calendar=[
                EarningsEvent(
                    symbol="AAPL",
                    company_name="Apple Inc.",
                    sector="Technology",
                    report_date=today.isoformat(),
                    fiscal_quarter="Q2 2026",
                    time="amc",
                ),
                EarningsEvent(
                    symbol="MSFT",
                    company_name="Microsoft Corp.",
                    sector="Technology",
                    report_date=tomorrow.isoformat(),
                    fiscal_quarter="Q2 2026",
                    time="amc",
                ),
            ],
            earnings_relevance={"AAPL": "portfolio"},
        )
        messages = self.formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "Today" in full
        assert "Tomorrow" in full
        assert "(AAPL) Apple Inc., Technology" in full
        assert "Q2 2026" in full
        assert "post-market" in full
        assert "[portfolio]" in full
        assert "Upcoming:" not in full

    def test_earnings_line_omits_sector_when_unknown(self):
        e = EarningsEvent(
            symbol="ZZZ",
            company_name="Zeta Corp",
            report_date="2026-05-01",
            fiscal_quarter="Q1 2026",
            time="bmo",
        )
        line = self.formatter._format_earnings_line(e, {})
        assert line.startswith("(ZZZ) Zeta Corp")
        assert "," not in line.split(" — ")[0]
        assert "Q1 2026" in line
        assert "01/05/2026" in line
        assert "pre-market" in line

    def test_earnings_line_includes_estimate_when_provided(self):
        e = EarningsEvent(
            symbol="AAPL",
            company_name="Apple Inc.",
            sector="Technology",
            report_date="2026-05-01",
            fiscal_quarter="Q2 2026",
            time="amc",
            eps_estimate=2.34,
        )
        line = self.formatter._format_earnings_line(e, {"AAPL": "portfolio"})
        assert "est. $2.34" in line
        assert "[portfolio]" in line

    def test_earnings_section_is_empty_when_no_dated_events(self):
        # Events without report_date should still render under "This Week" but
        # if the calendar is empty, the section should be omitted entirely.
        briefing = MorningBriefing(earnings_calendar=[])
        messages = self.formatter.format_morning_briefing(briefing)
        full = "\n".join(messages)
        assert "EARNINGS CALENDAR" not in full

    def test_morning_includes_regional_and_portfolio_impact_sections(self):
        briefing = MorningBriefing(
            regional_lens=[
                {
                    "region": "US",
                    "direction": "up",
                    "status": "lead",
                    "driver": "rates and inflation repricing",
                    "implication": "US participation supports broad risk appetite.",
                }
            ],
            regional_skew_summary="Regional skew: US up, Europe mixed, Asia up.",
            portfolio_impact_bullets=[
                "Risk backdrop is supportive enough for growth-heavy exposures.",
                "Most portfolio-linked names in today’s tape: NVDA, MSFT.",
            ],
            portfolio_action_posture="monitor_and_simulate",
            regime_context="Regime context: broad continuation versus recent sessions.",
            positioning_alignment="Positioning alignment: broadly aligned with a constructive regime.",
        )
        full = "\n".join(self.formatter.format_morning_briefing(briefing))
        assert "REGIONAL LENS" in full
        assert "PORTFOLIO IMPACT TODAY" in full
        assert "REGIME CONTEXT" in full
        assert "monitor and simulate" in full

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

    def test_intraday_global_risk_block_renders(self):
        update = IntradayUpdate(
            hour_label="12:56",
            global_risk_items=[
                NormalisedEvent(
                    title="Oil tops $100 as US moves to blockade Iran",
                    summary="Energy shock reprices inflation expectations.",
                    event_type="geopolitical",
                    cluster_size=5,
                )
            ],
            new_events=[],
            events_fetched=10,
            events_after_dedup=5,
            events_sent=1,
        )
        messages = self.formatter.format_intraday_update(update)
        full = "\n".join(messages)
        assert "GLOBAL RISK UPDATE" in full
        assert "Why market-relevant:" in full

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


class _DummyNewsDataWithEvents:
    def __init__(self, events):
        self._events = events
        self.finnhub = None

    def fetch_all(self, watchlist=None):
        return self._events

    def fetch_market_news(self):
        return self._events

    @staticmethod
    def fetch_company_news(*_args, **_kwargs):
        return []

    @staticmethod
    def fetch_filings(*_args, **_kwargs):
        return []


class _DummyMacroData:
    def get_morning_macro(self):
        return []

    def get_treasury_yields(self):
        return None, None

    def get_ecb_snapshot(self):
        return []

    def get_eurostat_snapshot(self):
        return []


class TestMorningSectorSelection:
    def test_sector_scan_skips_macro_bleed_with_single_loose_ticker(self):
        generator = MorningBriefingGenerator(
            settings=Settings(enable_charts=False),
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


class TestMorningGlobalSectionDedupe:
    def test_global_news_dedupes_against_other_sections(self):
        generator = MorningBriefingGenerator(
            settings=Settings(),
            profile=UserProfile(sector_weights={"technology": 1.0}),
            universe=SectorUniverse(
                sectors=[],
                indices=[InstrumentDef(symbol="SPY", display="S&P 500")],
                macro_instruments=[],
            ),
            market_data=_DummyMarketData(),
            news_data=_DummyNewsData(),
            macro_data=_DummyMacroData(),
        )

        shared_event = NormalisedEvent(
            title="Oil tops $100 as shipping risk rises around Hormuz",
            summary="Energy and logistics costs reprice inflation expectations.",
            event_type="geopolitical",
            final_score=0.8,
            cluster_id="global_story_1",
        )
        briefing = MorningBriefing(
            global_news=[shared_event],
            top_themes=[shared_event],
            watchlist_events=[shared_event],
        )

        generator._dedupe_cross_section_events(briefing)

        assert len(briefing.global_news) == 1
        assert briefing.top_themes == []
        assert briefing.watchlist_events == []


class TestMorningCrossTypeSuppression:
    def test_morning_generator_skips_already_sent_and_continuation_events(self, monkeypatch):
        already_sent = NormalisedEvent(
            title="US warns buyers of Iranian oil could face sanctions",
            summary="Energy and inflation sensitivity rise.",
            source="finnhub",
            event_type="geopolitical",
            final_score=0.9,
            factual_confidence_score=0.85,
            cluster_size=4,
            already_sent=True,
            update_status="duplicate",
        )
        continuation = NormalisedEvent(
            title="Oil tops $100 as shipping risk persists near Hormuz",
            summary="Cross-asset risk continues to reprice.",
            source="finnhub",
            event_type="geopolitical",
            final_score=0.88,
            factual_confidence_score=0.82,
            cluster_size=4,
            already_sent=False,
            update_status="material_update",
        )
        fresh = NormalisedEvent(
            title="Fed official signals one rate cut remains possible in 2026",
            summary="Rates path remains data dependent.",
            source="finnhub",
            event_type="macro_release",
            final_score=0.84,
            factual_confidence_score=0.83,
            cluster_size=3,
            already_sent=False,
            update_status="new",
        )

        monkeypatch.setattr(
            "app.briefing.morning_generator.process_event_stream",
            lambda *_args, **_kwargs: [already_sent, continuation, fresh],
        )

        generator = MorningBriefingGenerator(
            settings=Settings(),
            profile=UserProfile(sector_weights={"technology": 1.0}),
            universe=SectorUniverse(
                sectors=[],
                indices=[InstrumentDef(symbol="SPY", display="S&P 500")],
                macro_instruments=[],
            ),
            market_data=_DummyMarketData(),
            news_data=_DummyNewsDataWithEvents([fresh]),
            macro_data=_DummyMacroData(),
        )

        briefing = generator.generate()
        rendered_titles = {evt.title for evt in briefing.global_news + briefing.top_themes}
        assert "Fed official signals one rate cut remains possible in 2026" in rendered_titles
        assert "US warns buyers of Iranian oil could face sanctions" not in rendered_titles
        assert "Oil tops $100 as shipping risk persists near Hormuz" not in rendered_titles


class TestPhase63BriefingSections:
    def test_generator_populates_regional_and_impact_fields(self, monkeypatch):
        fresh = NormalisedEvent(
            title="Iran reopens Strait of Hormuz",
            summary="Energy shipping risk remains elevated.",
            source="finnhub",
            event_type="geopolitical",
            final_score=0.84,
            factual_confidence_score=0.83,
            cluster_size=3,
            already_sent=False,
            update_status="new",
            tickers=["XOM"],
        )
        monkeypatch.setattr(
            "app.briefing.morning_generator.process_event_stream",
            lambda *_args, **_kwargs: [fresh],
        )

        class _StubMarket:
            def get_quotes(self, symbols):
                rows = []
                for symbol in symbols:
                    if symbol == "^VIX":
                        rows.append(QuoteData(symbol=symbol, display_name="VIX", current_price=19.5, change=2.0, change_percent=11.5))
                    elif symbol == "CL=F":
                        rows.append(QuoteData(symbol=symbol, display_name="WTI Crude Oil (CL1:COM)", current_price=82.0, change=4.9, change_percent=5.9))
                    else:
                        rows.append(QuoteData(symbol=symbol, display_name=symbol, current_price=100.0, change=1.2, change_percent=1.2))
                return rows

        class _StubMacro:
            def get_morning_macro(self):
                return [MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.3, change=0.03)]

            def get_treasury_yields(self):
                return (
                    MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.3, change=0.03),
                    MacroDataPoint(series_id="UST2Y", name="US 2Y Treasury Yield", value=3.8, change=0.02),
                )

            def get_ecb_snapshot(self):
                return []

            def get_eurostat_snapshot(self):
                return []

        generator = MorningBriefingGenerator(
            settings=Settings(enable_charts=False),
            profile=UserProfile(
                watchlist_primary=["XOM"],
                delivery={"morning_channels": ["telegram"]},
            ),
            universe=SectorUniverse(
                sectors=[],
                indices=[
                    InstrumentDef(symbol="^GSPC", display="S&P 500 (SPX)"),
                    InstrumentDef(symbol="^STOXX50E", display="EURO STOXX 50"),
                    InstrumentDef(symbol="^N225", display="Nikkei 225"),
                    InstrumentDef(symbol="^VIX", display="VIX"),
                ],
                macro_instruments=[InstrumentDef(symbol="CL=F", display="WTI Crude Oil (CL1:COM)")],
            ),
            market_data=_StubMarket(),
            news_data=_DummyNewsDataWithEvents([fresh]),
            macro_data=_StubMacro(),
        )
        briefing = generator.generate()
        assert briefing.regional_lens
        assert briefing.regional_skew_summary.startswith("Regional skew:")
        assert briefing.portfolio_impact_bullets
        assert briefing.portfolio_action_posture
        assert briefing.regime_context
        assert briefing.positioning_alignment
