"""Macro data service: economic indicators from FRED."""

from __future__ import annotations

from app.data_sources.providers.fred import FREDProvider
from app.logger import get_logger
from app.schemas.events import MacroDataPoint
from app.settings import Settings

logger = get_logger("macro_data")

# Morning briefing series: yields, USD, oil
MORNING_SERIES = ["DGS10", "DGS2", "T10Y2Y", "DTWEXBGS"]

# Extended macro context
EXTENDED_SERIES = ["UNRATE", "CPIAUCSL", "FEDFUNDS"]


class MacroDataService:
    """Fetches macroeconomic data from FRED."""

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

    def get_extended_macro(self) -> list[MacroDataPoint]:
        """Fetch broader macro context (unemployment, CPI, fed funds)."""
        if not self.fred or not self.fred.is_configured():
            return []

        return self.fred.get_macro_snapshot(EXTENDED_SERIES)
