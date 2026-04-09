"""Intraday update generator: fetches new events since last check,
deduplicates against sent history, scores, and returns the top items.
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.processing.dedupe import deduplicate_events
from app.processing.relevance_scoring import score_events
from app.schemas.briefings import IntradayUpdate
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse

logger = get_logger("intraday_gen")


class IntradayGenerator:
    """Generates hourly intraday updates with only new, material developments."""

    def __init__(
        self,
        settings: Settings,
        profile: UserProfile,
        universe: SectorUniverse,
        market_data: MarketDataService,
        news_data: NewsDataService,
    ):
        self.settings = settings
        self.profile = profile
        self.universe = universe
        self.market_svc = market_data
        self.news_svc = news_data

    def generate(
        self,
        min_score: float = 0.40,
        max_events: int = 7,
    ) -> IntradayUpdate:
        """Generate an intraday update."""
        logger.info("Generating intraday update...")

        # Fetch new events
        events = self.news_svc.fetch_market_news()
        fetched = len(events)

        # Enrich with sector tags
        for evt in events:
            for ticker in evt.tickers:
                sectors = self.universe.sectors_for_ticker(ticker)
                evt.sectors.extend(s for s in sectors if s not in evt.sectors)

        # Dedupe (includes already-sent check against DB)
        deduped = deduplicate_events(events)

        # Score and filter
        scored = score_events(deduped, self.profile)
        top = [e for e in scored if e.final_score >= min_score][:max_events]

        # Market snapshot
        snapshot = self.market_svc.get_quotes(self.universe.all_index_symbols[:4])

        now = datetime.now()
        update = IntradayUpdate(
            generated_at=now,
            hour_label=now.strftime("%H:%M"),
            market_snapshot=snapshot,
            new_events=top,
            events_fetched=fetched,
            events_after_dedup=len(deduped),
            events_sent=len(top),
        )

        logger.info(
            "Intraday update: %d fetched, %d deduped, %d above threshold",
            fetched, len(deduped), len(top),
        )
        return update
