"""FRED provider: macroeconomic time series from the Federal Reserve.

Free, unlimited API.  Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

from __future__ import annotations

from datetime import datetime, timezone

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
    "GOLDPMGBD228NLBM": "Gold (USD/troy oz)",
    "DHHNGSP": "Henry Hub Natural Gas (USD/MMBtu)",
    "UNRATE": "US Unemployment Rate",
    "CPIAUCSL": "US CPI (All Urban)",
    "FEDFUNDS": "Fed Funds Rate",
    "GDPC1": "US Real GDP",
}

# Some legacy FRED IDs are intermittently retired/migrated.
# Try these alternates transparently before failing hard.
SERIES_FALLBACKS: dict[str, list[str]] = {
    "GOLDAMGBD228NLBM": ["GOLDPMGBD228NLBM"],
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

    def get_latest_observation(self, series_id: str, units: str | None = None) -> MacroDataPoint | None:
        """Fetch the most recent observation for a FRED series."""
        attempted = [series_id] + SERIES_FALLBACKS.get(series_id, [])
        for sid in attempted:
            point = self._get_latest_observation_once(series_id=sid, units=units)
            if point is not None:
                # Preserve requested semantic ID/name for downstream consumers.
                point.series_id = series_id
                point.name = DEFAULT_SERIES.get(series_id, DEFAULT_SERIES.get(sid, series_id))
                return point
        logger.warning("Failed to fetch FRED series %s", series_id)
        return None

    def _get_latest_observation_once(self, series_id: str, units: str | None = None) -> MacroDataPoint | None:
        """Single-attempt fetch for one concrete FRED series ID."""
        try:
            params = {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": 2,  # current + previous for change calc
            }
            if units:
                params["units"] = units
            data = self._get(
                f"{BASE_URL}/series/observations",
                params=self._params(params),
            )
        except ProviderError:
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

    def get_local_yoy_observation(
        self,
        series_id: str,
        *,
        window_size: int = 36,
        min_lookback_days: int = 330,
    ) -> MacroDataPoint | None:
        """Compute deterministic YoY from raw observations.

        Uses latest valid observation and the nearest valid observation at least
        `min_lookback_days` older.
        """
        attempted = [series_id] + SERIES_FALLBACKS.get(series_id, [])
        for sid in attempted:
            point = self._get_local_yoy_once(
                sid,
                window_size=window_size,
                min_lookback_days=min_lookback_days,
            )
            if point is not None:
                point.series_id = series_id
                point.name = DEFAULT_SERIES.get(series_id, DEFAULT_SERIES.get(sid, series_id))
                return point
        return None

    def _get_local_yoy_once(
        self,
        series_id: str,
        *,
        window_size: int,
        min_lookback_days: int,
    ) -> MacroDataPoint | None:
        rows = self._fetch_recent_valid_observations(series_id, limit=max(13, int(window_size)))
        if len(rows) < 2:
            return None
        latest = rows[0]
        latest_date = _parse_iso_date(latest.get("date", ""))
        latest_value = _safe_float(latest.get("value"))
        if latest_date is None or latest_value is None:
            return None

        year_ago = None
        for row in rows[1:]:
            d = _parse_iso_date(row.get("date", ""))
            v = _safe_float(row.get("value"))
            if d is None or v is None:
                continue
            if (latest_date - d).days >= int(min_lookback_days):
                year_ago = (d, v)
                break
        if year_ago is None or year_ago[1] == 0:
            return None

        yoy = ((latest_value / year_ago[1]) - 1.0) * 100.0
        return MacroDataPoint(
            series_id=series_id,
            name=DEFAULT_SERIES.get(series_id, series_id),
            value=yoy,
            previous_value=year_ago[1],
            change=yoy,
            change_percent=None,
            date=latest.get("date", ""),
            source="fred",
        )

    def _fetch_recent_valid_observations(self, series_id: str, *, limit: int) -> list[dict]:
        try:
            data = self._get(
                f"{BASE_URL}/series/observations",
                params=self._params(
                    {
                        "series_id": series_id,
                        "sort_order": "desc",
                        "limit": int(limit),
                    }
                ),
            )
        except ProviderError:
            return []
        observations = data.get("observations", []) if isinstance(data, dict) else []
        return [o for o in observations if isinstance(o, dict) and o.get("value", ".") != "."]

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


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _parse_iso_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None
