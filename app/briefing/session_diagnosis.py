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
    spx, nasdaq, dow, russell = _major_us_index_moves(briefing)

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
        us=us,
        europe=europe,
        nasdaq=nasdaq,
        russell=russell,
        spx=spx,
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
        watch.append("Rates: UNAVAILABLE. US 10Y quote missing; rates-pressure confirmation incomplete.")
    elif ten_y >= 4.45:
        zone = "4.70%" if ten_y < 4.70 else "4.80%"
        active.append(
            f"Rates: ACTIVE. US 10Y is {ten_y:.2f}%, already above 4.45%; valuation-sensitive growth and duration remain under pressure. Next stress zone: {zone}."
        )
    else:
        watch.append(f"Rates: WATCH. US 10Y is {ten_y:.2f}%; a move above 4.45% would re-activate rates pressure.")

    if wti_pct is None:
        watch.append("Oil: UNAVAILABLE. WTI quote missing; energy impulse confirmation pending.")
    elif wti_pct >= 3.0:
        active.append(f"Oil: ACTIVE SHOCK. WTI is {wti_pct:+.2f}% and confirms escalating energy/inflation pressure.")
    elif wti_pct >= 1.0:
        active.append(f"Oil: ELEVATED. WTI is {wti_pct:+.2f}%, keeping energy/inflation risk active but below escalation shock.")
    elif wti_pct <= -1.0:
        cooled.append(f"Oil: COOLED. WTI impulse eased ({wti_pct:+.2f}%), reducing immediate energy-shock pressure.")
    else:
        watch.append(f"Oil: WATCH. WTI is {wti_pct:+.2f}%; a move above +1.00% would re-elevate energy stress.")

    if vix_level is None:
        watch.append("Volatility: UNCONFIRMED. VIX unavailable, so market-stress confirmation is incomplete.")
    elif vix_level >= 20:
        active.append(f"Volatility: ACTIVE. VIX is {vix_level:.2f}, confirming broader risk stress.")
    else:
        cooled.append(f"Volatility: CONTAINED. VIX is {vix_level:.2f}; this is not a panic tape.")

    if regional_avg is not None and regional_avg < -0.5:
        active.append(f"Regional split: ACTIVE. Regional average is {regional_avg:+.2f}%, keeping downside breadth risk in focus.")

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
    us: float | None,
    europe: float | None,
    nasdaq: float | None,
    russell: float | None,
    spx: float | None,
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
        if (
            us is not None
            and europe is not None
            and us < 0.0
            and europe > 0.0
            and (
                (nasdaq is not None and spx is not None and nasdaq < spx)
                or (russell is not None and spx is not None and russell < spx)
            )
        ):
            if session_key == "europe_midday":
                return (
                    f"{prefix}: Europe is carrying the tape while US growth/small caps lag, but this is not clean risk-on because "
                    "rates remain above pressure thresholds and leadership quality is uneven."
                )
            if session_key == "us_pre_open":
                return (
                    f"{prefix}: Europe remains firmer but US setup is rates-sensitive; with 10Y above pressure levels, "
                    "US open risk still skews toward a valuation-led fade."
                )
            if session_key == "us_intraday_risk":
                return (
                    f"{prefix}: US reaction is confirming weaker quality under high rates; Europe still leads, "
                    "so this remains regional split rather than synchronized risk-on."
                )
            return (
                f"{prefix}: rates are the main macro pressure point, but the equity reaction is regionally split "
                f"rather than broad risk-off; Europe is firmer while US growth/small caps lag."
            )
        return f"{prefix}: rates are the main headwind with cross-asset pressure still active."
    if primary_driver == "geo_energy_pressure":
        return f"{prefix}: geo/energy pressure is leading; monitor transmission into rates and equities."
    if primary_driver == "risk_on_confirmation":
        return f"{prefix}: risk-on confirmation is broad across regions and breadth."
    if primary_driver == "risk_off_confirmation":
        return f"{prefix}: risk-off confirmation is broad across regions and volatility."

    # Build a plain-language mixed-signal summary without internal score values.
    parts: list[str] = []
    if regional_avg is not None:
        parts.append(f"regional average {regional_avg:+.2f}%")
    if rates_pressure_score >= 1.0:
        parts.append("rates above pressure threshold")
    elif rates_pressure_score >= 0.5:
        parts.append("rates near pressure threshold")
    if wti_pct is not None and abs(wti_pct) >= 1.0:
        parts.append(f"oil {wti_pct:+.2f}%")
    if geo_level in {"ELEVATED", "HIGH", "EXTREME"} and not vix_available:
        parts.append(f"geo risk {geo_level.lower()} but market confirmation incomplete (VIX unavailable)")
    elif geo_level in {"ELEVATED", "HIGH", "EXTREME"}:
        parts.append(f"geo risk {geo_level.lower()}")
    if not vix_available:
        parts.append("VIX unavailable")
    body = "; ".join(parts) if parts else "cross-asset picture mixed"
    return f"{prefix}: mixed signals: {body}."


def _major_us_index_moves(briefing: MorningBriefing) -> tuple[float | None, float | None, float | None, float | None]:
    spx = nasdaq = dow = russell = None
    for quote in (briefing.market_setup.index_quotes or []):
        name = f"{quote.display_name} {quote.symbol}".lower()
        change = float(quote.change_percent or 0.0)
        if ("s&p" in name or "spx" in name) and spx is None:
            spx = change
        elif ("nasdaq" in name or "ixic" in name) and nasdaq is None:
            nasdaq = change
        elif ("dow" in name or "dji" in name) and dow is None:
            dow = change
        elif ("russell" in name or "rut" in name) and russell is None:
            russell = change
    return spx, nasdaq, dow, russell


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
