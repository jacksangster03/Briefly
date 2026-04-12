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

        macro_chart = self.renderer.render_macro_risk_strip(briefing.market_setup.macro_quotes)
        if macro_chart:
            charts.append(macro_chart)

        perf_quotes = briefing.portfolio_quotes or briefing.watchlist_quotes
        if perf_quotes:
            title = "Top Holdings Performance" if briefing.portfolio_quotes else "Watchlist Performance"
            caption = (
                "Latest move across top configured held positions."
                if briefing.portfolio_quotes
                else "Latest move across the primary watchlist basket."
            )
            perf_chart = self.renderer.render_performance_snapshot(
                perf_quotes,
                title=title,
                key="top_holdings_performance" if briefing.portfolio_quotes else "watchlist_performance",
                caption=caption,
            )
            if perf_chart:
                charts.append(perf_chart)

        sector_chart = self.renderer.render_sector_exposure_performance(
            self._build_sector_exposure_points(briefing),
        )
        if sector_chart:
            charts.append(sector_chart)

        focus_symbol = self._pick_focus_symbol(briefing)
        if focus_symbol:
            history = self.market_svc.get_price_history(focus_symbol, period="1mo", interval="1d")
            focus_chart = self.renderer.render_price_history(
                format_company_ticker(focus_symbol),
                history,
            )
            if focus_chart:
                focus_chart.key = "event_linked_trend"
                focus_chart.title = f"{format_company_ticker(focus_symbol)} Event-Linked Trend"
                focus_chart.filename = "event-linked-trend.png"
                focus_chart.caption = (
                    "Recent trend for the symbol most directly linked to the highest-signal current event."
                )
                charts.append(focus_chart)

        # Keep chart packs bounded and consistent for email readability.
        return charts[:5]

    def _pick_focus_symbol(self, briefing: MorningBriefing) -> str:
        portfolio_symbols = set(self.profile.portfolio_symbols)

        for event in briefing.portfolio_focus:
            for ticker in event.tickers:
                if ticker in portfolio_symbols:
                    return ticker

        for event in briefing.top_themes:
            for ticker in event.tickers:
                if ticker in portfolio_symbols:
                    return ticker

        for event in briefing.watchlist_events:
            for ticker in event.tickers:
                if ticker in portfolio_symbols:
                    return ticker

        if self.profile.portfolio_symbols:
            return self.profile.portfolio_symbols[0]

        if briefing.watchlist_quotes:
            return briefing.watchlist_quotes[0].symbol

        return ""

    def _build_sector_exposure_points(self, briefing: MorningBriefing) -> list[tuple[str, float, float]]:
        """Create (sector label, portfolio weight, sector ETF change%) points."""
        if not self.profile.portfolio_sector_weights:
            return []

        etf_change_by_sector: dict[str, float] = {}
        display_by_sector: dict[str, str] = {}
        for snapshot in briefing.sector_scan:
            if snapshot.etf_quote is None:
                continue
            etf_change_by_sector[snapshot.sector_key] = snapshot.etf_quote.change_percent
            display_by_sector[snapshot.sector_key] = snapshot.display_name

        points: list[tuple[str, float, float]] = []
        for sector_key, weight in self.profile.portfolio_sector_weights.items():
            if sector_key not in etf_change_by_sector:
                continue
            points.append(
                (
                    display_by_sector.get(sector_key, sector_key.replace("_", " ").title()),
                    weight,
                    etf_change_by_sector[sector_key],
                )
            )
        return points
