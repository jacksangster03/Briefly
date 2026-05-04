"""FRED provider: macroeconomic time series from the Federal Reserve.

Free, unlimited API.  Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import MacroDataPoint

logger = get_logger("fred")

BASE_URL = "https://api.stlouisfed.org/fred"

# Key series for the morning briefing macro context
DEFAULT_SERIES = {
    "DGS2": "US 2Y Treasury Yield",
    "DGS10": "US 10Y Treasury Yield",
    "DGS30": "US 30Y Treasury Yield",
    "T10Y2Y": "10Y-2Y Yield Spread",
    "DTWEXBGS": "Trade-Weighted USD Index",
    "DCOILWTICO": "WTI Crude Oil (USD/bbl)",
    "DCOILBRENTEU": "Brent Crude Oil (USD/bbl)",
    "GOLDAMGBD228NLBM": "Gold (USD/troy oz)",
    "DHHNGSP": "Henry Hub Natural Gas (USD/MMBtu)",
    "UNRATE": "US Unemployment Rate",
    "CPIAUCSL": "US CPI (All Urban)",
    "FEDFUNDS": "Fed Funds Rate",
    "GDPC1": "US Real GDP",
}


class FREDProvider(BaseProvider):
    name = "fred"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _params(self, extra: dict | None = None) -> dict:
        p = {"api_key": self.api_key, "file_type": "json"}
        if extra:
            p.update(extra)
        return p

    # -- Series observation ---------------------------------------------------

    def get_latest_observation(self, series_id: str) -> MacroDataPoint | None:
        """Fetch the most recent observation for a FRED series."""
        try:
            data = self._get(
                f"{BASE_URL}/series/observations",
                params=self._params({
                    "series_id": series_id,
                    "sort_order": "desc",
                    "limit": 2,  # current + previous for change calc
                }),
            )
        except ProviderError:
            logger.warning("Failed to fetch FRED series %s", series_id)
            return None

        observations = data.get("observations", [])
        if not observations:
            return None

        # Filter out missing values (FRED uses "." for missing)
        valid = [o for o in observations if o.get("value", ".") != "."]
        if not valid:
            return None

        latest = valid[0]
        value = float(latest["value"])

        previous_value = None
        change = None
        change_percent = None
        if len(valid) > 1:
            previous_value = float(valid[1]["value"])
            change = value - previous_value
            if previous_value != 0:
                change_percent = (change / abs(previous_value)) * 100

        return MacroDataPoint(
            series_id=series_id,
            name=DEFAULT_SERIES.get(series_id, series_id),
            value=value,
            previous_value=previous_value,
            change=change,
            change_percent=change_percent,
            date=latest.get("date", ""),
            source="fred",
        )

    def get_macro_snapshot(
        self, series_ids: list[str] | None = None
    ) -> list[MacroDataPoint]:
        """Fetch latest values for a set of macro series."""
        ids = series_ids or list(DEFAULT_SERIES.keys())
        results = []
        for sid in ids:
            point = self.get_latest_observation(sid)
            if point:
                results.append(point)

        logger.info("Fetched %d/%d FRED series", len(results), len(ids))
        return results

    # -- Series search (utility) ----------------------------------------------

    def search_series(self, query: str, limit: int = 5) -> list[dict]:
        """Search FRED for series matching a keyword."""
        try:
            data = self._get(
                f"{BASE_URL}/series/search",
                params=self._params({
                    "search_text": query,
                    "limit": limit,
                }),
            )
            return data.get("seriess", [])
        except ProviderError:
            return []
