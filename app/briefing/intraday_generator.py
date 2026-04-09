"""Intraday update generator: fetches new events since last check,
deduplicates against sent history, scores, and returns the top items.
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.pipeline import process_event_stream, select_intraday_events
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
        self.rules = load_alert_rules(settings).intraday

    def generate(
        self,
        min_score: float | None = None,
        max_events: int | None = None,
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

        scored = process_event_stream(events, self.profile, self.settings)
        effective_min_score = min_score if min_score is not None else self.rules.min_final_score
        effective_max_events = max_events if max_events is not None else self.rules.max_events_per_update
        if effective_min_score != self.rules.min_final_score:
            self.rules.min_final_score = effective_min_score
        if effective_max_events != self.rules.max_events_per_update:
            self.rules.max_events_per_update = effective_max_events
        top = select_intraday_events(scored, self.rules)

        # Market snapshot
        snapshot = self.market_svc.get_quotes(self.universe.all_index_symbols[:4])

        now = datetime.now()
        update = IntradayUpdate(
            generated_at=now,
            hour_label=now.strftime("%H:%M"),
            market_snapshot=snapshot,
            new_events=top,
            events_fetched=fetched,
            events_after_dedup=len(scored),
            events_sent=len(top),
        )

        logger.info(
            "Intraday update: %d fetched, %d deduped, %d above threshold",
            fetched, len(scored), len(top),
        )
        return update
