"""Tests for ECB and Eurostat macro providers + service wiring."""

from __future__ import annotations

from app.data_sources.macro_data import MacroDataService
from app.data_sources.providers.ecb import ECBProvider
from app.data_sources.providers.eurostat import EurostatProvider
from app.settings import Settings


def test_ecb_provider_parses_series_to_macro_point():
    provider = ECBProvider(base_url="https://example.com")
    provider._get = lambda *_args, **_kwargs: {
        "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [3.75], "1": [4.0]}}}}],
        "structure": {"dimensions": {"observation": [{"values": [{"id": "2026-01"}, {"id": "2026-02"}]}]}},
    }

    point = provider.get_deposit_facility_rate()
    assert point is not None
    assert point.series_id == "ECB_DFR"
    assert point.value == 4.0
    assert point.previous_value == 3.75
    assert point.change == 0.25


def test_eurostat_provider_parses_unemployment_point():
    provider = EurostatProvider(base_url="https://example.com")
    provider._get = lambda *_args, **_kwargs: {
        "value": {"0": 6.4, "1": 6.3},
        "dimension": {
            "time": {
                "category": {
                    "index": {
                        "2026-01": 0,
                        "2026-02": 1,
                    }
                }
            }
        },
    }

    point = provider.get_euro_area_unemployment()
    assert point is not None
    assert point.series_id == "EUROSTAT_UNE_RT"
    assert point.value == 6.3
    assert point.previous_value == 6.4
    assert point.change == -0.1


def test_macro_service_exposes_ecb_and_eurostat_snapshots():
    settings = Settings(
        dry_run=True,
        fred_api_key="x",
        ecb_base_url="https://ecb.example.com",
        eurostat_base_url="https://eurostat.example.com",
    )
    svc = MacroDataService(settings)

    svc.ecb._get = lambda *_args, **_kwargs: {
        "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [3.75], "1": [4.0]}}}}],
        "structure": {"dimensions": {"observation": [{"values": [{"id": "2026-01"}, {"id": "2026-02"}]}]}},
    }
    svc.eurostat._get = lambda *_args, **_kwargs: {
        "value": {"0": 6.4, "1": 6.3},
        "dimension": {"time": {"category": {"index": {"2026-01": 0, "2026-02": 1}}}},
    }

    ecb = svc.get_ecb_snapshot()
    eurostat = svc.get_eurostat_snapshot()

    assert len(ecb) == 3
    assert ecb[0].source == "ecb"
    assert len(eurostat) == 1
    assert eurostat[0].source == "eurostat"
