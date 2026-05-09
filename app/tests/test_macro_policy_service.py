from __future__ import annotations

from types import SimpleNamespace

from app.briefing.macro_policy_calendar import load_macro_policy_calendar
from app.briefing.macro_policy_service import (
    build_macro_policy_dashboard,
    build_macro_policy_watch_summary,
)
from app.personalization.user_profile import load_user_profile


class _FakeFred:
    def __init__(self, points: dict[str, object]):
        self._points = points
        self.calls: list[tuple[str, str | None]] = []

    def is_configured(self) -> bool:
        return True

    def get_latest_observation(self, series_id: str, units: str | None = None):
        self.calls.append((series_id, units))
        return self._points.get(series_id)


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
    ):
        assert key in payload
    assert payload["rates_yield_curve_panel"]["curve_shape"] == "normal curve"
    assert payload["rates_yield_curve_panel"]["status"] in {"ok", "partial", "unavailable"}
    assert payload["inflation_tracker"]["series"]["us_cpi"]["label"] in {"US CPI YoY", "US CPI index level"}


def test_inflation_prefers_yoy_transform(validation_test_settings):
    profile = load_user_profile(validation_test_settings)
    points = {
        "CPIAUCSL": _point("CPIAUCSL", 3.4, 0.1),
        "CPILFESL": _point("CPILFESL", 3.2, 0.1),
        "PCEPI": _point("PCEPI", 2.6, 0.0),
        "PCEPILFE": _point("PCEPILFE", 2.8, 0.0),
        "GBRCPIALLMINMEI": _point("GBRCPIALLMINMEI", 3.1, 0.0),
    }
    svc = _FakeMacroService(points=points, curve=[], ecb=[])
    payload = build_macro_policy_dashboard(
        profile=profile,
        settings=validation_test_settings,
        macro_data_service=svc,  # type: ignore[arg-type]
    )
    us_cpi = payload["inflation_tracker"]["series"]["us_cpi"]
    assert us_cpi["unit"] == "%"
    assert us_cpi["value_kind"] == "rate_yoy"
    assert us_cpi["label"] == "US CPI YoY"
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
            "rates_yield_curve_panel": {"curve_shape": "mixed curve", "rate_impulse": "neutral impulse"},
            "inflation_tracker": {"series": {"us_cpi": {"value": None}}},
            "labour_tracker": {"series": {"us_unemployment_rate": {"value": 4.1, "change": 0.0}}},
            "macro_catalyst_calendar": {"events": [{"title": "Fed Meeting", "date": "2026-06-17"}]},
        }
    )
    assert "Macro Policy Watch:" in summary
    assert "Fed Meeting" in summary


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
