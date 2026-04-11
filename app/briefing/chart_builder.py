"""Attach reusable chart assets to morning briefings."""

from __future__ import annotations

from app.briefing.chart_renderer import ChartRenderer
from app.data_sources.market_data import MarketDataService
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import ChartAsset
from app.universe.ticker_metadata import format_company_ticker


class MorningChartBuilder:
    """Build a small set of static chart cards for richer delivery surfaces."""

    def __init__(
        self,
        profile: UserProfile,
        market_data: MarketDataService,
    ):
        self.profile = profile
        self.market_svc = market_data
        self.renderer = ChartRenderer()

    def build(self, briefing: MorningBriefing) -> list[ChartAsset]:
        charts: list[ChartAsset] = []

        market_chart = self.renderer.render_market_snapshot(briefing.market_setup.index_quotes)
        if market_chart:
            charts.append(market_chart)

        perf_quotes = briefing.portfolio_quotes or briefing.watchlist_quotes
        if perf_quotes:
            title = "Portfolio Movers" if briefing.portfolio_quotes else "Watchlist Movers"
            caption = (
                "Latest move across the top configured positions."
                if briefing.portfolio_quotes
                else "Latest move across the primary watchlist."
            )
            perf_chart = self.renderer.render_performance_snapshot(
                perf_quotes,
                title=title,
                key="portfolio_movers" if briefing.portfolio_quotes else "watchlist_movers",
                caption=caption,
            )
            if perf_chart:
                charts.append(perf_chart)

        focus_symbol = self._pick_focus_symbol(briefing)
        if focus_symbol:
            history = self.market_svc.get_price_history(focus_symbol, period="1mo", interval="1d")
            focus_chart = self.renderer.render_price_history(
                format_company_ticker(focus_symbol),
                history,
            )
            if focus_chart:
                charts.append(focus_chart)

        return charts

    def _pick_focus_symbol(self, briefing: MorningBriefing) -> str:
        portfolio_symbols = set(self.profile.portfolio_symbols)

        for event in briefing.portfolio_focus:
            for ticker in event.tickers:
                if ticker in portfolio_symbols:
                    return ticker

        if self.profile.portfolio_symbols:
            return self.profile.portfolio_symbols[0]

        if briefing.watchlist_quotes:
            return briefing.watchlist_quotes[0].symbol

        return ""
