"""Morning briefing generator: orchestrates data fetching, processing,
and assembly of the pre-market morning briefing.
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.processing.dedupe import deduplicate_events
from app.processing.relevance_scoring import score_events
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse

logger = get_logger("morning_gen")


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

    def generate(self) -> MorningBriefing:
        """Generate the full morning briefing."""
        logger.info("Generating morning briefing...")
        briefing = MorningBriefing(generated_at=datetime.now())

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

        # 4. Enrich events with sector tags from universe
        all_events = self._enrich_sectors(all_events)

        # 5. Deduplicate
        deduped = deduplicate_events(all_events)
        briefing.events_after_dedup = len(deduped)

        # 6. Score and rank
        scored = score_events(deduped, self.profile)

        # 7. Assemble sections
        briefing.top_themes = scored[:5]
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
        """Build sector snapshots with ETF quotes and top events per sector."""
        # Fetch sector ETF quotes
        etf_symbols = self.universe.all_sector_etfs
        etf_quotes = {}
        if etf_symbols:
            for q in self.market_svc.get_quotes(etf_symbols):
                etf_quotes[q.symbol] = q

        # Sort sectors by user's sector weights (highest first)
        weighted_sectors = sorted(
            self.universe.sectors,
            key=lambda s: self.profile.sector_weights.get(s.key, 0.3),
            reverse=True,
        )

        snapshots = []
        for sector in weighted_sectors[:12]:  # top 12 sectors
            # Events mentioning this sector's tickers
            sector_tickers = set(sector.key_names)
            sector_events = [
                e for e in scored_events
                if any(t in sector_tickers for t in e.tickers)
                or sector.key in e.sectors
            ]

            etf_quote = etf_quotes.get(sector.etf)
            snap = SectorSnapshot(
                sector_key=sector.key,
                display_name=sector.display_name,
                etf_symbol=sector.etf,
                etf_quote=etf_quote,
                top_events=sector_events[:3],
            )
            snapshots.append(snap)

        return snapshots

    def _fetch_earnings(self):
        """Fetch today's earnings calendar."""
        if not self.news_svc.finnhub or not self.news_svc.finnhub.is_configured():
            return []
        return self.news_svc.finnhub.get_earnings_calendar(days_ahead=1)

    def _filter_watchlist_events(self, scored: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Filter events relevant to the user's watchlist."""
        watchlist_set = set(self.profile.all_watchlist_tickers)
        return [
            e for e in scored
            if any(t in watchlist_set for t in e.tickers)
        ][:8]

    def _fetch_watchlist_quotes(self) -> list:
        """Fetch quotes for primary watchlist tickers."""
        tickers = self.profile.watchlist_primary[:10]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)

    def _enrich_sectors(self, events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Tag events with sector information based on their tickers."""
        for evt in events:
            if evt.sectors:
                continue
            for ticker in evt.tickers:
                sectors = self.universe.sectors_for_ticker(ticker)
                evt.sectors.extend(s for s in sectors if s not in evt.sectors)
        return events
