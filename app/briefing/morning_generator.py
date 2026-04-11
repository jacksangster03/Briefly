"""Morning briefing generator: orchestrates data fetching, processing,
and assembly of the pre-market morning briefing.
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.briefing.theme_builder import build_top_themes
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.pipeline import is_actionable_event, process_event_stream
from app.schemas.briefings import MarketSetup, MorningBriefing, session_mode_for
from app.schemas.events import NormalisedEvent, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse

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

        # 2. Macro context from FRED
        briefing.macro_context = self.macro_svc.get_morning_macro()
        ten_y, two_y = self.macro_svc.get_treasury_yields()
        briefing.market_setup.treasury_10y = ten_y
        briefing.market_setup.treasury_2y = two_y

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

        # 7. Assemble sections
        briefing.top_themes = build_top_themes(scored, max_themes=self.rules.max_themes)
        briefing.sector_scan = self._build_sector_scan(scored)
        briefing.earnings_calendar = self._fetch_earnings()
        briefing.watchlist_events = self._filter_watchlist_events(scored)
        briefing.watchlist_quotes = self._fetch_watchlist_quotes()
        briefing.events_sent = len(briefing.top_themes) + sum(
            len(s.top_events) for s in briefing.sector_scan
        ) + len(briefing.watchlist_events)

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

        return setup

    def _build_sector_scan(self, scored_events: list[NormalisedEvent]) -> list[SectorSnapshot]:
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
        """Fetch today's earnings calendar."""
        if not self.news_svc.finnhub or not self.news_svc.finnhub.is_configured():
            return []
        return self.news_svc.finnhub.get_earnings_calendar(days_ahead=1)[: self.rules.max_earnings]

    def _filter_watchlist_events(self, scored: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Filter events relevant to the user's watchlist."""
        watchlist_set = set(self.profile.all_watchlist_tickers)
        return [
            e for e in scored
            if is_actionable_event(e) and any(t in watchlist_set for t in e.tickers)
        ][: self.rules.max_watchlist_events]

    def _fetch_watchlist_quotes(self) -> list:
        """Fetch quotes for primary watchlist tickers."""
        tickers = self.profile.watchlist_primary[:10]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)
