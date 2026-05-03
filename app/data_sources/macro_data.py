"""Macro data service: economic indicators from FRED, ECB, and Eurostat."""

from __future__ import annotations

from app.data_sources.providers.ecb import ECBProvider
from app.data_sources.providers.eurostat import EurostatProvider
from app.data_sources.providers.fred import FREDProvider
from app.logger import get_logger
from app.schemas.events import MacroDataPoint
from app.settings import Settings

logger = get_logger("macro_data")

# Morning briefing series: yields, USD, oil
MORNING_SERIES = ["DGS10", "DGS2", "T10Y2Y", "DTWEXBGS"]

# Full yield curve: 2Y, 5Y, 10Y, 30Y
YIELD_CURVE_SERIES = ["DGS2", "DGS5", "DGS10", "DGS30"]

# Extended macro context
EXTENDED_SERIES = ["UNRATE", "CPIAUCSL", "FEDFUNDS"]


class MacroDataService:
    """Fetches macroeconomic data from FRED, ECB, and Eurostat."""

    def __init__(self, settings: Settings):
        self.fred = (
            FREDProvider(
                api_key=settings.fred_api_key,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.fred_configured
            else None
        )
        self.ecb = (
            ECBProvider(
                base_url=settings.ecb_base_url,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.ecb_configured
            else None
        )
        self.eurostat = (
            EurostatProvider(
                base_url=settings.eurostat_base_url,
                timeout=settings.provider_timeout,
                max_retries=settings.provider_max_retries,
            )
            if settings.eurostat_configured
            else None
        )

    def get_morning_macro(self) -> list[MacroDataPoint]:
        """Fetch key macro indicators for the morning briefing."""
        if not self.fred or not self.fred.is_configured():
            logger.warning("FRED not configured; macro data unavailable")
            return []

        return self.fred.get_macro_snapshot(MORNING_SERIES)

    def get_treasury_yields(self) -> tuple[MacroDataPoint | None, MacroDataPoint | None]:
        """Return (10Y yield, 2Y yield) or (None, None) on failure."""
        if not self.fred or not self.fred.is_configured():
            return None, None

        ten_y = self.fred.get_latest_observation("DGS10")
        two_y = self.fred.get_latest_observation("DGS2")
        return ten_y, two_y

    def get_yield_curve(self) -> list[MacroDataPoint]:
        """Fetch 2Y/5Y/10Y/30Y Treasury yields for curve shape chart."""
        if not self.fred or not self.fred.is_configured():
            return []
        return self.fred.get_macro_snapshot(YIELD_CURVE_SERIES)

    def get_extended_macro(self) -> list[MacroDataPoint]:
        """Fetch broader macro context (unemployment, CPI, fed funds)."""
        if not self.fred or not self.fred.is_configured():
            return []

        return self.fred.get_macro_snapshot(EXTENDED_SERIES)

    def get_ecb_snapshot(self) -> list[MacroDataPoint]:
        """Fetch ECB policy rate, EUR/USD, and HICP inflation."""
        if not self.ecb or not self.ecb.is_configured():
            return []
        points = []
        for fetcher in [
            self.ecb.get_deposit_facility_rate,
            self.ecb.get_eur_usd,
            self.ecb.get_hicp_inflation,
        ]:
            point = fetcher()
            if point is not None:
                points.append(point)
        logger.info("ECB snapshot: %d items", len(points))
        return points

    def get_eurostat_snapshot(self) -> list[MacroDataPoint]:
        """Fetch Euro area unemployment from Eurostat."""
        if not self.eurostat or not self.eurostat.is_configured():
            return []
        point = self.eurostat.get_euro_area_unemployment()
        points = [point] if point is not None else []
        logger.info("Eurostat snapshot: %d items", len(points))
        return points
