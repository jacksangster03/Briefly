"""Deterministic macro policy dashboard service."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

from app.briefing.macro_policy_calendar import load_macro_policy_calendar, upcoming_macro_events
from app.briefing.macro_policy_lens import build_macro_portfolio_lens
from app.briefing.macro_policy_signals import build_policy_signals
from app.data_sources.macro_data import MacroDataService
from app.personalization.user_profile import UserProfile
from app.settings import Settings


def build_macro_policy_dashboard(
    *,
    profile: UserProfile,
    settings: Settings,
    macro_data_service: MacroDataService | None = None,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    local_tz = ZoneInfo(profile.timezone or settings.timezone or "Europe/Madrid")
    local_now = now_utc.astimezone(local_tz)
    try:
        svc = macro_data_service or MacroDataService(settings)
    except Exception:
        svc = None

    policy = _safe_panel(lambda: _build_central_bank_policy(svc), panel_name="central_bank_policy")
    inflation = _safe_panel(lambda: _build_inflation_tracker(svc), panel_name="inflation_tracker")
    labour = _safe_panel(lambda: _build_labour_tracker(svc), panel_name="labour_tracker")
    rates = _safe_panel(lambda: _build_rates_panel(svc), panel_name="rates_yield_curve_panel")
    calendar = _safe_panel(lambda: _build_calendar(settings=settings, local_now=local_now), panel_name="macro_catalyst_calendar")
    lens = _safe_panel(lambda: build_macro_portfolio_lens(profile), panel_name="portfolio_lens")

    try:
        policy_signals = build_policy_signals(
            {
                "central_bank_policy": policy,
                "inflation_tracker": inflation,
                "labour_tracker": labour,
                "rates_yield_curve_panel": rates,
                "portfolio_lens": lens,
            }
        )
    except Exception:
        policy_signals = {
            "status": "unavailable",
            "fed_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"], "risks": []},
            "ecb_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"], "risks": []},
            "inflation_pressure": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"]},
            "labour_pressure": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"]},
            "rates_pressure": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"]},
            "portfolio_implications": ["Policy signal unavailable due to partial data or service error."],
            "regions": {
                "us": {"status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"], "risks": []}},
                "eurozone": {"status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": [], "missing": ["signal_engine_error"], "risks": []}},
                "uk": {"status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": ["not_wired"], "missing": ["not_wired"], "risks": []}},
                "japan": {"status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": ["not_wired"], "missing": ["not_wired"], "risks": []}},
                "china": {"status": "unavailable", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": ["not_wired"], "missing": ["not_wired"], "risks": []}},
                "spain": {"status": "partial", "policy_bias": {"label": "uncertain", "confidence": "low", "drivers": ["country_lens"], "missing": ["signal_engine_error"], "risks": []}},
            },
            "global_summary": {
                "status": "unavailable",
                "fed_bias": "uncertain",
                "ecb_bias": "uncertain",
                "note": "Regional policy signal unavailable.",
            },
            "methodology_note": "Deterministic signal unavailable. Not a forecast.",
        }

    panels = [policy, inflation, labour, rates, calendar, lens, policy_signals]
    overall_status = _aggregate_status([str(panel.get("status", "unavailable")) for panel in panels])
    return {
        "generated_at": now_utc.isoformat(),
        "status": overall_status,
        "central_bank_policy": policy,
        "inflation_tracker": inflation,
        "labour_tracker": labour,
        "rates_yield_curve_panel": rates,
        "macro_catalyst_calendar": calendar,
        "portfolio_lens": lens,
        "policy_signals": policy_signals,
        "data_basis": {
            "macro_sources": "FRED + ECB + Eurostat + local deterministic calendar seed",
            "timezone": str(local_tz.key) if hasattr(local_tz, "key") else (profile.timezone or settings.timezone),
            "freshness_note": "Official-release macro series can lag live market prices by design.",
        },
    }


def build_macro_policy_watch_summary(payload: dict[str, Any]) -> str:
    """Compact deterministic summary string for optional briefing inclusion."""
    if not payload:
        return "Macro Policy Watch unavailable."
    rates = payload.get("rates_yield_curve_panel", {}) or {}
    inflation = payload.get("inflation_tracker", {}) or {}
    labour = payload.get("labour_tracker", {}) or {}
    calendar = payload.get("macro_catalyst_calendar", {}) or {}
    curve = str(rates.get("curve_shape", "mixed curve"))
    impulse = str(rates.get("rate_impulse", "neutral rates impulse"))
    cpi = _value_line(inflation.get("series", {}).get("us_cpi"))
    unrate = _value_line(labour.get("series", {}).get("us_unemployment_rate"))
    next_evt = ""
    events = list(calendar.get("events", []) or [])
    if events:
        evt = events[0]
        next_evt = f"Next catalyst: {evt.get('title', 'macro event')} ({evt.get('date', '')})."
    return (
        f"Macro Policy Watch: {curve}; {impulse}. "
        f"US CPI {cpi}; US unemployment {unrate}. "
        f"{next_evt}".strip()
    )


def _build_central_bank_policy(svc: MacroDataService) -> dict[str, Any]:
    if svc is None:
        return _panel_unavailable("macro service unavailable")
    fed = _fred_point(svc, "FEDFUNDS")
    ecb_points = {p.series_id: p for p in (svc.get_ecb_snapshot() or [])}
    ecb = ecb_points.get("ECB_DFR")
    banks = {
        "fed": _series_payload(fed, label="Fed policy rate (effective)"),
        "ecb": _series_payload(ecb, label="ECB deposit facility"),
        "boe": _placeholder("not_wired"),
        "boj": _placeholder("not_wired"),
        "snb": _placeholder("not_wired"),
    }
    return {
        "status": _aggregate_status([str(v.get("status", "unavailable")) for v in banks.values()]),
        "series": banks,
        "data_basis": "Fed from FRED; ECB from ECB SDMX; BoE/BoJ/SNB placeholders in Phase 1",
    }


def _build_inflation_tracker(svc: MacroDataService) -> dict[str, Any]:
    if svc is None:
        return _panel_unavailable("macro service unavailable")
    ecb_points = {p.series_id: p for p in (svc.get_ecb_snapshot() or [])}
    series = {
        "us_cpi": _inflation_series_payload(
            transformed=_fred_point(svc, "CPIAUCSL", units="pc1"),
            raw=_fred_point(svc, "CPIAUCSL"),
            local_yoy=_fred_local_yoy_point(svc, "CPIAUCSL"),
            yoy_label="US CPI YoY",
            raw_label="US CPI index level",
        ),
        "us_core_cpi": _inflation_series_payload(
            transformed=_fred_point(svc, "CPILFESL", units="pc1"),
            raw=_fred_point(svc, "CPILFESL"),
            local_yoy=_fred_local_yoy_point(svc, "CPILFESL"),
            yoy_label="US Core CPI YoY",
            raw_label="US Core CPI index level",
        ),
        "us_pce": _inflation_series_payload(
            transformed=_fred_point(svc, "PCEPI", units="pc1"),
            raw=_fred_point(svc, "PCEPI"),
            local_yoy=_fred_local_yoy_point(svc, "PCEPI"),
            yoy_label="US PCE YoY",
            raw_label="US PCE index level",
        ),
        "us_core_pce": _inflation_series_payload(
            transformed=_fred_point(svc, "PCEPILFE", units="pc1"),
            raw=_fred_point(svc, "PCEPILFE"),
            local_yoy=_fred_local_yoy_point(svc, "PCEPILFE"),
            yoy_label="US Core PCE YoY",
            raw_label="US Core PCE index level",
        ),
        "eurozone_hicp": _series_payload(
            ecb_points.get("ECB_HICP"),
            label="Eurozone HICP YoY",
            unit="%",
            value_kind="rate_yoy",
        ),
        "uk_cpi": _inflation_series_payload(
            transformed=_fred_point(svc, "GBRCPIALLMINMEI", units="pc1"),
            raw=_fred_point(svc, "GBRCPIALLMINMEI"),
            local_yoy=_fred_local_yoy_point(svc, "GBRCPIALLMINMEI"),
            yoy_label="UK CPI YoY",
            raw_label="UK CPI index level",
        ),
    }
    return {
        "status": _aggregate_status([str(v.get("status", "unavailable")) for v in series.values()]),
        "series": series,
        "data_basis": "FRED + ECB official series (headline metrics prioritized in Phase 1)",
    }


def _build_labour_tracker(svc: MacroDataService) -> dict[str, Any]:
    if svc is None:
        return _panel_unavailable("macro service unavailable")
    series = {
        "us_unemployment_rate": _series_payload(
            _fred_point(svc, "UNRATE"),
            label="US unemployment rate",
            unit="%",
            value_kind="rate_level",
        ),
        "us_payrolls": _series_payload(
            _fred_point(svc, "PAYEMS"),
            label="US nonfarm payrolls level",
            unit="thousands",
            value_kind="level",
        ),
        "us_wage_growth": _inflation_series_payload(
            transformed=_fred_point(svc, "CES0500000003", units="pc1"),
            raw=_fred_point(svc, "CES0500000003"),
            local_yoy=_fred_local_yoy_point(svc, "CES0500000003"),
            yoy_label="US average hourly earnings YoY",
            raw_label="US average hourly earnings level",
            raw_unit="USD/hour",
        ),
        "us_initial_claims": _series_payload(
            _fred_point(svc, "ICSA"),
            label="US initial claims",
            unit="claims",
            value_kind="count",
        ),
        "us_jolts_openings": _series_payload(
            _fred_point(svc, "JTSJOL"),
            label="US JOLTS openings level",
            unit="thousands",
            value_kind="level",
        ),
    }
    return {
        "status": _aggregate_status([str(v.get("status", "unavailable")) for v in series.values()]),
        "series": series,
        "data_basis": "FRED labour series (availability depends on configured series coverage)",
    }


def _build_rates_panel(svc: MacroDataService) -> dict[str, Any]:
    if svc is None:
        return _panel_unavailable("macro service unavailable")
    curve = {p.series_id: p for p in (svc.get_yield_curve() or [])}
    two = curve.get("DGS2")
    ten = curve.get("DGS10")
    thirty = curve.get("DGS30")
    spread = _fred_point(svc, "T10Y2Y")
    series = {
        "us_2y": _series_payload(two, label="US 2Y yield", unit="%", value_kind="rate_level"),
        "us_10y": _series_payload(ten, label="US 10Y yield", unit="%", value_kind="rate_level"),
        "us_30y": _series_payload(thirty, label="US 30Y yield", unit="%", value_kind="rate_level"),
        "spread_10y_2y": _series_payload(spread, label="10Y-2Y spread", unit="pp", value_kind="spread"),
    }
    curve_shape = _curve_shape(two, ten, thirty)
    rate_impulse = _rate_impulse(ten)
    interpretation = _portfolio_interpretation(rate_impulse=rate_impulse, curve_shape=curve_shape)
    return {
        "status": _aggregate_status([str(v.get("status", "unavailable")) for v in series.values()]),
        "series": series,
        "curve_shape": curve_shape,
        "rate_impulse": rate_impulse,
        "portfolio_interpretation": interpretation,
        "data_basis": "FRED Treasury series; impulse based on latest 10Y change.",
    }


def _build_calendar(*, settings: Settings, local_now: datetime) -> dict[str, Any]:
    events = load_macro_policy_calendar(configs_dir=settings.configs_dir)
    upcoming = upcoming_macro_events(events, today=local_now.date(), horizon_days=14)
    status = "ok" if upcoming else "partial"
    return {
        "status": status,
        "events": upcoming[:20],
        "data_basis": "Local deterministic calendar seed (configs/macro_calendar.yaml)",
    }


def _fred_point(svc: MacroDataService, series_id: str, units: str | None = None):
    if svc is None:
        return None
    if not svc.fred or not svc.fred.is_configured():
        return None
    try:
        return svc.fred.get_latest_observation(series_id, units=units)
    except TypeError:
        # Backward compatibility with older provider signatures.
        return svc.fred.get_latest_observation(series_id)


def _fred_local_yoy_point(svc: MacroDataService, series_id: str):
    if svc is None or not svc.fred or not svc.fred.is_configured():
        return None
    provider = svc.fred
    fn = getattr(provider, "get_local_yoy_observation", None)
    if callable(fn):
        try:
            return fn(series_id)
        except Exception:
            return None
    return None


def _safe_panel(builder, *, panel_name: str) -> dict[str, Any]:
    try:
        payload = builder()
        if isinstance(payload, dict):
            payload.setdefault("status", "partial")
            payload.setdefault("data_basis", "")
            return payload
    except Exception:
        pass
    return _panel_unavailable(f"{panel_name} unavailable")


def _panel_unavailable(note: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "data_basis": note,
        "series": {},
        "events": [],
        "summary": note,
    }


def _series_payload(
    point,
    *,
    label: str,
    unit: str = "",
    value_kind: str = "level",
) -> dict[str, Any]:
    change_unit = _change_unit_for_value_kind(value_kind)
    if point is None:
        return {
            "status": "unavailable",
            "label": label,
            "value": None,
            "change": None,
            "change_percent": None,
            "change_unit": change_unit,
            "date": "",
            "latest_observation_date": "",
            "source": "",
            "unit": unit,
            "value_kind": value_kind,
            "freshness": "unavailable",
        }
    freshness = _freshness_label(point.date)
    status = _status_from_freshness(freshness)
    return {
        "status": status,
        "label": label,
        "value": point.value,
        "change": point.change,
        "change_percent": point.change_percent,
        "change_unit": change_unit,
        "date": point.date,
        "latest_observation_date": point.date,
        "source": point.source,
        "unit": unit,
        "value_kind": value_kind,
        "freshness": freshness,
    }


def _inflation_series_payload(
    *,
    transformed,
    raw,
    local_yoy,
    yoy_label: str,
    raw_label: str,
    raw_unit: str = "index",
) -> dict[str, Any]:
    transformed_valid = transformed is not None and not _looks_like_raw_index_for_yoy(transformed=transformed, raw=raw)
    if transformed_valid:
        payload = _series_payload(
            transformed,
            label=yoy_label,
            unit="%",
            value_kind="rate_yoy",
        )
        payload["transformation"] = "fred_units_pc1"
        return payload
    if local_yoy is not None:
        payload = _series_payload(
            local_yoy,
            label=yoy_label,
            unit="%",
            value_kind="rate_yoy",
        )
        payload["transformation"] = "local_yoy"
        return payload
    payload = _series_payload(
        raw,
        label=raw_label,
        unit=raw_unit,
        value_kind="index_level",
    )
    if payload.get("status") != "unavailable":
        payload["status"] = "partial"
        payload["fallback_note"] = "YoY transform unavailable; showing index level."
        payload["note"] = "yoy_transform_unavailable"
    payload["transformation"] = "raw_index_fallback"
    return payload


def _looks_like_raw_index_for_yoy(*, transformed, raw) -> bool:
    try:
        t = float(getattr(transformed, "value", None))
        r = float(getattr(raw, "value", None)) if raw is not None else None
    except Exception:
        return False
    # If transformed value equals raw level, units transform likely ignored.
    if r is not None and abs(t - r) < 1e-9:
        return True
    # Defensive sanity check: YoY inflation rates should not look like index levels.
    if abs(t) > 60:
        return True
    return False


def _freshness_label(date_str: str) -> str:
    if not date_str:
        return "unknown"
    try:
        dt = datetime.strptime(str(date_str), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    age_days = (datetime.now(timezone.utc) - dt).days
    if age_days <= 7:
        return "fresh"
    if age_days <= 45:
        return "stale"
    return "old"


def _status_from_freshness(freshness: str) -> str:
    if freshness == "fresh":
        return "ok"
    if freshness in {"stale", "old"}:
        return "stale"
    if freshness == "unavailable":
        return "unavailable"
    return "partial"


def _change_unit_for_value_kind(value_kind: str) -> str:
    kind = (value_kind or "").strip().lower()
    if kind in {"rate_yoy", "rate_level", "spread"}:
        return "pp"
    if kind == "count":
        return "count"
    if kind in {"index_level", "level"}:
        return "level"
    return ""


def _aggregate_status(statuses: list[str]) -> str:
    vals = [s for s in statuses if s]
    if not vals:
        return "unavailable"
    if all(s == "ok" for s in vals):
        return "ok"
    if all(s == "stale" for s in vals):
        return "stale"
    if any(s == "ok" for s in vals):
        return "partial"
    if any(s == "stale" for s in vals):
        return "partial"
    if any(s == "partial" for s in vals):
        return "partial"
    return "unavailable"


def _placeholder(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "value": None,
        "change": None,
        "change_percent": None,
        "date": "",
        "source": "",
        "freshness": "unavailable",
        "note": reason,
    }


def _curve_shape(two, ten, thirty) -> str:
    vals = []
    for item in (two, ten, thirty):
        if item is None:
            return "unavailable"
        vals.append(float(item.value or 0.0))
    two_v, ten_v, thirty_v = vals
    if ten_v < two_v:
        return "inverted curve"
    if two_v < ten_v < thirty_v:
        return "normal curve"
    return "mixed curve"


def _rate_impulse(ten) -> str:
    if ten is None or ten.change is None:
        return "unavailable"
    delta_bp = float(ten.change) * 100.0
    if delta_bp >= 7:
        return "sharp bear-steepening pressure"
    if delta_bp >= 3:
        return "higher-rate pressure"
    if delta_bp <= -7:
        return "sharp duration relief"
    if delta_bp <= -3:
        return "moderate duration relief"
    return "neutral impulse"


def _portfolio_interpretation(*, rate_impulse: str, curve_shape: str) -> str:
    if rate_impulse in {"sharp bear-steepening pressure", "higher-rate pressure"}:
        return "Higher long-end yields can pressure duration-heavy bonds and growth valuations."
    if rate_impulse in {"sharp duration relief", "moderate duration relief"}:
        return "Falling long-end yields can support duration assets, though growth-risk signaling remains mixed."
    if curve_shape == "inverted curve":
        return "Inverted curve still flags tighter financial conditions and growth fragility."
    return "Macro rates signal is balanced; monitor incoming inflation/labour catalysts."


def _value_line(series_row: dict[str, Any] | None) -> str:
    row = series_row or {}
    if row.get("value") is None:
        return "unavailable"
    val = float(row.get("value") or 0.0)
    label = f"{val:.2f}"
    chg = row.get("change")
    if chg is None:
        return label
    sign = "+" if float(chg) >= 0 else ""
    return f"{label} ({sign}{float(chg):.2f})"
