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
            if euro_unrate is not None:
                drivers.append("Disinflation plus softer labour backdrop supports easing bias.")
            else:
                drivers.append("Eurozone disinflation supports an easing bias, but labour confirmation is unavailable.")
        elif hicp >= 2.8 and (euro_unrate is None or euro_unrate <= 6.0):
            label = "hike_leaning"
            confidence = "medium" if euro_unrate is not None else "low"
            if euro_unrate is not None:
                drivers.append("Inflation pressure with firm labour backdrop supports tighter bias.")
            else:
                drivers.append("Inflation pressure supports tighter bias, but labour confirmation is unavailable.")
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
    policy_series = ((payload.get("central_bank_policy") or {}).get("series") or {})
    rates_series = ((payload.get("rates_yield_curve_panel") or {}).get("series") or {})

    us = {
        "region_key": "us",
        "region_label": "United States",
        "scope": "central_bank",
        "central_bank": "Fed",
        "status": fed_bias.get("status", "partial"),
        "policy_bias": _strip_status(fed_bias),
        "inflation_pressure": {"label": "linked_global", "drivers": ["Uses US CPI/Core CPI/PCE/Core PCE deterministic stack."], "missing": []},
        "labour_pressure": {"label": "linked_global", "drivers": ["Uses US unemployment/claims/wages deterministic stack."], "missing": []},
        "rates_pressure": {"label": "linked_global", "drivers": ["Uses US Treasury 2Y/10Y moves."], "missing": []},
        "drivers": list((_strip_status(fed_bias).get("drivers") or [])[:3]),
        "missing": list((_strip_status(fed_bias).get("missing") or [])[:6]),
        "risks": list((_strip_status(fed_bias).get("risks") or [])[:3]),
        "data_basis": "US macro deterministic stack (FRED + Treasury yields).",
    }

    eurozone = {
        "region_key": "eurozone",
        "region_label": "Eurozone",
        "scope": "central_bank",
        "central_bank": "ECB",
        "status": ecb_bias.get("status", "partial"),
        "policy_bias": _strip_status(ecb_bias),
        "inflation_pressure": {"label": "regional", "drivers": ["Eurozone HICP and ECB policy anchor."], "missing": []},
        "labour_pressure": {"label": "regional", "drivers": ["Euro area unemployment when available."], "missing": []},
        "rates_pressure": {"label": "regional", "drivers": ["ECB rate anchor; market rates proxies limited in Phase 1."], "missing": ["eurozone_rates_proxy"]},
        "drivers": list((_strip_status(ecb_bias).get("drivers") or [])[:3]),
        "missing": list((_strip_status(ecb_bias).get("missing") or [])[:6]),
        "risks": list((_strip_status(ecb_bias).get("risks") or [])[:3]),
        "data_basis": "Eurozone deterministic stack (ECB + Eurozone HICP + Euro labour when available).",
    }

    uk = _build_uk_region(
        inflation_series=inflation_series,
        labour_series=labour_series,
        policy_series=policy_series,
    )
    spain = _build_spain_region(
        inflation_series=inflation_series,
        labour_series=labour_series,
        rates_series=rates_series,
        ecb_bias=_strip_status(ecb_bias),
    )
    japan = _build_asia_central_bank_region(
        key="japan",
        label="Japan",
        central_bank="BoJ",
        inflation_series_key="japan_cpi",
        labour_series_key="japan_unemployment_rate",
        policy_series_key="boj",
        inflation_series=inflation_series,
        labour_series=labour_series,
        policy_series=policy_series,
    )
    china = _build_asia_central_bank_region(
        key="china",
        label="China",
        central_bank="PBoC",
        inflation_series_key="china_cpi",
        labour_series_key="china_unemployment_rate",
        policy_series_key="pboc",
        inflation_series=inflation_series,
        labour_series=labour_series,
        policy_series=policy_series,
    )
    return {
        "us": us,
        "eurozone": eurozone,
        "uk": uk,
        "japan": japan,
        "china": china,
        "spain": spain,
    }


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


def _build_uk_region(
    *,
    inflation_series: dict[str, Any],
    labour_series: dict[str, Any],
    policy_series: dict[str, Any],
) -> dict[str, Any]:
    uk_cpi = _as_float((inflation_series.get("uk_cpi") or {}).get("value"))
    uk_unrate = _as_float((labour_series.get("uk_unemployment_rate") or {}).get("value"))
    boe_rate = _as_float((policy_series.get("boe") or {}).get("value"))
    missing: list[str] = []
    if uk_cpi is None:
        missing.append("uk_cpi")
    if uk_unrate is None:
        missing.append("uk_unemployment_rate")
    if boe_rate is None:
        missing.append("boe_policy_rate")
    if uk_cpi is None and uk_unrate is None and boe_rate is None:
        return _region_placeholder("uk", "United Kingdom", "BoE")

    inflation_label = "uncertain"
    inflation_drivers: list[str] = []
    if uk_cpi is not None:
        if uk_cpi <= 2.4:
            inflation_label = "easing"
        elif uk_cpi >= 3.0:
            inflation_label = "reaccelerating"
        else:
            inflation_label = "sticky"
        inflation_drivers.append(f"UK CPI at {uk_cpi:.1f}% YoY.")
    labour_label = "uncertain"
    labour_drivers: list[str] = []
    if uk_unrate is not None:
        if uk_unrate >= 4.8:
            labour_label = "cooling"
        elif uk_unrate <= 4.0:
            labour_label = "tight"
        else:
            labour_label = "balanced"
        labour_drivers.append(f"UK unemployment at {uk_unrate:.1f}%.")
    rates_label = "uncertain"
    rates_drivers: list[str] = []
    if boe_rate is not None:
        if boe_rate >= 4.5:
            rates_label = "tightening"
        elif boe_rate <= 2.5:
            rates_label = "easing"
        else:
            rates_label = "neutral"
        rates_drivers.append(f"BoE policy proxy at {boe_rate:.2f}%.")

    drivers: list[str] = []
    if uk_cpi is not None and uk_cpi <= 2.4:
        if uk_unrate is not None:
            drivers.append("UK disinflation with softer labour supports easing bias.")
        else:
            drivers.append("UK disinflation supports easing bias, but labour confirmation is unavailable.")
        policy_label = "cut_leaning"
    elif uk_cpi is not None and uk_cpi >= 3.0 and uk_unrate is not None and uk_unrate <= 4.0 and boe_rate is not None and boe_rate >= 4.5:
        drivers.append("UK inflation pressure with tighter labour backdrop supports tighter bias.")
        policy_label = "hike_leaning"
    elif uk_cpi is not None:
        drivers.append("UK inputs are mixed; deterministic hold bias.")
        policy_label = "hold"
    else:
        policy_label = "uncertain"

    confidence = "high" if not missing else ("medium" if len(missing) == 1 else "low")
    status = "ok" if not missing else "partial"
    return {
        "region_key": "uk",
        "region_label": "United Kingdom",
        "scope": "central_bank",
        "central_bank": "BoE",
        "status": status,
        "policy_bias": {
            "label": policy_label,
            "confidence": confidence,
            "drivers": drivers[:4],
            "missing": missing[:6],
            "risks": ["Deterministic bias, not a forecast or implied probability."],
        },
        "inflation_pressure": {"label": inflation_label, "drivers": inflation_drivers[:3], "missing": ["uk_cpi"] if uk_cpi is None else []},
        "labour_pressure": {"label": labour_label, "drivers": labour_drivers[:3], "missing": ["uk_unemployment_rate"] if uk_unrate is None else []},
        "rates_pressure": {"label": rates_label, "drivers": rates_drivers[:3], "missing": ["boe_policy_rate"] if boe_rate is None else []},
        "drivers": drivers[:4],
        "missing": missing[:6],
        "risks": ["UK signal uses UK-specific inputs only; US data is not used as direct UK driver."],
        "data_basis": "UK CPI + UK unemployment + BoE policy proxy when available.",
    }


def _build_spain_region(
    *,
    inflation_series: dict[str, Any],
    labour_series: dict[str, Any],
    rates_series: dict[str, Any],
    ecb_bias: dict[str, Any],
) -> dict[str, Any]:
    spain_cpi = _as_float((inflation_series.get("spain_cpi") or {}).get("value"))
    spain_unrate = _as_float((labour_series.get("spain_unemployment_rate") or {}).get("value"))
    spain_rates = _as_float((rates_series.get("spain_10y") or {}).get("value"))

    missing: list[str] = []
    if spain_cpi is None:
        missing.append("spain_cpi")
    if spain_unrate is None:
        missing.append("spain_unemployment_rate")
    if spain_rates is None:
        missing.append("spain_rates_proxy")

    drivers: list[str] = [
        "Spain is modeled as a country macro lens under the ECB policy anchor (not a standalone central bank bias)."
    ]
    if spain_cpi is not None:
        drivers.append(f"Spain CPI at {spain_cpi:.1f}% YoY.")
    if spain_unrate is not None:
        drivers.append(f"Spain unemployment at {spain_unrate:.1f}%.")
    if spain_cpi is None and spain_unrate is None:
        drivers.append("Spain-specific inflation/labour fields are unavailable; using Eurozone policy anchor context.")

    lens_confidence = "high" if (spain_cpi is not None and spain_unrate is not None) else "low"
    status = "ok" if not missing else "partial"
    return {
        "region_key": "spain",
        "region_label": "Spain",
        "scope": "country_lens",
        "policy_anchor": "ECB",
        "status": status,
        "country_lens": {
            "label": "country_macro_lens",
            "confidence": lens_confidence,
            "drivers": drivers[:4],
            "missing": missing[:6],
            "risks": ["Country lens only; refer to ECB for policy-bias anchor."],
        },
        "inflation_pressure": {"label": "uncertain" if spain_cpi is None else ("reaccelerating" if spain_cpi >= 3.0 else "easing"), "drivers": drivers[:2], "missing": ["spain_cpi"] if spain_cpi is None else []},
        "labour_pressure": {"label": "uncertain" if spain_unrate is None else ("cooling" if spain_unrate >= 11.5 else "balanced"), "drivers": drivers[:3], "missing": ["spain_unemployment_rate"] if spain_unrate is None else []},
        "rates_pressure": {"label": "uncertain", "drivers": ["Spain rates proxy limited in Phase 1."], "missing": ["spain_rates_proxy"] if spain_rates is None else []},
        "drivers": drivers[:4],
        "missing": missing[:6],
        "risks": ["Uses ECB anchor when Spain-specific fields are partial."],
        "data_basis": "Spain CPI/unemployment when available + ECB anchor.",
    }


def _build_asia_central_bank_region(
    *,
    key: str,
    label: str,
    central_bank: str,
    inflation_series_key: str,
    labour_series_key: str,
    policy_series_key: str,
    inflation_series: dict[str, Any],
    labour_series: dict[str, Any],
    policy_series: dict[str, Any],
) -> dict[str, Any]:
    cpi = _as_float((inflation_series.get(inflation_series_key) or {}).get("value"))
    unrate = _as_float((labour_series.get(labour_series_key) or {}).get("value"))
    policy_rate = _as_float((policy_series.get(policy_series_key) or {}).get("value"))
    missing: list[str] = []
    if cpi is None:
        missing.append(f"{key}_cpi")
    if unrate is None:
        missing.append(f"{key}_unemployment_rate")
    if policy_rate is None:
        missing.append(f"{key}_policy_rate")
    if cpi is None and unrate is None and policy_rate is None:
        return _region_placeholder(key, label, central_bank)

    drivers: list[str] = []
    if cpi is not None:
        drivers.append(f"{label} CPI at {cpi:.1f}% YoY.")
    if unrate is not None:
        drivers.append(f"{label} unemployment at {unrate:.1f}%.")
    if policy_rate is not None:
        drivers.append(f"{central_bank} policy proxy at {policy_rate:.2f}%.")

    policy_label = "hold" if cpi is not None else "uncertain"
    status = "ok" if not missing else "partial"
    confidence = "high" if not missing else "low"
    return {
        "region_key": key,
        "region_label": label,
        "scope": "central_bank",
        "central_bank": central_bank,
        "status": status,
        "policy_bias": {
            "label": policy_label,
            "confidence": confidence,
            "drivers": drivers[:4] or [f"{label} inputs remain partial in Phase 1."],
            "missing": missing[:6],
            "risks": ["Deterministic regional lens; partial data can reduce confidence."],
        },
        "inflation_pressure": {"label": "uncertain" if cpi is None else ("reaccelerating" if cpi >= 3.0 else "easing"), "drivers": drivers[:2], "missing": [f"{key}_cpi"] if cpi is None else []},
        "labour_pressure": {"label": "uncertain" if unrate is None else ("cooling" if unrate >= 4.0 else "balanced"), "drivers": drivers[:3], "missing": [f"{key}_unemployment_rate"] if unrate is None else []},
        "rates_pressure": {"label": "uncertain" if policy_rate is None else "neutral", "drivers": drivers[:3], "missing": [f"{key}_policy_rate"] if policy_rate is None else []},
        "drivers": drivers[:4],
        "missing": missing[:6],
        "risks": ["No implied probabilities; not a forecast."],
        "data_basis": f"{label} CPI/labour/policy proxies where available.",
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
