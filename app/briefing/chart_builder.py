"""Attach reusable chart assets to morning briefings."""

from __future__ import annotations

from typing import Any

from app.briefing.chart_renderer import ChartRenderer
from app.briefing.morning_charts import build_morning_chart_bundle, selected_chart_specs
from app.data_sources.market_data import MarketDataService
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import ChartAsset
from app.schemas.events import MacroDataPoint


class MorningChartBuilder:
    """Build a small set of static chart cards for richer delivery surfaces."""

    def __init__(
        self,
        profile: UserProfile,
        market_data: MarketDataService,
        macro_data_svc: Any | None = None,
    ):
        self.profile = profile
        self.market_svc = market_data
        self.macro_svc = macro_data_svc
        self.renderer = ChartRenderer()

    def build(self, briefing: MorningBriefing) -> list[ChartAsset]:
        yield_curve: list[MacroDataPoint] = []
        if self.macro_svc is not None:
            try:
                yield_curve = self.macro_svc.get_yield_curve()
            except Exception:
                pass
        bundle, selection = build_morning_chart_bundle(
            briefing=briefing,
            profile=self.profile,
            market_data_service=self.market_svc,
            yield_curve_points=yield_curve,
        )
        briefing.morning_chart_bundle = bundle
        briefing.morning_chart_selection = selection

        rendered: list[ChartAsset] = []
        for spec in selected_chart_specs(bundle):
            asset = self.renderer.render_from_spec(spec)
            if asset:
                rendered.append(asset)

        # Backward compatibility fallback: keep at least one chart if deterministic
        # bundle is fully unavailable.
        if not rendered:
            legacy_market = self.renderer.render_market_snapshot(briefing.market_setup.index_quotes)
            if legacy_market:
                rendered.append(legacy_market)
            legacy_macro = self.renderer.render_macro_risk_strip(briefing.market_setup.macro_quotes)
            if legacy_macro:
                rendered.append(legacy_macro)

        default_mode = "full" if (briefing.session_key or "morning") == "morning" else "desk"
        density = str(self.profile.delivery.get("email_density_mode", default_mode)).strip().lower()
        cap = 5 if density == "desk" else 9
        return rendered[:cap]
