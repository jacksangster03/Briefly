"""Breaking alert generator: identifies high-importance events above threshold."""

from __future__ import annotations

from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.pipeline import process_event_stream, select_breaking_events
from app.schemas.briefings import BreakingAlert
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse
from app.universe.ticker_metadata import format_company_ticker_list

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
        self.rules = load_alert_rules(settings).breaking

    def check(
        self,
        min_score: float | None = None,
        max_alerts: int | None = None,
    ) -> list[BreakingAlert]:
        """Check for breaking events. Returns list of alerts to send."""
        events = self.news_svc.fetch_market_news()

        scored = process_event_stream(
            events,
            self.profile,
            self.settings,
            sector_lookup=self.universe.sectors_for_ticker,
        )
        if min_score is not None:
            self.rules.min_final_score = min_score
        if max_alerts is not None:
            self.rules.max_per_hour = max_alerts
        breaking = select_breaking_events(scored, self.rules)

        if not breaking:
            return []

        # Build alerts with market context
        context_quotes = self.market_svc.get_quotes(self.universe.all_index_symbols[:4])
        name_map = {i.symbol: i.display for i in self.universe.indices}
        for quote in context_quotes:
            quote.display_name = name_map.get(quote.symbol, quote.symbol)
        alerts = []

        for evt in breaking[:max_alerts]:
            alert = BreakingAlert(
                event=evt,
                market_context=context_quotes,
                reason=_build_alert_reason(evt),
            )
            alerts.append(alert)
            logger.info(
                "Breaking alert: %s (score=%.3f)",
                evt.title[:60], evt.final_score,
            )

        return alerts


def _build_alert_reason(evt: NormalisedEvent) -> str:
    """Build a concise, user-facing reason for why this event triggered."""
    parts: list[str] = []

    if evt.cluster_size > 1:
        parts.append(f"Confirmed by {evt.cluster_size} sources")
    if evt.update_status == "material_update":
        parts.append("developing story with new details")
    if evt.tickers:
        label = format_company_ticker_list(evt.tickers, max_items=3)
        if label:
            parts.append(f"affects {label}")
    if evt.sectors:
        parts.append(f"sector: {', '.join(evt.sectors[:2])}")
    if evt.source == "sec_edgar":
        parts.append("official regulatory filing")

    if not parts:
        parts.append("high-impact market event")

    return ". ".join(parts)
