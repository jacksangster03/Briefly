"""Breaking alert generator: identifies high-importance events above threshold."""

from __future__ import annotations

from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.user_profile import UserProfile
from app.processing.dedupe import deduplicate_events
from app.processing.relevance_scoring import score_events
from app.schemas.briefings import BreakingAlert
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse

logger = get_logger("breaking_gen")


class BreakingAlertGenerator:
    """Identifies events above the breaking threshold and builds alerts."""

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

    def check(
        self,
        min_score: float = 0.80,
        max_alerts: int = 3,
    ) -> list[BreakingAlert]:
        """Check for breaking events. Returns list of alerts to send."""
        events = self.news_svc.fetch_market_news()

        # Enrich sectors
        for evt in events:
            for ticker in evt.tickers:
                sectors = self.universe.sectors_for_ticker(ticker)
                evt.sectors.extend(s for s in sectors if s not in evt.sectors)

        # Dedupe + score
        deduped = deduplicate_events(events)
        scored = score_events(deduped, self.profile)

        # Filter to breaking threshold, exclude already-sent
        breaking = [
            e for e in scored
            if e.final_score >= min_score and not e.already_sent
        ]

        if not breaking:
            return []

        # Build alerts with market context
        context_quotes = self.market_svc.get_quotes(self.universe.all_index_symbols[:4])
        alerts = []

        for evt in breaking[:max_alerts]:
            alert = BreakingAlert(
                event=evt,
                market_context=context_quotes,
                reason=evt.score_explanation,
            )
            alerts.append(alert)
            logger.info(
                "Breaking alert: %s (score=%.3f)",
                evt.title[:60], evt.final_score,
            )

        return alerts
