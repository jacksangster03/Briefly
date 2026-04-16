"""Intraday update generator: fetches new events since last check,
deduplicates against sent history, scores, and returns the top items.
"""

from __future__ import annotations

from datetime import datetime

from app.briefing.global_news_selector import event_tracking_key, select_global_market_events
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.pipeline import is_actionable_event, process_event_stream, select_intraday_events
from app.schemas.briefings import IntradayUpdate, session_mode_for
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse

logger = get_logger("intraday_gen")
MAX_GLOBAL_RISK_ITEMS = 3


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

        scored = process_event_stream(
            events,
            self.profile,
            self.settings,
            sector_lookup=self.universe.sectors_for_ticker,
        )
        prioritized = self._prioritize_for_portfolio(scored)
        now = datetime.now()
        session_mode = session_mode_for(now)
        global_risk_items = self._build_global_risk_items(prioritized, session_mode=session_mode)
        effective_min_score = min_score if min_score is not None else self.rules.min_final_score
        effective_max_events = max_events if max_events is not None else self.rules.max_events_per_update
        if effective_min_score != self.rules.min_final_score:
            self.rules.min_final_score = effective_min_score
        if effective_max_events != self.rules.max_events_per_update:
            self.rules.max_events_per_update = effective_max_events
        top = select_intraday_events(prioritized, self.rules)
        if global_risk_items:
            global_keys = {event_tracking_key(evt) for evt in global_risk_items}
            top = [evt for evt in top if event_tracking_key(evt) not in global_keys]
            remaining_budget = max(0, effective_max_events - len(global_risk_items))
            top = top[:remaining_budget]

        # Market snapshot
        snapshot = self.market_svc.get_quotes(self.universe.all_index_symbols[:4])
        name_map = {i.symbol: i.display for i in self.universe.indices}
        for quote in snapshot:
            quote.display_name = name_map.get(quote.symbol, quote.symbol)

        update = IntradayUpdate(
            generated_at=now,
            session_mode=session_mode,
            hour_label=now.strftime("%H:%M"),
            market_snapshot=snapshot,
            global_risk_items=global_risk_items,
            new_events=top,
            events_fetched=fetched,
            events_after_dedup=len(scored),
            events_sent=len(top) + len(global_risk_items),
        )

        logger.info(
            "Intraday update: %d fetched, %d deduped, %d above threshold",
            fetched, len(scored), len(top),
        )
        return update

    def _build_global_risk_items(
        self,
        events: list[NormalisedEvent],
        *,
        session_mode: str,
    ) -> list[NormalisedEvent]:
        if not self.profile.intraday_global_risk_enabled:
            return []
        return select_global_market_events(
            events,
            session_mode=session_mode,
            max_items=MAX_GLOBAL_RISK_ITEMS,
            region_weights=self.profile.coverage_weights,
            actionable_check=is_actionable_event,
        )

    def _prioritize_for_portfolio(self, events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Apply a small portfolio-aware tie-break boost before selection."""
        if not self.profile.has_portfolio:
            return events

        portfolio_symbols = set(self.profile.portfolio_symbols)
        weight_by_ticker = self.profile.portfolio_weight_by_ticker
        concentrated_sectors = {
            sector
            for sector, weight in self.profile.portfolio_sector_weights.items()
            if weight >= 0.2
        }

        def _priority(event: NormalisedEvent) -> float:
            bonus = 0.0
            held_hits = [ticker for ticker in event.tickers if ticker in portfolio_symbols]
            if held_hits:
                max_weight = max(weight_by_ticker.get(ticker, 0.0) for ticker in held_hits)
                bonus += 0.05
                bonus += min(0.05, max_weight / 200.0)
            elif concentrated_sectors and any(sector in concentrated_sectors for sector in event.sectors):
                bonus += 0.02
            return event.final_score + bonus

        return sorted(events, key=_priority, reverse=True)
