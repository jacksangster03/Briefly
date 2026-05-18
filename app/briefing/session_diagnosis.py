"""Deterministic session diagnosis engine and trigger-board builder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.briefings import MorningBriefing


@dataclass
class SessionDiagnosis:
    primary_driver: str
    secondary_drivers: list[str]
    rejected_drivers: list[str]
    regime_label: str
    confidence: str
    one_sentence_diagnosis: str
    regional_diagnosis: str
    portfolio_diagnosis: str
    data_caveats: list[str]
    scores: dict[str, float]
    trigger_board: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_driver": self.primary_driver,
            "secondary_drivers": self.secondary_drivers,
            "rejected_drivers": self.rejected_drivers,
            "regime_label": self.regime_label,
            "confidence": self.confidence,
            "one_sentence_diagnosis": self.one_sentence_diagnosis,
            "regional_diagnosis": self.regional_diagnosis,
            "portfolio_diagnosis": self.portfolio_diagnosis,
            "data_caveats": self.data_caveats,
            "scores": self.scores,
        }


def build_session_diagnosis(briefing: MorningBriefing) -> SessionDiagnosis:
    session_key = (briefing.session_key or "morning").lower()
    canonical = dict(briefing.canonical_prices or {})
    quote_freshness = dict(briefing.quote_freshness or {})

    def _canon_val(key: str, field: str) -> float | None:
        row = dict(canonical.get(key) or {})
        val = row.get(field)
        if val is None:
            return None
        try:
            return float(val)
        except Exception:
            return None

    us = _canon_val("US", "change_percent")
    europe = _canon_val("EUROPE", "change_percent")
    asia = _canon_val("ASIA", "change_percent")
    wti_pct = _canon_val("WTI", "change_percent")
    ten_y = _canon_val("US10Y", "value")
    ten_y_chg = _canon_val("US10Y", "change")
    vix_level = _canon_val("VIX", "value")
    vix_delta = _canon_val("VIX", "change_percent")

    breadth_rows = list(briefing.market_setup.market_breadth or [])
    breadth_score = 0.0
    if breadth_rows:
        up = sum(1 for row in breadth_rows if float(row.change_percent or 0.0) > 0.0)
        breadth_score = (up / len(breadth_rows)) * 2.0 - 1.0

    regions = [v for v in (us, europe, asia) if v is not None]
    regional_avg = (sum(regions) / len(regions)) if regions else None
    divergence = 0.0
    if us is not None and europe is not None:
        divergence = abs(us - europe)

    rates_pressure_score = 0.0
    if ten_y is not None:
        if ten_y >= 4.45:
            rates_pressure_score += 1.0
        elif ten_y >= 4.30:
            rates_pressure_score += 0.5
    if ten_y_chg is not None:
        if ten_y_chg >= 0.03:
            rates_pressure_score += 0.7
        elif ten_y_chg <= -0.03:
            rates_pressure_score -= 0.6

    energy_geo_score = 0.0
    if wti_pct is not None:
        if wti_pct >= 1.5:
            energy_geo_score += 1.0
        elif wti_pct <= -1.0:
            energy_geo_score -= 0.6
    geo_level = (briefing.geo_risk_level or "").upper()
    if geo_level in {"ELEVATED", "HIGH", "EXTREME"}:
        energy_geo_score += 0.6

    tech_ai_concentration_score = 0.0
    tech_move_count = 0
    for q in (briefing.watchlist_quotes or []):
        sym = (q.symbol or "").upper()
        if sym in {"NVDA", "MSFT", "AAPL", "AMD", "AVGO", "META", "GOOGL", "AMZN"} and abs(float(q.change_percent or 0.0)) >= 2.0:
            tech_move_count += 1
    if tech_move_count >= 2:
        tech_ai_concentration_score = 0.8

    portfolio_transmission_score = 0.0
    pnl_chart = next(
        (c for c in (briefing.morning_chart_bundle.get("charts", []) if briefing.morning_chart_bundle else []) if c.get("chart_key") == "pnl_attribution_waterfall"),
        {},
    )
    total_contrib = ((pnl_chart.get("meta") or {}).get("total_contribution"))
    if total_contrib is not None:
        try:
            portfolio_transmission_score = float(total_contrib)
        except Exception:
            portfolio_transmission_score = 0.0

    caveats: list[str] = []
    if briefing.market_data_outage:
        caveats.append("Market data unavailable; no directional read generated.")
    if briefing.news_data_outage:
        caveats.append("News scan returned no usable items; provider diagnostics required.")

    vix_meta = quote_freshness.get("VIX") or quote_freshness.get("^VIX") or {}
    vix_state = str(vix_meta.get("freshness_state") or "")
    vix_available = vix_level is not None and vix_level > 0 and vix_state != "unavailable"
    if not vix_available:
        caveats.append("VIX unavailable.")

    confidence = "high"
    if briefing.market_data_outage or not regions:
        confidence = "low"
    elif len(caveats) >= 2:
        confidence = "low"
    elif len(caveats) == 1:
        confidence = "medium"

    primary_driver = "balanced_cross_asset"
    secondary: list[str] = []
    rejected: list[str] = []

    if briefing.market_data_outage:
        primary_driver = "data_degraded"
    elif rates_pressure_score >= 1.2:
        primary_driver = "rates_headwind"
    elif energy_geo_score >= 1.2:
        primary_driver = "geo_energy_pressure"
    elif breadth_score >= 0.35 and regional_avg is not None and regional_avg > 0.2:
        primary_driver = "risk_on_confirmation"
    elif breadth_score <= -0.35 and regional_avg is not None and regional_avg < -0.2:
        primary_driver = "risk_off_confirmation"
    else:
        primary_driver = "mixed_confirmation"

    if tech_ai_concentration_score > 0:
        secondary.append("tech_ai_concentration")
    if divergence >= 0.8:
        secondary.append("regional_divergence")
    if energy_geo_score < 0 and wti_pct is not None:
        secondary.append("oil_cooling")

    if geo_level in {"ELEVATED", "HIGH", "EXTREME"} and not vix_available:
        rejected.append("geo_market_confirmation_missing_vix")

    regime_label = "DATA DEGRADED" if briefing.market_data_outage else (briefing.session_quality_label or "Mixed tape")
    if regime_label.lower().startswith("mixed") and briefing.market_data_outage:
        regime_label = "DATA DEGRADED"

    one_sentence = _session_sentence(
        session_key=session_key,
        primary_driver=primary_driver,
        regional_avg=regional_avg,
        rates_pressure_score=rates_pressure_score,
        wti_pct=wti_pct,
        geo_level=geo_level,
        vix_available=vix_available,
    )

    regional_diag = _regional_sentence(us=us, europe=europe, asia=asia, market_data_outage=briefing.market_data_outage)
    portfolio_diag = _portfolio_sentence(portfolio_transmission_score=portfolio_transmission_score, market_data_outage=briefing.market_data_outage)

    trigger_board = build_trigger_board(
        ten_y=ten_y,
        ten_y_chg=ten_y_chg,
        wti_pct=wti_pct,
        vix_level=vix_level if vix_available else None,
        regional_avg=regional_avg,
        session_key=session_key,
    )

    scores = {
        "rates_pressure_score": round(rates_pressure_score, 3),
        "energy_geo_score": round(energy_geo_score, 3),
        "equity_breadth_score": round(float(breadth_score), 3),
        "regional_divergence_score": round(float(divergence), 3),
        "tech_ai_concentration_score": round(tech_ai_concentration_score, 3),
        "portfolio_transmission_score": round(float(portfolio_transmission_score), 3),
        "data_quality_score": 0.0 if briefing.market_data_outage else 1.0,
    }

    return SessionDiagnosis(
        primary_driver=primary_driver,
        secondary_drivers=secondary,
        rejected_drivers=rejected,
        regime_label=regime_label,
        confidence=confidence,
        one_sentence_diagnosis=one_sentence,
        regional_diagnosis=regional_diag,
        portfolio_diagnosis=portfolio_diag,
        data_caveats=caveats,
        scores=scores,
        trigger_board=trigger_board,
    )


def build_trigger_board(
    *,
    ten_y: float | None,
    ten_y_chg: float | None,
    wti_pct: float | None,
    vix_level: float | None,
    regional_avg: float | None,
    session_key: str,
) -> dict[str, list[str]]:
    active: list[str] = []
    watch: list[str] = []
    cooled: list[str] = []

    if ten_y is None:
        watch.append("US 10Y unavailable; monitor rates confirmation once live quotes return.")
    elif ten_y >= 4.45:
        active.append(f"US 10Y is above 4.45% ({ten_y:.2f}%), keeping pressure on duration/growth multiples.")
    else:
        watch.append(f"US 10Y above 4.45% would reinforce rates pressure (now {ten_y:.2f}%).")

    if wti_pct is None:
        watch.append("WTI unavailable; energy impulse confirmation pending.")
    elif wti_pct >= 1.5:
        active.append(f"WTI is elevated at {wti_pct:+.2f}%, reinforcing energy/inflation stress.")
    elif wti_pct <= -1.0:
        cooled.append(f"Oil impulse cooled ({wti_pct:+.2f}%), reducing immediate energy-shock pressure.")
    else:
        watch.append(f"WTI above +1.50% would re-escalate energy stress (now {wti_pct:+.2f}%).")

    if vix_level is None:
        watch.append("VIX unavailable; volatility confirmation incomplete.")
    elif vix_level >= 20:
        active.append(f"VIX at {vix_level:.2f} confirms broader risk-off pressure.")
    else:
        cooled.append(f"VIX remains contained at {vix_level:.2f}; broad stress confirmation is limited.")

    if regional_avg is not None and regional_avg < -0.5:
        active.append(f"Regional average move {regional_avg:+.2f}% keeps downside breadth in focus.")

    return {"active": active[:4], "watch": watch[:4], "cooled": cooled[:3]}


def _session_sentence(
    *,
    session_key: str,
    primary_driver: str,
    regional_avg: float | None,
    rates_pressure_score: float,
    wti_pct: float | None,
    geo_level: str,
    vix_available: bool,
) -> str:
    prefix = {
        "morning": "Overnight setup",
        "europe_midday": "Europe session so far",
        "us_pre_open": "US pre-open handoff",
        "us_intraday_risk": "US open reaction",
        "into_close": "Into-close quality check",
        "closing_wrap": "Day verdict",
    }.get(session_key, "Session diagnosis")

    if primary_driver == "data_degraded":
        return f"{prefix}: provider data degraded; no directional read generated."
    if primary_driver == "rates_headwind":
        return f"{prefix}: rates are the main headwind with cross-asset pressure still active."
    if primary_driver == "geo_energy_pressure":
        return f"{prefix}: geo/energy pressure is leading; monitor transmission into rates and equities."
    if primary_driver == "risk_on_confirmation":
        return f"{prefix}: risk-on confirmation is broad across regions and breadth."
    if primary_driver == "risk_off_confirmation":
        return f"{prefix}: risk-off confirmation is broad across regions and volatility."

    reg = "unavailable" if regional_avg is None else f"{regional_avg:+.2f}%"
    oil_desc = "unavailable" if wti_pct is None else f"{wti_pct:+.2f}%"
    vix_desc = "unavailable" if not vix_available else "available"
    return (
        f"{prefix}: mixed cross-asset signals (regional {reg}, rates score {rates_pressure_score:+.2f}, "
        f"oil {oil_desc}, geo {geo_level or 'n/a'}, VIX {vix_desc})."
    )


def _regional_sentence(*, us: float | None, europe: float | None, asia: float | None, market_data_outage: bool) -> str:
    if market_data_outage:
        return "Regional read unavailable due to provider outage."
    bits: list[str] = []
    if us is not None:
        bits.append(f"US {us:+.2f}%")
    if europe is not None:
        bits.append(f"Europe {europe:+.2f}%")
    if asia is not None:
        bits.append(f"Asia {asia:+.2f}%")
    if not bits:
        return "Regional read unavailable."
    return "Regional read: " + ", ".join(bits) + "."


def _portfolio_sentence(*, portfolio_transmission_score: float, market_data_outage: bool) -> str:
    if market_data_outage:
        return "Portfolio transmission unavailable because quote set is incomplete."
    if portfolio_transmission_score > 0.0:
        return f"Portfolio transmission positive ({portfolio_transmission_score:+.2f}%)."
    if portfolio_transmission_score < 0.0:
        return f"Portfolio transmission negative ({portfolio_transmission_score:+.2f}%)."
    return "Portfolio transmission flat/unclear."
