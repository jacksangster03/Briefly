"""Morning briefing generator: orchestrates data fetching, processing,
and assembly of the pre-market morning briefing.
"""

from __future__ import annotations

from datetime import datetime
import re

from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.global_news_selector import select_global_market_events
from app.briefing.market_setup_interpreter import interpret_market_setup
from app.briefing.portfolio_impact import build_portfolio_impact
from app.briefing.regime_context import build_regime_context, compute_geo_risk_level
from app.briefing.regime_tracker import classify_regime, persist_regime_snapshot
from app.briefing.session_quality import compute_session_quality
from app.briefing.trust_contract import (
    active_index_quotes,
    freshness_block,
    resolve_canonical_prices,
    run_pre_send_lints,
    section_confidence,
)
from app.briefing.regional_lens import build_regional_lens
from app.briefing.theme_builder import build_top_themes
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.article_quality import (
    classify_article_type,
    classify_source_quality,
    is_low_quality_for_section,
)
from app.processing.pipeline import is_actionable_event, process_event_stream
from app.schemas.briefings import MarketSetup, MorningBriefing, session_mode_for
from app.schemas.events import EarningsEvent, MacroDataPoint, NormalisedEvent, QuoteData, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse
from app.universe.ticker_metadata import TICKER_DISPLAY_NAMES, company_name_for_ticker

logger = get_logger("morning_gen")

MACRO_BLEED_TERMS = (
    "powell",
    "fed",
    "inflation",
    "treasury",
    "yield",
    "oil",
    "crude",
    "hormuz",
    "iran",
    "geopolitical",
    "ceasefire",
    "opec",
    "middle east",
    "war",
)
MAX_PORTFOLIO_FOCUS = 5
MAX_GLOBAL_NEWS = 6
PORTFOLIO_DIRECT_MIN_SCORE = 2.0
PORTFOLIO_READTHROUGH_MIN_SCORE = 1.6
TRUST_TOP_THEMES_MIN_SCORE = 1.0
TRUST_SECTOR_SCAN_MIN_SCORE = 0.9
PORTFOLIO_CATALYST_EVENT_TYPES = {
    "earnings",
    "guidance",
    "fda_decision",
    "m_and_a",
    "current_report",
    "annual_report",
    "quarterly_report",
    "ownership_disclosure",
    "macro_release",
    "fed_decision",
    "geopolitical",
    "regulatory",
    "filing",
}
PORTFOLIO_CATALYST_TERMS = (
    "earnings",
    "guidance",
    "outlook",
    "forecast",
    "sec filing",
    "8-k",
    "10-q",
    "10-k",
    "fda",
    "approval",
    "investigation",
    "probe",
    "settlement",
    "contract",
    "order",
    "partnership",
    "acquisition",
    "merger",
    "capex",
    "production",
    "output",
    "supply",
    "demand",
    "pipeline",
    "hormuz",
    "tariff",
    "sanction",
    "recall",
    "launch",
)
PORTFOLIO_READTHROUGH_TERMS = (
    "inflation",
    "cpi",
    "ppi",
    "jobs",
    "payroll",
    "jobless",
    "fomc",
    "powell",
    "federal reserve",
    "rate cut",
    "rate hike",
    "treasury yield",
    "oil",
    "crude",
    "hormuz",
    "opec",
    "sanction",
    "ceasefire",
    "tariff",
    "export restriction",
    "supply chain",
)
PORTFOLIO_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "dirt cheap",
    "outraged",
)
PORTFOLIO_SOFT_PENALTY_PATTERNS = (
    "analysts love",
    "can't agree on a direction",
    "stock is a buy",
    "stock is cheap",
    "betting against",
    "name-drops",
    "says he owns",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
)
PORTFOLIO_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
WATCHLIST_MIN_EDITORIAL_SCORE = 1.0
WATCHLIST_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "your friends will find out and it will be embarrassing",
)
WATCHLIST_SOFT_PENALTY_PATTERNS = (
    "dirt cheap",
    "analysts love",
    "can't agree on a direction",
    "betting against",
    "name-drops",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
    "outraged",
)
WATCHLIST_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
TRUST_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "your friends will find out and it will be embarrassing",
)
TRUST_SOFT_PENALTY_PATTERNS = (
    "dirt cheap",
    "analysts love",
    "can't agree on a direction",
    "betting against",
    "name-drops",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
    "outraged",
)
TRUST_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
WEEKEND_LOW_TRUST_SOURCES = (
    "motley fool",
    "fool.com",
    "investorplace",
    "benzinga",
    "zacks",
    "thestreet",
)
TRUST_MARKET_LINK_TERMS = (
    "inflation",
    "rates",
    "yield",
    "treasury",
    "fed",
    "ecb",
    "fomc",
    "oil",
    "crude",
    "gold",
    "dollar",
    "fx",
    "earnings",
    "guidance",
    "sanction",
    "tariff",
    "hormuz",
    "iran",
    "ceasefire",
    "shipping",
    "blockade",
    "geopolitical",
)
TRUST_PERSONAL_FINANCE_PATTERNS = (
    "best cd rates",
    "apy",
    "high-yield savings",
    "checking account",
    "personal finance",
)


def _compute_earnings_surprise(event: EarningsEvent) -> EarningsEvent:
    """Compute surprise_percent when estimate and actual are both available."""
    if (
        event.surprise_percent is None
        and event.eps_estimate is not None
        and event.eps_actual is not None
        and event.eps_estimate != 0.0
    ):
        surprise = (event.eps_actual - event.eps_estimate) / abs(event.eps_estimate) * 100
        event.surprise_percent = round(surprise, 2)
    return event


def enrich_earnings_from_yfinance(events: list[EarningsEvent]) -> list[EarningsEvent]:
    """Backfill missing earnings estimates/actuals from yfinance where possible."""
    try:
        import yfinance as yf
    except ImportError:
        return events

    needs_enrich = [event for event in events if event.eps_estimate is None or event.eps_actual is None]
    symbols = list({event.symbol for event in needs_enrich if event.symbol})
    if not symbols:
        return events

    def _fetch_one(symbol: str):
        try:
            df = yf.Ticker(symbol).earnings_dates
            return symbol, df if df is not None and not df.empty else None
        except Exception:
            return symbol, None

    from concurrent.futures import ThreadPoolExecutor, as_completed
    yf_data: dict[str, object] = {}
    max_workers = min(8, len(symbols))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, df = future.result()
            yf_data[sym] = df

    for event in events:
        df = yf_data.get(event.symbol)
        if df is None:
            _compute_earnings_surprise(event)
            continue
        try:
            for date_idx, row in df.iterrows():
                date_str = str(date_idx)[:10]
                if date_str != event.report_date:
                    continue
                if event.eps_estimate is None:
                    estimate = row["EPS Estimate"]
                    if estimate is not None and str(estimate) != "nan":
                        event.eps_estimate = float(estimate)
                if event.eps_actual is None:
                    actual = row["Reported EPS"]
                    if actual is not None and str(actual) != "nan":
                        event.eps_actual = float(actual)
                break
        except Exception:
            pass
        _compute_earnings_surprise(event)

    return events


class MorningBriefingGenerator:
    """Builds the morning briefing by fetching data from all services,
    processing events, and assembling the final briefing object.
    """

    def __init__(
        self,
        settings: Settings,
        profile: UserProfile,
        universe: SectorUniverse,
        market_data: MarketDataService,
        news_data: NewsDataService,
        macro_data: MacroDataService,
    ):
        self.settings = settings
        self.profile = profile
        self.universe = universe
        self.market_svc = market_data
        self.news_svc = news_data
        self.macro_svc = macro_data
        self.rules = load_alert_rules(settings).morning

    def generate(self) -> MorningBriefing:
        """Generate the full morning briefing."""
        logger.info("Generating morning briefing...")
        now = datetime.now()
        briefing = MorningBriefing(
            generated_at=now,
            session_mode=session_mode_for(now),
        )

        # 1. Market setup (quotes for indices + macro instruments)
        briefing.market_setup = self._build_market_setup()

        # 2. Macro context: FRED core + ECB/Eurostat when configured
        briefing.macro_context = self.macro_svc.get_morning_macro()
        briefing.macro_context.extend(self.macro_svc.get_ecb_snapshot())
        briefing.macro_context.extend(self.macro_svc.get_eurostat_snapshot())
        try:
            briefing.commodity_strip = self.macro_svc.get_commodity_strip()
            gold_q = self.market_svc.get_quote("GC=F")
            if gold_q and gold_q.current_price > 0:
                prev = gold_q.previous_close or None
                briefing.commodity_strip.insert(0, MacroDataPoint(
                    series_id="GC=F",
                    name="Gold (USD/troy oz)",
                    value=round(gold_q.current_price, 2),
                    previous_value=round(prev, 2) if prev else None,
                    change=round(gold_q.change, 2) if gold_q.change is not None else None,
                    change_percent=round(gold_q.change_percent, 4) if gold_q.change_percent is not None else None,
                    date=gold_q.timestamp.strftime("%Y-%m-%d"),
                    source="yfinance",
                ))
        except Exception:
            briefing.commodity_strip = []
        # Reconcile: override FRED commodity values with live yfinance prices where available.
        # This eliminates the T+1 lag conflict between FRED settlement and live futures prices.
        self._reconcile_commodity_sources(briefing)
        ten_y, two_y = self.macro_svc.get_treasury_yields()
        briefing.market_setup.treasury_10y = ten_y
        briefing.market_setup.treasury_2y = two_y
        briefing.canonical_prices = resolve_canonical_prices(briefing)
        active_setup = self._active_market_setup_view(briefing)
        setup_interpretation = interpret_market_setup(active_setup, briefing.macro_context)
        briefing.market_setup_analysis = setup_interpretation.narrative
        briefing.dominant_tape_driver = setup_interpretation.dominant_driver
        briefing.market_setup_analysis_confidence = setup_interpretation.confidence
        briefing.market_setup_signal_tags = setup_interpretation.tags

        # 3. Fetch all news/events
        all_events = self.news_svc.fetch_all(
            watchlist=self.profile.all_watchlist_tickers[:20]  # limit API calls
        )
        briefing.events_fetched = len(all_events)

        # 4. Process, cluster, classify, and rank
        # Sector enrichment runs inside the pipeline after ticker resolution
        # so sector tags reflect the cleaned ticker set, not spurious feeds.
        scored = process_event_stream(
            all_events,
            self.profile,
            self.settings,
            sector_lookup=self.universe.sectors_for_ticker,
        )
        briefing.events_after_dedup = len(scored)
        eligible = [event for event in scored if not event.already_sent and event.update_status == "new"]

        # 7. Assemble sections
        briefing.global_news = self._build_global_news(eligible, briefing.session_mode)
        briefing.top_themes = self._build_top_themes(eligible, briefing.session_mode)
        briefing.portfolio_focus = self._build_portfolio_focus(eligible)
        briefing.sector_scan = self._build_sector_scan(eligible, briefing.session_mode)
        briefing.earnings_calendar = self._fetch_earnings()
        briefing.earnings_relevance = self._build_earnings_relevance(briefing.earnings_calendar)
        briefing.watchlist_events = self._filter_watchlist_events(eligible)
        briefing.watchlist_quotes = self._fetch_watchlist_quotes()
        briefing.portfolio_quotes = self._fetch_portfolio_quotes()
        active_setup = self._active_market_setup_view(briefing)
        setup_interpretation = interpret_market_setup(
            active_setup,
            briefing.macro_context,
            global_news=briefing.global_news,
        )
        briefing.market_setup_analysis = setup_interpretation.narrative
        briefing.dominant_tape_driver = setup_interpretation.dominant_driver
        briefing.market_setup_analysis_confidence = setup_interpretation.confidence
        briefing.market_setup_signal_tags = setup_interpretation.tags

        geo_level, geo_raw_level, geo_summary = self._build_geo_risk_meter(briefing)
        briefing.geo_risk_level = geo_level
        briefing.geo_risk_raw_level = geo_raw_level
        briefing.geo_risk_summary = geo_summary
        regime_snapshot, regime_shift = self._track_regime_snapshot(briefing)
        briefing.regime_snapshot = regime_snapshot
        briefing.regime_shift = regime_shift
        sq = compute_session_quality(
            market_breadth=briefing.market_setup.market_breadth,
            index_quotes=active_setup.index_quotes,
            macro_context=briefing.macro_context,
            commodity_strip=briefing.commodity_strip,
            geo_risk_level=briefing.geo_risk_level,
        )
        briefing.session_quality_score = round(sq.score, 4)
        briefing.session_quality_bucket = sq.bucket
        briefing.session_quality_color_hex = sq.color_hex
        briefing.session_quality_label = sq.label
        regional_lens, regional_skew = build_regional_lens(
            index_quotes=active_setup.index_quotes,
            global_news=briefing.global_news,
        )
        briefing.regional_lens = regional_lens
        briefing.regional_skew_summary = regional_skew
        impact_bullets, action_posture = build_portfolio_impact(
            profile=self.profile,
            global_news=briefing.global_news,
            top_themes=briefing.top_themes,
            portfolio_focus=briefing.portfolio_focus,
            setup_tags=briefing.market_setup_signal_tags,
        )
        briefing.portfolio_impact_bullets = impact_bullets
        briefing.portfolio_action_posture = action_posture
        regime_context, alignment = build_regime_context(
            profile_name=self.profile.name,
            current_setup_tags=briefing.market_setup_signal_tags,
        )
        briefing.regime_context = regime_context
        briefing.positioning_alignment = alignment
        self._dedupe_cross_section_events(briefing)
        if self._should_build_charts():
            chart_briefing = briefing.model_copy(deep=True)
            chart_briefing.market_setup = active_setup.model_copy(deep=True)
            briefing.chart_assets = MorningChartBuilder(
                profile=self.profile,
                market_data=self.market_svc,
                macro_data_svc=self.macro_svc,
            ).build(chart_briefing)
            briefing.morning_chart_bundle = chart_briefing.morning_chart_bundle
            briefing.morning_chart_selection = chart_briefing.morning_chart_selection
        # Final contract checks before delivery rendering.
        briefing.canonical_prices = resolve_canonical_prices(briefing)
        briefing.contract_warnings = run_pre_send_lints(
            briefing,
            timezone_name=self.profile.timezone,
        )
        briefing.section_confidence = section_confidence(
            briefing,
            timezone_name=self.profile.timezone,
        )
        briefing.data_freshness = freshness_block(
            briefing,
            timezone_name=self.profile.timezone,
        )
        sent_ids: set[str] = set()
        for event in (
            briefing.global_news
            + briefing.top_themes
            + briefing.watchlist_events
            + briefing.portfolio_focus
        ):
            sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        for snapshot in briefing.sector_scan:
            for event in snapshot.top_events:
                sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        briefing.events_sent = len(sent_ids)

        logger.info(
            "Morning briefing ready: %d fetched, %d deduped, %d themes, %d sectors, %d watchlist",
            briefing.events_fetched,
            briefing.events_after_dedup,
            len(briefing.top_themes),
            len(briefing.sector_scan),
            len(briefing.watchlist_events),
        )
        return briefing

    # -- Section builders -----------------------------------------------------

    def _build_market_setup(self) -> MarketSetup:
        """Fetch quotes for indices and macro instruments."""
        setup = MarketSetup()

        # Index quotes
        index_symbols = self.universe.all_index_symbols
        if index_symbols:
            quotes = self.market_svc.get_quotes(index_symbols)
            # Attach display names from universe
            name_map = {i.symbol: i.display for i in self.universe.indices}
            for q in quotes:
                q.display_name = name_map.get(q.symbol, q.symbol)
            setup.index_quotes = quotes

        # Macro instrument quotes (gold, oil, USD, BTC)
        macro_symbols = self.universe.all_macro_symbols
        if macro_symbols:
            quotes = self.market_svc.get_quotes(macro_symbols)
            name_map = {m.symbol: m.display for m in self.universe.macro_instruments}
            for q in quotes:
                q.display_name = name_map.get(q.symbol, q.symbol)
            setup.macro_quotes = quotes

        # Breadth proxy (index + sectors) from yfinance fallback provider.
        yfinance = getattr(self.market_svc, "yfinance", None)
        if yfinance and yfinance.is_configured():
            breadth_rows = []
            try:
                # S&P 500 proxy breadth context (volume vs 20D average).
                core = yfinance.get_index_breadth("SPY")
                if core is not None:
                    core.display_name = core.display_name or "S&P 500 Proxy Breadth (SPY)"
                    breadth_rows.append(core)
            except Exception:
                pass
            try:
                breadth_rows.extend(yfinance.get_sector_breadth())
            except Exception:
                pass
            setup.market_breadth = breadth_rows

        return setup

    def _build_top_themes(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str,
    ) -> list[NormalisedEvent]:
        """Build top themes with an editorial trust gate."""
        preferred_symbols = {
            symbol.upper()
            for symbol in (
                list(self.profile.portfolio_symbols)
                + list(self.profile.all_watchlist_tickers)
            )
            if symbol
        }
        return build_top_themes(
            scored_events,
            max_themes=self.rules.max_themes,
            editorial_gate=lambda evt: self._is_editorially_trustworthy(
                evt,
                session_mode,
                section="top_themes",
            ),
            preferred_symbols=preferred_symbols,
        )

    def _build_global_news(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str,
    ) -> list[NormalisedEvent]:
        """Select high-trust, market-linked global/geopolitical stories."""
        return select_global_market_events(
            scored_events,
            session_mode=session_mode,
            max_items=MAX_GLOBAL_NEWS,
            region_weights=self.profile.coverage_weights,
            actionable_check=is_actionable_event,
        )

    def _build_sector_scan(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str = "weekday",
    ) -> list[SectorSnapshot]:
        """Build sector snapshots with ETF quotes and top events per sector.

        Each event is placed in only one sector: the first matching sector
        in the user's weighted sector order. This prevents the same story
        from appearing in multiple sections (e.g. an Apple/JPMorgan AI
        partnership showing up under both Tech and Financials).
        """
        etf_symbols = self.universe.all_sector_etfs
        etf_quotes = {}
        if etf_symbols:
            for q in self.market_svc.get_quotes(etf_symbols):
                etf_quotes[q.symbol] = q

        weighted_sectors = sorted(
            self.universe.sectors,
            key=lambda s: self.profile.sector_weights.get(s.key, 0.3),
            reverse=True,
        )

        placed_event_ids: set[str] = set()
        snapshots = []
        for sector in weighted_sectors[:12]:
            sector_tickers = set(sector.key_names)
            sector_events = []
            for event in scored_events:
                if event.event_id in placed_event_ids:
                    continue
                if not is_actionable_event(event):
                    continue
                ticker_matches = sum(1 for ticker in event.tickers if ticker in sector_tickers)
                has_sector_tag = sector.key in event.sectors
                if not (ticker_matches or has_sector_tag):
                    continue
                if self._looks_like_macro_bleed(event) and ticker_matches < 2 and event.source != "sec_edgar":
                    continue
                if not self._is_editorially_trustworthy(event, session_mode, section="sector_scan"):
                    continue
                sector_events.append(event)
                placed_event_ids.add(event.event_id)

            etf_quote = etf_quotes.get(sector.etf)
            snap = SectorSnapshot(
                sector_key=sector.key,
                display_name=sector.display_name,
                etf_symbol=sector.etf,
                etf_quote=etf_quote,
                top_events=sector_events[: self.rules.max_sector_events],
            )
            snapshots.append(snap)

        return snapshots

    @staticmethod
    def _looks_like_macro_bleed(event: NormalisedEvent) -> bool:
        text = f"{event.title} {event.summary}".lower()
        return any(term in text for term in MACRO_BLEED_TERMS)

    def _fetch_earnings(self):
        """Fetch upcoming earnings, enrich with relevance/sector/cap, and filter.

        Keeps only events where the symbol is portfolio/watchlist relevant OR
        sits in the curated large-cap universe. Microcaps and unknowns are
        dropped before assembly so the briefing never carries noise.
        """
        if not self.news_svc.finnhub or not self.news_svc.finnhub.is_configured():
            return []

        # Pull a wider window than max_earnings since filtering will trim it.
        raw = self.news_svc.finnhub.get_earnings_calendar(days_ahead=7)

        portfolio = {h.symbol.upper() for h in self.profile.portfolio_holdings}
        watchlist = {s.upper() for s in self.profile.all_watchlist_tickers}
        large_cap_universe = set(TICKER_DISPLAY_NAMES.keys())

        ticker_to_sector: dict[str, str] = {}
        for sector_def in self.universe.sectors:
            for ticker in getattr(sector_def, "key_names", []) or []:
                ticker_to_sector.setdefault(ticker.upper(), sector_def.display_name)

        enriched: list[EarningsEvent] = []
        for event in raw:
            sym = (event.symbol or "").upper()
            if not sym:
                continue
            if sym in portfolio:
                event.is_relevant = True
                event.relevance_tag = "portfolio"
            elif sym in watchlist:
                event.is_relevant = True
                event.relevance_tag = "watchlist"
            if not event.company_name:
                event.company_name = company_name_for_ticker(sym)
            event.sector = ticker_to_sector.get(sym, event.sector or "")
            if sym in large_cap_universe:
                event.market_cap_bucket = "large"
            if event.is_relevant or event.market_cap_bucket == "large":
                enriched.append(event)

        enriched = enriched[: self.rules.max_earnings]
        enriched = [_compute_earnings_surprise(event) for event in enriched]

        prior_surprise_cache: dict[str, float | None] = {}
        finnhub = self.news_svc.finnhub
        if finnhub and finnhub.is_configured():
            for event in enriched:
                if event.prior_quarter_surprise_pct is not None:
                    continue
                symbol = (event.symbol or "").upper()
                if not symbol:
                    continue
                if symbol not in prior_surprise_cache:
                    prior_surprise_cache[symbol] = finnhub.get_prior_quarter_surprise_pct(symbol)
                event.prior_quarter_surprise_pct = prior_surprise_cache[symbol]

        missing = [
            event for event in enriched
            if event.is_relevant and (event.eps_estimate is None or event.eps_actual is None)
        ]
        if missing:
            enrich_earnings_from_yfinance(missing)

        return enriched

    def _track_regime_snapshot(self, briefing: MorningBriefing) -> tuple[dict[str, str], dict[str, str]]:
        vix_level = None
        for quote in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "vix" in text:
                vix_level = float(quote.current_price or 0.0)
                break
        oil_delta = 0.0
        for quote in briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "wti" in text or "crude" in text:
                oil_delta = float(quote.change_percent or 0.0)
                break
        ten_y_delta_bps = None
        if briefing.market_setup.treasury_10y and briefing.market_setup.treasury_10y.change is not None:
            ten_y_delta_bps = float(briefing.market_setup.treasury_10y.change) * 100.0
        breadth_ratio = None
        if briefing.market_setup.market_breadth:
            positive = sum(1 for row in briefing.market_setup.market_breadth if float(row.change_percent or 0.0) > 0.0)
            breadth_ratio = positive / max(1, len(briefing.market_setup.market_breadth))

        decision = classify_regime(
            setup_tags=briefing.market_setup_signal_tags,
            vix_level=vix_level,
            breadth_ratio=breadth_ratio,
            oil_delta_pct=oil_delta,
            ten_y_delta_bps=ten_y_delta_bps,
        )
        shift = persist_regime_snapshot(
            profile_name=self.profile.name,
            decision=decision,
            setup_tags=briefing.market_setup_signal_tags,
            vix_level=vix_level,
            geo_risk_level=briefing.geo_risk_level,
        )
        snapshot = {
            "risk_regime": decision.risk_regime,
            "trend_regime": decision.trend_regime,
            "factor_regime": decision.factor_regime,
        }
        return snapshot, shift

    def _active_market_setup_view(self, briefing: MorningBriefing) -> MarketSetup:
        """Copy market setup and exclude closed benchmarks from active breadth logic."""
        return briefing.market_setup.model_copy(
            update={
                "index_quotes": active_index_quotes(
                    briefing,
                    timezone_name=self.profile.timezone,
                )
            }
        )

    def _reconcile_commodity_sources(self, briefing: MorningBriefing) -> None:
        """Override FRED commodity strip values with live yfinance quotes where available.

        FRED oil/gas series are T+1 settlement prices; the live macro quotes from
        yfinance reflect current futures. Using the live price everywhere ensures the
        market-setup table, commodity strip, geo-risk meter, and session quality score
        all show the same WTI/Brent value.

        Gold is already inserted from yfinance as the first commodity strip element
        and is left untouched here.
        """
        live_wti: QuoteData | None = None
        live_brent: QuoteData | None = None
        for q in briefing.market_setup.macro_quotes:
            label = (q.display_name or q.symbol or "").upper()
            if ("WTI" in label or "CRUDE" in label) and "BRENT" not in label and q.current_price:
                live_wti = q
            elif "BRENT" in label and q.current_price:
                live_brent = q

        today = briefing.generated_at.strftime("%Y-%m-%d")
        for i, pt in enumerate(briefing.commodity_strip):
            pt_key = ((pt.name or "") + " " + (pt.series_id or "")).upper()
            live_q: QuoteData | None = None
            if ("WTI" in pt_key or "DCOILWTICO" in pt_key) and "BRENT" not in pt_key:
                live_q = live_wti
            elif "BRENT" in pt_key or "DCOILBRENTEU" in pt_key:
                live_q = live_brent
            # Gold (GC=F) is already yfinance-sourced — skip
            if live_q is not None and live_q.current_price > 0:
                briefing.commodity_strip[i] = MacroDataPoint(
                    series_id=pt.series_id,
                    name=pt.name,
                    value=round(live_q.current_price, 2),
                    change=round(live_q.change, 2) if live_q.change is not None else None,
                    change_percent=round(live_q.change_percent, 4) if live_q.change_percent is not None else None,
                    date=today,
                    source="yfinance_live",
                )

    def _build_geo_risk_meter(self, briefing: MorningBriefing) -> tuple[str, str, str]:
        from app.briefing.regime_context import _GEO_LEVEL_ORDER

        vix_level = None
        for quote in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "vix" in text:
                vix_level = float(quote.current_price or 0.0)
                break

        oil_delta = 0.0
        oil_level = 0.0
        gold_delta = 0.0
        usd_delta = 0.0
        for quote in briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "wti" in text or "crude" in text:
                oil_delta = float(quote.change_percent or 0.0)
                oil_level = float(quote.current_price or 0.0)
            elif "gold" in text:
                gold_delta = float(quote.change_percent or 0.0)
            elif "usd" in text or "dollar" in text or "dxy" in text:
                usd_delta = float(quote.change_percent or 0.0)
        # Prefer FRED commodity strip for oil level (more reliable than live quote)
        for pt in briefing.commodity_strip:
            key = (pt.name or pt.series_id or "").upper()
            if "WTI" in key or "DCOILWTICO" in key:
                oil_level = oil_level or float(pt.value or 0.0)
                break

        safe_haven_strength = max(0.0, gold_delta) + max(0.0, usd_delta)
        geo_terms = ("iran", "israel", "hormuz", "blockade", "missile", "ceasefire", "sanction", "shipping", "war", "attack", "strike", "invasion")
        events = briefing.global_news + briefing.top_themes

        # Pass density=None when no events; compute_geo_risk_level will label signal as stale
        density: float | None
        if not events:
            density = None
            has_geo_headlines = False
        else:
            geo_hits = 0
            has_geo_headlines = False
            for event in events:
                etext = f"{event.title} {event.summary}".lower()
                if any(term in etext for term in geo_terms):
                    geo_hits += max(1, int(event.cluster_size or 1))
                    has_geo_headlines = True
            density = geo_hits / max(1.0, float(sum(max(1, int(evt.cluster_size or 1)) for evt in events)))

        raw_level, summary = compute_geo_risk_level(
            vix_level=vix_level,
            oil_delta_pct=oil_delta,
            safe_haven_strength=safe_haven_strength,
            news_keyword_density=density,
        )
        level = raw_level

        # Floor rule: oil elevated + active geo headlines → at least ELEVATED, never LOW/MODERATE
        oil_is_elevated = oil_level > 90 or oil_delta >= 2.0
        if has_geo_headlines and oil_is_elevated:
            floor = "ELEVATED"
            try:
                if _GEO_LEVEL_ORDER.index(level) < _GEO_LEVEL_ORDER.index(floor):
                    level = floor
                    # Build a clean replacement summary — do not concatenate the raw model summary
                    vix_note = f"VIX {vix_level:.1f}" if vix_level else "VIX n/a"
                    haven_note = "haven neutral" if safe_haven_strength < 0.5 else f"haven +{safe_haven_strength:.2f}"
                    density_note = f"density {density:.2f}" if density is not None else "headline signal stale"
                    summary = (
                        f"Geo risk ELEVATED: oil {oil_level:.0f} USD/bbl ({oil_delta:+.2f}%) "
                        f"with active Middle East/geopolitical headlines; "
                        f"{vix_note} and {haven_note} do not confirm broad panic. "
                        f"Inputs: {vix_note}, oil {oil_delta:+.2f}%, {haven_note}, {density_note}."
                    )
            except ValueError:
                pass  # level not in order list (e.g. N/A) — leave unchanged

        return level, raw_level, summary

    def _build_earnings_relevance(self, earnings: list) -> dict[str, str]:
        portfolio = {holding.symbol.upper() for holding in self.profile.portfolio_holdings}
        watchlist = {symbol.upper() for symbol in self.profile.all_watchlist_tickers}
        relevance: dict[str, str] = {}
        for item in earnings:
            symbol = str(getattr(item, "symbol", "") or "").upper()
            if not symbol:
                continue
            if symbol in portfolio:
                relevance[symbol] = "portfolio"
            elif symbol in watchlist:
                relevance[symbol] = "watchlist"
        return relevance

    def _build_portfolio_focus(self, scored_events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Select concise portfolio-first items for the morning briefing."""
        if not self.profile.has_portfolio:
            return []

        portfolio_symbols = set(self.profile.portfolio_symbols)
        concentrated_sectors = {
            sector
            for sector, weight in self.profile.portfolio_sector_weights.items()
            if weight >= 0.2
        }
        selected: list[NormalisedEvent] = []
        seen_tracking_ids: set[str] = set()

        def _push(event: NormalisedEvent) -> None:
            tracking_id = event.cluster_id or event.content_hash or event.event_id
            if tracking_id in seen_tracking_ids:
                return
            seen_tracking_ids.add(tracking_id)
            selected.append(event)

        for event in scored_events:
            if len(selected) >= MAX_PORTFOLIO_FOCUS:
                break
            if not is_actionable_event(event):
                continue
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=True):
                _push(event)

        for event in scored_events:
            if len(selected) >= MAX_PORTFOLIO_FOCUS:
                break
            if not is_actionable_event(event):
                continue
            if event.final_score < 0.62:
                continue
            if not (concentrated_sectors and any(sector in concentrated_sectors for sector in event.sectors)):
                continue
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=False):
                _push(event)

        return selected

    @staticmethod
    def _normalise_text(value: str) -> str:
        return (
            (value or "")
            .lower()
            .replace("’", "'")
            .replace("‘", "'")
            .replace("`", "'")
            .replace("—", "-")
            .replace("–", "-")
            .replace("\xa0", " ")
        )

    def _portfolio_focus_worthy(
        self,
        event: NormalisedEvent,
        portfolio_symbols: set[str],
        *,
        require_direct: bool,
    ) -> bool:
        score, has_direct, has_readthrough = self._portfolio_focus_editorial_score(
            event,
            portfolio_symbols,
        )
        if require_direct and not has_direct:
            return False
        if require_direct:
            return score >= PORTFOLIO_DIRECT_MIN_SCORE
        return has_readthrough and score >= PORTFOLIO_READTHROUGH_MIN_SCORE

    def _portfolio_focus_editorial_score(
        self,
        event: NormalisedEvent,
        portfolio_symbols: set[str],
    ) -> tuple[float, bool, bool]:
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        held_hits = [ticker for ticker in event.tickers if ticker in portfolio_symbols]
        has_direct = bool(held_hits)
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        has_readthrough = self._has_portfolio_readthrough_signal(event, text_lower)
        title_mentions_held = self._title_mentions_tickers_or_companies(title_lower, held_hits)

        # Held ticker appeared only indirectly (often in a side mention): reject
        # unless the event carries a hard catalyst.
        if has_direct and not title_mentions_held and not has_catalyst:
            return -99.0, has_direct, has_readthrough

        # Keep explicit low-value framing out of scarce PORTFOLIO FOCUS slots,
        # unless there is a genuinely material catalyst.
        if any(pattern in text_lower for pattern in PORTFOLIO_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return -99.0, has_direct, has_readthrough

        score = 0.0
        if has_direct:
            score += 1.8
        if title_mentions_held:
            score += 0.8
        if has_catalyst:
            score += 1.2
        if has_readthrough:
            score += 0.6
        if event.cluster_size >= 3:
            score += 0.2

        for pattern in PORTFOLIO_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.65
        if any(rx.search(title_lower) for rx in PORTFOLIO_SOFT_PENALTY_REGEXES):
            score -= 0.8
        if "?" in title_lower and not has_catalyst:
            score -= 0.35

        return score, has_direct, has_readthrough

    @staticmethod
    def _is_material_portfolio_catalyst(event: NormalisedEvent, text_lower: str) -> bool:
        if event.source == "sec_edgar":
            return True
        if event.event_type in PORTFOLIO_CATALYST_EVENT_TYPES:
            return True
        return any(term in text_lower for term in PORTFOLIO_CATALYST_TERMS)

    @staticmethod
    def _has_portfolio_readthrough_signal(event: NormalisedEvent, text_lower: str) -> bool:
        if event.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory"}:
            return True
        return any(term in text_lower for term in PORTFOLIO_READTHROUGH_TERMS)

    @staticmethod
    def _title_mentions_tickers_or_companies(title_lower: str, tickers: list[str]) -> bool:
        for ticker in tickers:
            if re.search(rf"\b{re.escape(ticker.lower())}\b", title_lower):
                return True
            company = company_name_for_ticker(ticker).lower()
            if company != ticker.lower() and company in title_lower:
                return True
        return False

    def _filter_watchlist_events(self, scored: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Filter events relevant to the user's watchlist."""
        watchlist_set = set(self.profile.all_watchlist_tickers)
        selected: list[NormalisedEvent] = []
        seen_tracking_ids: set[str] = set()
        for event in scored:
            if len(selected) >= self.rules.max_watchlist_events:
                break
            if not is_actionable_event(event):
                continue
            watchlist_hits = [ticker for ticker in event.tickers if ticker in watchlist_set]
            if not watchlist_hits:
                continue
            if not self._watchlist_event_worthy(event, watchlist_hits):
                continue

            tracking_id = event.cluster_id or event.content_hash or event.event_id
            if tracking_id in seen_tracking_ids:
                continue
            seen_tracking_ids.add(tracking_id)
            selected.append(event)
        return selected

    def _watchlist_event_worthy(
        self,
        event: NormalisedEvent,
        watchlist_hits: list[str],
    ) -> bool:
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        title_mentions_watchlist = self._title_mentions_tickers_or_companies(title_lower, watchlist_hits)

        if not title_mentions_watchlist and not has_catalyst:
            return False

        if any(pattern in text_lower for pattern in WATCHLIST_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return False

        score = 0.0
        score += 1.0  # direct watchlist hit
        if title_mentions_watchlist:
            score += 0.5
        if has_catalyst:
            score += 0.8
        if event.cluster_size >= 3:
            score += 0.2
        if event.source == "sec_edgar":
            score += 0.3

        for pattern in WATCHLIST_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.4
        if any(rx.search(title_lower) for rx in WATCHLIST_SOFT_PENALTY_REGEXES):
            score -= 0.6
        if "?" in title_lower and not has_catalyst:
            score -= 0.3

        return score >= WATCHLIST_MIN_EDITORIAL_SCORE

    def _is_editorially_trustworthy(
        self,
        event: NormalisedEvent,
        session_mode: str,
        *,
        section: str,
    ) -> bool:
        """Apply section-level trust gating for top themes and sector scan."""
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        source_name = self._normalise_text(str(event.raw_data.get("source_name", "")))
        url_lower = self._normalise_text(event.url)
        is_weekend = session_mode in {"saturday", "sunday"}
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        has_readthrough = self._has_portfolio_readthrough_signal(event, text_lower)

        portfolio_symbols = {s.upper() for s in self.profile.portfolio_symbols}
        watchlist_symbols = {s.upper() for s in self.profile.all_watchlist_tickers}
        relevant_symbols = portfolio_symbols | watchlist_symbols
        has_relevant_ticker = any(t.upper() in relevant_symbols for t in event.tickers)

        article_type = classify_article_type(event.title, event.summary, event.url)
        source_quality = classify_source_quality(
            event.source,
            str(event.raw_data.get("source_name", "")),
            event.url,
        )
        trusted_source = source_quality in {"tier1_wire", "tier1_press", "sec_filing"}

        if section == "top_themes":
            # Hard-stop low-signal editorial formats unless they are directly relevant
            # and strongly corroborated by a trusted source.
            if article_type in {"listicle", "seo", "opinion"}:
                if not (has_relevant_ticker and has_catalyst and trusted_source):
                    return False
            if article_type == "preview" and not has_relevant_ticker:
                return False
            if source_quality == "blog" and not (has_relevant_ticker and has_catalyst):
                return False
            if (
                not has_relevant_ticker
                and not trusted_source
                and event.cluster_size < 3
                and not has_catalyst
            ):
                return False
            if event.event_type == "news_search" and not has_relevant_ticker and not has_catalyst:
                return False

        if (
            is_low_quality_for_section(article_type, source_quality)
            and not has_relevant_ticker
            and not has_catalyst
        ):
            return False

        if (
            section == "top_themes"
            and not has_relevant_ticker
            and not has_catalyst
            and not has_readthrough
            and not any(term in text_lower for term in TRUST_MARKET_LINK_TERMS)
        ):
            return False

        if (
            section == "top_themes"
            and any(pattern in text_lower for pattern in TRUST_PERSONAL_FINANCE_PATTERNS)
            and not has_catalyst
        ):
            return False

        # Hard blocks for obvious low-value framing unless there is a hard catalyst.
        if any(pattern in text_lower for pattern in TRUST_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return False

        score = 0.0
        if event.source == "sec_edgar":
            score += 1.2
        if event.factual_confidence_score >= 0.80:
            score += 0.8
        elif event.factual_confidence_score >= 0.65:
            score += 0.5
        else:
            score += 0.2
        if event.cluster_size >= 3:
            score += 0.35
        if has_catalyst:
            score += 0.8
        if has_readthrough:
            score += 0.35
        if event.final_score >= 0.75:
            score += 0.2

        for pattern in TRUST_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.45
        if any(rx.search(title_lower) for rx in TRUST_SOFT_PENALTY_REGEXES):
            score -= 0.6

        if is_weekend:
            if any(marker in source_name or marker in url_lower for marker in WEEKEND_LOW_TRUST_SOURCES):
                score -= 0.55
            if "?" in title_lower and not has_catalyst:
                score -= 0.25
            if (
                event.factual_confidence_score < 0.60
                and event.cluster_size <= 2
                and not has_catalyst
                and not has_readthrough
            ):
                return False

        threshold = TRUST_TOP_THEMES_MIN_SCORE if section == "top_themes" else TRUST_SECTOR_SCAN_MIN_SCORE
        return score >= threshold

    def _dedupe_cross_section_events(self, briefing: MorningBriefing) -> None:
        """Keep a story from repeating across multiple morning sections."""
        seen_keys: set[str] = set()
        briefing.global_news = self._dedupe_event_list(briefing.global_news, seen_keys)
        briefing.portfolio_focus = self._dedupe_event_list(briefing.portfolio_focus, seen_keys)
        briefing.top_themes = self._dedupe_event_list(briefing.top_themes, seen_keys)

        for snapshot in briefing.sector_scan:
            snapshot.top_events = self._dedupe_event_list(snapshot.top_events, seen_keys)

        briefing.watchlist_events = self._dedupe_event_list(briefing.watchlist_events, seen_keys)

    def _dedupe_event_list(
        self,
        events: list[NormalisedEvent],
        seen_keys: set[str],
    ) -> list[NormalisedEvent]:
        deduped: list[NormalisedEvent] = []
        for event in events:
            key = self._event_dedupe_key(event)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(event)
        return deduped

    def _event_dedupe_key(self, event: NormalisedEvent) -> str:
        if event.cluster_id:
            return f"cluster:{event.cluster_id}"
        if event.content_hash:
            return f"hash:{event.content_hash}"
        title_key = self._normalise_text(event.title).strip()
        return f"title:{title_key}"

    def _fetch_watchlist_quotes(self) -> list:
        """Fetch quotes for primary watchlist tickers."""
        tickers = self.profile.watchlist_primary[:10]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)

    def _fetch_portfolio_quotes(self) -> list:
        """Fetch quotes for top held positions for richer delivery surfaces."""
        tickers = self.profile.portfolio_symbols[:6]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)

    def _should_build_charts(self) -> bool:
        """Avoid extra work unless at least one delivery/debug surface can use charts."""
        if not getattr(self.settings, "enable_charts", True):
            return False
        return bool(
            self.settings.email_configured
            or getattr(self.settings, "telegram_send_charts", False)
            or self.settings.show_output
        )
