"""Eurostat provider via the public JSON API."""

from __future__ import annotations

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import MacroDataPoint

logger = get_logger("eurostat")

_UNE_DATASET = "UNE_RT_M"


class EurostatProvider(BaseProvider):
    name = "eurostat"

    def __init__(self, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _fetch_dataset(self, dataset: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{dataset}"
        try:
            data = self._get(url, params=params or {})
            return data if isinstance(data, dict) else {}
        except ProviderError:
            logger.warning("Eurostat fetch failed for dataset %s", dataset)
            return {}

    def get_euro_area_unemployment(self) -> MacroDataPoint | None:
        data = self._fetch_dataset(
            _UNE_DATASET,
            params={
                "geo": "EA20",
                "s_adj": "SA",
                "age": "TOTAL",
                "sex": "T",
                "unit": "PC_ACT",
                "lang": "EN",
            },
        )
        if not data:
            return None

        try:
            values_map: dict = data.get("value", {})
            time_index: dict = (
                data.get("dimension", {})
                .get("time", {})
                .get("category", {})
                .get("index", {})
            )
            if not values_map or not time_index:
                return None

            sorted_times = sorted(time_index.items(), key=lambda item: item[1])
            if not sorted_times:
                return None

            latest_period, latest_idx = sorted_times[-1]
            prev_idx = sorted_times[-2][1] if len(sorted_times) >= 2 else None

            latest_key = str(latest_idx)
            prev_key = str(prev_idx) if prev_idx is not None else None
            if latest_key not in values_map:
                return None

            value = float(values_map[latest_key])
            prev_value = float(values_map[prev_key]) if prev_key and prev_key in values_map else None
            change = round(value - prev_value, 4) if prev_value is not None else None

            return MacroDataPoint(
                series_id="EUROSTAT_UNE_RT",
                name="Euro Area Unemployment Rate",
                value=round(value, 4),
                previous_value=round(prev_value, 4) if prev_value is not None else None,
                change=change,
                date=latest_period,
                source="eurostat",
            )
        except Exception as exc:
            logger.warning("Eurostat parse error: %s", exc)
            return None
