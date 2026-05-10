from __future__ import annotations

from types import SimpleNamespace

from app.briefing.macro_policy_calendar import load_macro_policy_calendar
from app.briefing.macro_policy_service import (
    build_macro_policy_dashboard,
    build_macro_policy_watch_summary,
)
from app.data_sources.providers.fred import FREDProvider
from app.personalization.user_profile import load_user_profile


class _FakeFred:
    def __init__(self, points: dict[str, object]):
        self._points = points
        self.calls: list[tuple[str, str | None]] = []
        self.local_yoy: dict[str, object] = {}
        self.pc1_points: dict[str, object] = {}

    def is_configured(self) -> bool:
        return True

    def get_latest_observation(self, series_id: str, units: str | None = None):
        self.calls.append((series_id, units))
        if units == "pc1" and series_id in self.pc1_points:
            return self.pc1_points.get(series_id)
        return self._points.get(series_id)

    def get_local_yoy_observation(self, series_id: str, *, window_size: int = 36, min_lookback_days: int = 330):
        _ = window_size, min_lookback_days
        return self.local_yoy.get(series_id)


class _FakeMacroService:
    def __init__(self, points: dict[str, object], curve: list[object], ecb: list[object]):
        self.fred = _FakeFred(points)
        self._curve = curve
        self._ecb = ecb

    def get_ecb_snapshot(self):
        return list(self._ecb)

    def get_yield_curve(self):
        return list(self._curve)


def _point(series_id: str, value: float, change: float = 0.0, date: str = "2026-05-09"):
    return SimpleNamespace(
        series_id=series_id,
        value=value,
        change=change,
        change_percent=None,
        date=date,
        source="stub",
    )


def test_macro_policy_dashboard_schema_is_stable(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    points = {
        "FEDFUNDS": _point("FEDFUNDS", 5.33, 0.00),
        "CPIAUCSL": _point("CPIAUCSL", 318.1, 0.2),
        "CPILFESL": _point("CPILFESL", 325.2, 0.1),
        "PCEPI": _point("PCEPI", 123.4, 0.1),
        "PCEPILFE": _point("PCEPILFE", 124.2, 0.1),
        "GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 4.2, 0.0),
        "UNRATE": _point("UNRATE", 4.1, 0.0),
        "PAYEMS": _point("PAYEMS", 160000, 120),
        "CES0500000003": _point("CES0500000003", 35.0, 0.1),
        "ICSA": _point("ICSA", 220000, -2000),
        "JTSJOL": _point("JTSJOL", 8000, -30),
        "T10Y2Y": _point("T10Y2Y", 0.45, 0.01),
    }
    curve = [
        _point("DGS2", 3.9, 0.02),
        _point("DGS10", 4.3, 0.05),
        _point("DGS30", 4.9, 0.04),
    ]
    ecb = [
        _point("ECB_DFR", 3.75, 0.0),
        _point("ECB_HICP", 2.4, 0.1),
    ]
    svc = _FakeMacroService(points=points, curve=curve, ecb=ecb)

    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )

    for key in (
        "central_bank_policy",
        "inflation_tracker",
        "labour_tracker",
        "rates_yield_curve_panel",
        "macro_catalyst_calendar",
        "portfolio_lens",
        "data_basis",
        "generated_at",
        "status",
        "policy_signals",
    ):
        assert key in payload
    assert "fed_bias" in payload["policy_signals"]
    assert "ecb_bias" in payload["policy_signals"]
    assert "regions" in payload["policy_signals"]
    assert "global_summary" in payload["policy_signals"]
    assert payload["rates_yield_curve_panel"]["curve_shape"] == "normal curve"
    assert payload["rates_yield_curve_panel"]["status"] in {"ok", "partial", "unavailable"}
    assert payload["inflation_tracker"]["series"]["us_cpi"]["label"] in {"US CPI YoY", "US CPI index level"}


def test_inflation_prefers_yoy_transform(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    points = {
        "CPIAUCSL": _point("CPIAUCSL", 330.0, 0.8),
        "CPILFESL": _point("CPILFESL", 334.0, 0.7),
        "PCEPI": _point("PCEPI", 130.3, 0.2),
        "PCEPILFE": _point("PCEPILFE", 129.3, 0.2),
        "GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 136.1, 0.3),
    }
    svc = _FakeMacroService(points=points, curve=[], ecb=[])
    svc.fred.pc1_points = {
        "CPIAUCSL": _point("CPIAUCSL", 3.4, 0.1),
        "CPILFESL": _point("CPILFESL", 3.2, 0.1),
        "PCEPI": _point("PCEPI", 2.6, 0.0),
        "PCEPILFE": _point("PCEPILFE", 2.8, 0.0),
        "GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 3.1, 0.0),
    }
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    us_cpi = payload["inflation_tracker"]["series"]["us_cpi"]
    assert us_cpi["unit"] == "%"
    assert us_cpi["value_kind"] == "rate_yoy"
    assert us_cpi["label"] == "US CPI YoY"
    assert us_cpi["transformation"] in {"fred_units_pc1", "local_yoy"}
    assert ("CPIAUCSL", "pc1") in svc.fred.calls


def test_inflation_raw_index_fallback_is_labelled(validation_test_settings):
    profile = load_user_profile(validation_test_settings)

    class _NoTransformFred(_FakeFred):
        def get_latest_observation(self, series_id: str, units: str | None = None):
            if units == "pc1":
                return None
            return super().get_latest_observation(series_id, units=units)

    svc = _FakeMacroService(points={"CPIAUCSL": _point("CPIAUCSL", 318.1, 0.2)}, curve=[], ecb=[])
    svc.fred = _NoTransformFred({"CPIAUCSL": _point("CPIAUCSL", 318.1, 0.2)})
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    us_cpi = payload["inflation_tracker"]["series"]["us_cpi"]
    assert us_cpi["label"] == "US CPI index level"
    assert us_cpi["value_kind"] == "index_level"
    assert us_cpi.get("fallback_note")
    assert us_cpi["transformation"] == "raw_index_fallback"


def test_local_yoy_transform_used_when_pc1_unavailable(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    svc = _FakeMacroService(points={"CPIAUCSL": _point("CPIAUCSL", 330.0, 1.0)}, curve=[], ecb=[])

    class _Pc1BrokenFred(_FakeFred):
        def get_latest_observation(self, series_id: str, units: str | None = None):
            if units == "pc1":
                return None
            return super().get_latest_observation(series_id, units=units)

    fred = _Pc1BrokenFred({"CPIAUCSL": _point("CPIAUCSL", 330.0, 1.0)})
    fred.local_yoy["CPIAUCSL"] = _point("CPIAUCSL", 3.125, 0.0)
    svc.fred = fred
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    us_cpi = payload["inflation_tracker"]["series"]["us_cpi"]
    assert us_cpi["label"] == "US CPI YoY"
    assert abs(float(us_cpi["value"]) - 3.125) < 1e-6
    assert us_cpi["transformation"] == "local_yoy"


def test_raw_level_never_retains_yoy_label(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    svc = _FakeMacroService(points={"GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 136.1, 0.0)}, curve=[], ecb=[])

    class _IgnoresUnitsFred(_FakeFred):
        def get_latest_observation(self, series_id: str, units: str | None = None):
            # Simulates provider returning raw index even when units=pc1 is requested.
            return super().get_latest_observation(series_id, units=None)

    svc.fred = _IgnoresUnitsFred({"GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 136.1, 0.0)})
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    uk = payload["inflation_tracker"]["series"]["uk_cpi"]
    assert uk["label"] == "UK CPI index level"
    assert uk["value_kind"] == "index_level"
    assert uk["transformation"] == "raw_index_fallback"


def test_fred_local_yoy_computation_from_raw_observations():
    provider = FREDProvider(api_key="x", timeout=1, max_retries=0)

    def _stub_get(_url, params):
        assert params.get("series_id") == "CPIAUCSL"
        return {
            "observations": [
                {"date": "2026-05-01", "value": "330"},
                {"date": "2026-04-01", "value": "329"},
                {"date": "2025-05-01", "value": "320"},
            ]
        }

    provider._get = _stub_get  # type: ignore[assignment]
    point = provider.get_local_yoy_observation("CPIAUCSL")
    assert point is not None
    assert abs(float(point.value) - 3.125) < 1e-6


def test_macro_policy_dashboard_graceful_when_data_missing(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    svc = _FakeMacroService(points={}, curve=[], ecb=[])
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    assert payload["status"] in {"partial", "unavailable"}
    assert payload["central_bank_policy"]["series"]["boe"]["status"] == "unavailable"
    assert payload["inflation_tracker"]["series"]["us_cpi"]["status"] == "unavailable"


def test_macro_calendar_loader_handles_missing_and_malformed(tmp_path):
    missing = load_macro_policy_calendar(configs_dir=str(tmp_path))
    assert missing == []

    cfg = tmp_path / "macro_calendar.yaml"
    cfg.write_text("events: [:::broken", encoding="utf-8")
    malformed = load_macro_policy_calendar(configs_dir=str(tmp_path))
    assert malformed == []


def test_macro_policy_watch_summary_handles_partial():
    summary = build_macro_policy_watch_summary(
        {
            "policy_signals": {
                "fed_bias": {"label": "hold"},
                "ecb_bias": {"label": "uncertain", "confidence": "low"},
                "inflation_pressure": {"label": "uncertain"},
                "labour_pressure": {"label": "balanced"},
                "rates_pressure": {"label": "neutral"},
                "regions": {
                    "uk": {"status": "partial"},
                    "spain": {"status": "partial"},
                    "japan": {"status": "unavailable"},
                    "china": {"status": "unavailable"},
                },
                "portfolio_implications": ["mixed macro posture"],
            }
        }
    )
    assert "MACRO POLICY WATCH" in summary
    assert "Fed: hold" in summary


class _BoomMacroService:
    def __init__(self):
        self.fred = None

    def get_ecb_snapshot(self):
        raise RuntimeError("ecb unavailable")

    def get_yield_curve(self):
        raise RuntimeError("curve unavailable")


def test_macro_policy_dashboard_handles_provider_failures(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=_BoomMacroService(),  # type: ignore[arg-type]
    )
    assert payload["status"] in {"partial", "unavailable"}
    assert payload["central_bank_policy"]["status"] in {"partial", "unavailable"}
    assert payload["rates_yield_curve_panel"]["status"] == "unavailable"
    assert "policy_signals" in payload


def test_missing_date_and_stale_status_handling(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    points = {
        "UNRATE": _point("UNRATE", 4.1, 0.0, date=""),
        "DGS10": _point("DGS10", 4.2, 0.0, date="2024-01-01"),
        "DGS2": _point("DGS2", 4.0, 0.0, date="2024-01-01"),
        "DGS30": _point("DGS30", 4.4, 0.0, date="2024-01-01"),
        "T10Y2Y": _point("T10Y2Y", 0.2, 0.0, date="2024-01-01"),
    }
    svc = _FakeMacroService(points=points, curve=[points["DGS2"], points["DGS10"], points["DGS30"]], ecb=[])
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    unrate = payload["labour_tracker"]["series"]["us_unemployment_rate"]
    assert unrate["status"] == "partial"
    assert unrate["latest_observation_date"] == ""
    ten = payload["rates_yield_curve_panel"]["series"]["us_10y"]
    assert ten["status"] in {"stale", "partial"}
    assert ten["unit"] == "%"
    assert ten["change_unit"] == "pp"


def test_signal_engine_failure_is_non_fatal(validation_test_settings, monkeypatch):
    profile = load_user_profile(validation_test_settings)
    svc = _FakeMacroService(points={}, curve=[], ecb=[])

    def _boom(_payload):
        raise RuntimeError("signal failure")

    monkeypatch.setattr("app.briefing.macro_policy_service.build_policy_signals", _boom)
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    assert payload["policy_signals"]["status"] == "unavailable"
