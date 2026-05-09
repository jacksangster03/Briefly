"""Deterministic macro policy signal engine (Phase 1C)."""

from __future__ import annotations

from typing import Any


def build_policy_signals(dashboard_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dashboard_payload or {}
    inflation = classify_inflation_pressure(payload.get("inflation_tracker", {}) or {})
    labour = classify_labour_pressure(payload.get("labour_tracker", {}) or {})
    rates = classify_rates_pressure(payload.get("rates_yield_curve_panel", {}) or {})
    fed = classify_fed_policy_bias(
        inflation_pressure=inflation,
        labour_pressure=labour,
        rates_pressure=rates,
    )
    ecb = classify_ecb_policy_bias(
        inflation_panel=payload.get("inflation_tracker", {}) or {},
        labour_panel=payload.get("labour_tracker", {}) or {},
        policy_panel=payload.get("central_bank_policy", {}) or {},
    )
    regions = _build_region_signals(
        payload=payload,
        fed_bias=fed,
        ecb_bias=ecb,
    )
    global_summary = _build_global_summary(regions)
    implications = build_portfolio_implications(
        inflation_pressure=inflation,
        labour_pressure=labour,
        rates_pressure=rates,
        fed_bias=fed,
        ecb_bias=ecb,
    )
    status = _aggregate_status(
        [inflation.get("status"), labour.get("status"), rates.get("status"), fed.get("status"), ecb.get("status")]
    )
    return {
        "status": status,
        "fed_bias": _strip_status(fed),
        "ecb_bias": _strip_status(ecb),
        "inflation_pressure": _strip_status(inflation),
        "labour_pressure": _strip_status(labour),
        "rates_pressure": _strip_status(rates),
        "portfolio_implications": implications,
        "regions": regions,
        "global_summary": global_summary,
        "methodology_note": "Deterministic signal, not a market-implied probability and not a forecast.",
    }


def classify_inflation_pressure(inflation_panel: dict[str, Any]) -> dict[str, Any]:
    series = inflation_panel.get("series", {}) if isinstance(inflation_panel, dict) else {}
    cpi = _as_float(series.get("us_cpi", {}).get("value"))
    core = _as_float(series.get("us_core_cpi", {}).get("value"))
    pce = _as_float(series.get("us_pce", {}).get("value"))
    core_pce = _as_float(series.get("us_core_pce", {}).get("value"))
    metrics = [v for v in (cpi, core, pce, core_pce) if v is not None]
    drivers: list[str] = []
    missing = _missing_for(series, ("us_cpi", "us_core_cpi", "us_pce", "us_core_pce"))
    if not metrics:
        return _uncertain("inflation_pressure", missing=missing)
    core_ref = core_pce if core_pce is not None else core
    headline_ref = pce if pce is not None else cpi
    if core_ref is not None and core_ref <= 2.4 and (headline_ref is None or headline_ref <= 2.6):
        label = "easing"
        drivers.append(f"Core inflation near target range ({core_ref:.1f}%).")
    elif core_ref is not None and core_ref >= 3.0 and (headline_ref is None or headline_ref >= 2.8):
        label = "reaccelerating"
        drivers.append(f"Core inflation elevated ({core_ref:.1f}%), above target-consistent zone.")
    elif core_ref is not None and core_ref > 2.4:
        label = "sticky"
        drivers.append(f"Core inflation remains above target-consistent zone ({core_ref:.1f}%).")
    else:
        label = "uncertain"
    if headline_ref is not None:
        drivers.append(f"Headline inflation reference {headline_ref:.1f}% YoY.")
    return _signal(
        label=label,
        confidence=_confidence(len(metrics), missing),
        drivers=drivers,
        missing=missing,
        status=_status_for_signal(missing),
    )


def classify_labour_pressure(labour_panel: dict[str, Any]) -> dict[str, Any]:
    series = labour_panel.get("series", {}) if isinstance(labour_panel, dict) else {}
    unrate = _as_float(series.get("us_unemployment_rate", {}).get("value"))
    claims = _as_float(series.get("us_initial_claims", {}).get("value"))
    wage_yoy = _as_float(series.get("us_wage_growth", {}).get("value"))
    payroll_chg = _as_float(series.get("us_payrolls", {}).get("change"))
    jolts_chg = _as_float(series.get("us_jolts_openings", {}).get("change"))
    drivers: list[str] = []
    missing = _missing_for(series, ("us_unemployment_rate", "us_initial_claims", "us_wage_growth"))
    available_count = len([x for x in (unrate, claims, wage_yoy, payroll_chg, jolts_chg) if x is not None])
    if available_count == 0:
        return _uncertain("labour_pressure", missing=missing)
    cooling_score = 0
    tight_score = 0
    if unrate is not None:
        if unrate >= 4.3:
            cooling_score += 1
        elif unrate <= 3.8:
            tight_score += 1
        drivers.append(f"Unemployment at {unrate:.1f}%.")
    if claims is not None:
        if claims >= 250_000:
            cooling_score += 1
        elif claims <= 220_000:
            tight_score += 1
        drivers.append(f"Initial claims {int(claims):,}.")
    if wage_yoy is not None:
        if wage_yoy >= 4.0:
            tight_score += 1
        elif wage_yoy <= 3.0:
            cooling_score += 1
        drivers.append(f"Wage growth {wage_yoy:.1f}% YoY.")
    if payroll_chg is not None:
        if payroll_chg < 0:
            cooling_score += 1
            drivers.append("Payroll change softened vs prior observation.")
        elif payroll_chg > 0:
            tight_score += 1
    if jolts_chg is not None and jolts_chg < 0:
        cooling_score += 1
        drivers.append("JOLTS openings declining.")
    if cooling_score >= tight_score + 1:
        label = "cooling"
    elif tight_score >= cooling_score + 1:
        label = "tight"
    else:
        label = "balanced" if available_count >= 2 else "uncertain"
    return _signal(
        label=label,
        confidence=_confidence(available_count, missing),
        drivers=drivers,
        missing=missing,
        status=_status_for_signal(missing),
    )


def classify_rates_pressure(rates_panel: dict[str, Any]) -> dict[str, Any]:
    series = rates_panel.get("series", {}) if isinstance(rates_panel, dict) else {}
    two_chg = _as_float(series.get("us_2y", {}).get("change"))
    ten_chg = _as_float(series.get("us_10y", {}).get("change"))
    drivers: list[str] = []
    missing = _missing_for(series, ("us_2y", "us_10y"))
    available_count = len([x for x in (two_chg, ten_chg) if x is not None])
    if available_count == 0:
        return _uncertain("rates_pressure", missing=missing)
    avg = sum(x for x in (two_chg, ten_chg) if x is not None) / available_count
    if avg <= -0.03:
        label = "easing"
        drivers.append("Front-end and/or long-end yields are falling.")
    elif avg >= 0.03:
        label = "tightening"
        drivers.append("Front-end and/or long-end yields are rising.")
    else:
        label = "neutral"
        drivers.append("Yield moves are modest/offsetting.")
    if two_chg is not None:
        drivers.append(f"2Y change {two_chg:+.2f}pp.")
    if ten_chg is not None:
        drivers.append(f"10Y change {ten_chg:+.2f}pp.")
    return _signal(
        label=label,
        confidence=_confidence(available_count, missing),
        drivers=drivers,
        missing=missing,
        status=_status_for_signal(missing),
    )


def classify_fed_policy_bias(
    *,
    inflation_pressure: dict[str, Any],
    labour_pressure: dict[str, Any],
    rates_pressure: dict[str, Any],
) -> dict[str, Any]:
    drivers: list[str] = []
    missing: list[str] = []
    infl = inflation_pressure.get("label", "uncertain")
    lab = labour_pressure.get("label", "uncertain")
    rat = rates_pressure.get("label", "uncertain")
    if infl == "easing" and lab == "cooling" and rat == "easing":
        label = "cut_leaning"
        drivers.append("Inflation easing + labour cooling + rates easing.")
    elif infl in {"reaccelerating", "sticky"} and lab == "tight" and rat == "tightening":
        label = "hike_leaning"
        drivers.append("Inflation pressure + tight labour + tightening rates.")
    elif infl == "uncertain" or lab == "uncertain":
        label = "uncertain"
    else:
        label = "hold"
        drivers.append("Cross-signals mixed; deterministic hold bias.")
    confidence = _bias_confidence(label, inflation_pressure, labour_pressure, rates_pressure, missing)
    risks = [
        "Deterministic policy bias is not a market-implied probability.",
        "Signal is not a forecast and can flip on new releases.",
    ]
    return {
        "status": _status_for_signal(missing),
        "label": label,
        "confidence": confidence,
        "drivers": drivers + _collect_driver_snippets(inflation_pressure, labour_pressure, rates_pressure),
        "missing": missing,
        "risks": risks,
    }


def classify_ecb_policy_bias(
    *,
    inflation_panel: dict[str, Any],
    labour_panel: dict[str, Any],
    policy_panel: dict[str, Any],
) -> dict[str, Any]:
    inf_series = inflation_panel.get("series", {}) if isinstance(inflation_panel, dict) else {}
    lab_series = labour_panel.get("series", {}) if isinstance(labour_panel, dict) else {}
    cb_series = policy_panel.get("series", {}) if isinstance(policy_panel, dict) else {}

    hicp = _as_float((inf_series.get("eurozone_hicp") or {}).get("value"))
    ecb_rate = _as_float((cb_series.get("ecb") or {}).get("value"))
    euro_unrate = _as_float((lab_series.get("euro_area_unemployment_rate") or {}).get("value"))

    missing: list[str] = []
    if hicp is None:
        missing.append("eurozone_hicp")
    if ecb_rate is None:
        missing.append("ecb_deposit_rate")
    if euro_unrate is None:
        missing.append("euro_area_unemployment_rate")

    drivers: list[str] = []
    label = "uncertain"
    confidence = "low"

    if hicp is not None:
        drivers.append(f"Eurozone HICP at {hicp:.1f}% YoY.")
    if ecb_rate is not None:
        drivers.append(f"ECB deposit facility at {ecb_rate:.2f}%.")
    if euro_unrate is not None:
        drivers.append(f"Euro area unemployment at {euro_unrate:.1f}%.")

    if hicp is not None and ecb_rate is not None:
        if hicp <= 2.2 and (euro_unrate is None or euro_unrate >= 6.5):
            label = "cut_leaning"
            confidence = "medium" if euro_unrate is not None else "low"
            drivers.append("Disinflation plus softer labour backdrop supports easing bias.")
        elif hicp >= 2.8 and (euro_unrate is None or euro_unrate <= 6.0):
            label = "hike_leaning"
            confidence = "medium" if euro_unrate is not None else "low"
            drivers.append("Inflation pressure with firm labour backdrop supports tighter bias.")
        else:
            label = "hold"
            confidence = "medium" if euro_unrate is not None else "low"
            drivers.append("Eurozone inputs are mixed; deterministic hold bias.")

    # Explicitly constrain ECB to Eurozone data and only mention US as spillover context.
    drivers.append("US inflation/labour are treated as spillover context, not direct ECB drivers.")
    risks = [
        "Deterministic policy bias is not a market-implied probability.",
        "Signal is not a forecast and can flip on new releases.",
    ]
    return {
        "status": _status_for_signal(missing),
        "label": label,
        "confidence": confidence,
        "drivers": drivers[:5],
        "missing": missing[:6],
        "risks": risks,
    }


def build_portfolio_implications(
    *,
    inflation_pressure: dict[str, Any],
    labour_pressure: dict[str, Any],
    rates_pressure: dict[str, Any],
    fed_bias: dict[str, Any],
    ecb_bias: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    if fed_bias.get("label") == "cut_leaning":
        out.append("Duration/growth sleeves may find support if easing trend persists.")
    if fed_bias.get("label") == "hike_leaning":
        out.append("Higher-rate sensitivity risk remains for long-duration equities and bonds.")
    if rates_pressure.get("label") == "tightening":
        out.append("Watch bond duration and valuation-sensitive growth exposure.")
    if rates_pressure.get("label") == "easing":
        out.append("Easing rates pressure can support duration assets, but confirm via labour/inflation follow-through.")
    if ecb_bias.get("label") == "uncertain":
        out.append("ECB signal confidence is limited by partial Eurozone inputs.")
    if not out:
        out.append("Macro signal is mixed; keep sizing disciplined and catalyst-driven.")
    return out[:4]


def _signal(*, label: str, confidence: str, drivers: list[str], missing: list[str], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "confidence": confidence,
        "drivers": drivers[:4],
        "missing": missing[:6],
    }


def _uncertain(_name: str, *, missing: list[str]) -> dict[str, Any]:
    return _signal(
        label="uncertain",
        confidence="low",
        drivers=[],
        missing=missing,
        status="unavailable" if missing else "partial",
    )


def _status_for_signal(missing: list[str]) -> str:
    return "partial" if missing else "ok"


def _missing_for(series: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    missing = []
    for key in keys:
        row = series.get(key, {}) if isinstance(series, dict) else {}
        if row.get("status") in {"unavailable", None} or row.get("value") is None:
            missing.append(key)
    return missing


def _confidence(available_count: int, missing: list[str]) -> str:
    if available_count >= 4 and not missing:
        return "high"
    if available_count >= 2:
        return "medium"
    return "low"


def _bias_confidence(label: str, inflation: dict[str, Any], labour: dict[str, Any], rates: dict[str, Any], missing: list[str]) -> str:
    if label == "uncertain":
        return "low"
    rank = {"low": 1, "medium": 2, "high": 3}
    inv = {1: "low", 2: "medium", 3: "high"}
    scores = [
        rank.get(str(inflation.get("confidence", "low")), 1),
        rank.get(str(labour.get("confidence", "low")), 1),
        rank.get(str(rates.get("confidence", "low")), 1),
    ]
    base = inv.get(min(scores), "low")
    if missing:
        return "low" if base == "medium" else base
    return base


def _collect_driver_snippets(*signals: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for sig in signals:
        for line in sig.get("drivers", [])[:1]:
            if line and line not in out:
                out.append(str(line))
    return out[:3]


def _strip_status(signal: dict[str, Any]) -> dict[str, Any]:
    out = dict(signal)
    out.pop("status", None)
    return out


def _aggregate_status(values: list[str | None]) -> str:
    vals = [v for v in values if v]
    if not vals:
        return "unavailable"
    if all(v == "ok" for v in vals):
        return "ok"
    if all(v == "unavailable" for v in vals):
        return "unavailable"
    return "partial"


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _build_region_signals(
    *,
    payload: dict[str, Any],
    fed_bias: dict[str, Any],
    ecb_bias: dict[str, Any],
) -> dict[str, Any]:
    inflation_series = ((payload.get("inflation_tracker") or {}).get("series") or {})
    labour_series = ((payload.get("labour_tracker") or {}).get("series") or {})

    us = {
        "region_key": "us",
        "region_label": "United States",
        "scope": "central_bank",
        "central_bank": "Fed",
        "status": fed_bias.get("status", "partial"),
        "policy_bias": _strip_status(fed_bias),
    }
    eurozone = {
        "region_key": "eurozone",
        "region_label": "Eurozone",
        "scope": "central_bank",
        "central_bank": "ECB",
        "status": ecb_bias.get("status", "partial"),
        "policy_bias": _strip_status(ecb_bias),
    }

    placeholders = {
        "uk": _region_placeholder("uk", "United Kingdom", "BoE"),
        "japan": _region_placeholder("japan", "Japan", "BoJ"),
        "china": _region_placeholder("china", "China", "PBoC"),
    }

    spain_missing: list[str] = []
    hicp = _as_float((inflation_series.get("eurozone_hicp") or {}).get("value"))
    if hicp is None:
        spain_missing.append("eurozone_hicp_proxy")
    ibex = _as_float((labour_series.get("spain_ibex_proxy") or {}).get("value"))
    if ibex is None:
        spain_missing.append("spain_market_proxy")
    spain = {
        "region_key": "spain",
        "region_label": "Spain",
        "scope": "country_lens",
        "status": "partial",
        "policy_bias": {
            "label": "uncertain",
            "confidence": "low",
            "drivers": [
                "Spain is modeled as a country lens inside Eurozone policy, not a standalone central bank bias."
            ],
            "missing": spain_missing or ["country_specific_policy_inputs"],
            "risks": ["Use Eurozone ECB signal as primary policy anchor for Spain in Phase 1."],
        },
    }

    regions: dict[str, Any] = {
        "us": us,
        "eurozone": eurozone,
        "uk": placeholders["uk"],
        "japan": placeholders["japan"],
        "china": placeholders["china"],
        "spain": spain,
    }
    return regions


def _region_placeholder(key: str, label: str, central_bank: str) -> dict[str, Any]:
    return {
        "region_key": key,
        "region_label": label,
        "scope": "central_bank",
        "central_bank": central_bank,
        "status": "unavailable",
        "policy_bias": {
            "label": "uncertain",
            "confidence": "low",
            "drivers": [f"{central_bank} regional signal is not wired in Phase 1."],
            "missing": [f"{key}_inflation", f"{key}_labour", f"{key}_policy_rate"],
            "risks": ["Placeholder only; no regional deterministic bias yet."],
        },
    }


def _build_global_summary(regions: dict[str, Any]) -> dict[str, Any]:
    us = ((regions.get("us") or {}).get("policy_bias") or {})
    euro = ((regions.get("eurozone") or {}).get("policy_bias") or {})
    status_values = [
        str((regions.get("us") or {}).get("status", "unavailable")),
        str((regions.get("eurozone") or {}).get("status", "unavailable")),
    ]
    return {
        "status": _aggregate_status(status_values),
        "fed_bias": us.get("label", "uncertain"),
        "ecb_bias": euro.get("label", "uncertain"),
        "note": "Regional policy bias is deterministic, non-probabilistic, and not a forecast.",
    }
