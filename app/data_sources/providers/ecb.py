"""ECB data provider via SDMX JSON endpoint."""

from __future__ import annotations

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import MacroDataPoint

logger = get_logger("ecb")

# ECB Deposit Facility Rate (daily, level). Previous key (RT0...CUSA.A)
# now returns 400 on the new data API for many clients.
_DFR_FLOW = "FM/D.U2.EUR.4F.KR.DFR.LEV"
_EURUSD_FLOW = "EXR/D.USD.EUR.SP00.A"
_HICP_FLOW = "ICP/M.U2.N.000000.4.ANR"


class ECBProvider(BaseProvider):
    name = "ecb"

    def __init__(self, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _fetch_series(self, flow: str, last_n: int = 2) -> list[tuple[str, float]]:
        url = f"{self.base_url}/data/{flow}"
        try:
            data = self._get(
                url,
                params={"lastNObservations": last_n, "detail": "dataonly"},
                headers={"Accept": "application/vnd.sdmx.data+json;version=1.0.0-wd"},
            )
        except ProviderError:
            logger.warning("ECB fetch failed for %s", flow)
            return []

        try:
            datasets = data.get("dataSets", [])
            if not datasets:
                return []
            series_items = list(datasets[0].get("series", {}).values())
            if not series_items:
                return []
            observations = series_items[0].get("observations", {})
            time_values = (
                data.get("structure", {})
                .get("dimensions", {})
                .get("observation", [{}])[0]
                .get("values", [])
            )

            result: list[tuple[str, float]] = []
            for idx_str, obs in sorted(observations.items(), key=lambda item: int(item[0])):
                idx = int(idx_str)
                date_str = time_values[idx]["id"] if idx < len(time_values) else ""
                if not obs:
                    continue
                result.append((date_str, float(obs[0])))
            return result
        except Exception as exc:
            logger.warning("ECB parse error for %s: %s", flow, exc)
            return []

    def get_deposit_facility_rate(self) -> MacroDataPoint | None:
        obs = self._fetch_series(_DFR_FLOW, last_n=2)
        if not obs:
            return None
        date, value = obs[-1]
        prev_value = obs[-2][1] if len(obs) >= 2 else None
        change = round(value - prev_value, 4) if prev_value is not None else None
        return MacroDataPoint(
            series_id="ECB_DFR",
            name="ECB Deposit Facility Rate",
            value=round(value, 4),
            previous_value=round(prev_value, 4) if prev_value is not None else None,
            change=change,
            date=date,
            source="ecb",
        )

    def get_eur_usd(self) -> MacroDataPoint | None:
        obs = self._fetch_series(_EURUSD_FLOW, last_n=2)
        if not obs:
            return None
        date, value = obs[-1]
        prev_value = obs[-2][1] if len(obs) >= 2 else None
        change = round(value - prev_value, 6) if prev_value is not None else None
        change_pct = round(change / prev_value * 100, 4) if prev_value and change is not None else None
        return MacroDataPoint(
            series_id="ECB_EURUSD",
            name="EUR/USD (ECB reference)",
            value=round(value, 6),
            previous_value=round(prev_value, 6) if prev_value is not None else None,
            change=change,
            change_percent=change_pct,
            date=date,
            source="ecb",
        )

    def get_hicp_inflation(self) -> MacroDataPoint | None:
        obs = self._fetch_series(_HICP_FLOW, last_n=2)
        if not obs:
            return None
        date, value = obs[-1]
        prev_value = obs[-2][1] if len(obs) >= 2 else None
        change = round(value - prev_value, 4) if prev_value is not None else None
        return MacroDataPoint(
            series_id="ECB_HICP",
            name="Euro Area HICP Inflation (YoY)",
            value=round(value, 4),
            previous_value=round(prev_value, 4) if prev_value is not None else None,
            change=change,
            date=date,
            source="ecb",
        )
