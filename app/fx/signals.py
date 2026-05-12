"""FX signal builder: derives deterministic signals from an FX panel.

No forecasts, no LLM calls. All signals are derived from threshold rules
applied to observed price changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.fx.panel import FXQuote
from app.logger import get_logger

logger = get_logger("fx.signals")


@dataclass
class FXSignals:
    """Deterministic FX signals derived from a panel of FX quotes."""

    usd_pressure: str           # "stronger" | "weaker" | "neutral" | "unavailable"
    eur_pressure: str           # "stronger" | "weaker" | "neutral" | "unavailable"
    sterling_pressure: str      # "stronger" | "weaker" | "neutral" | "unavailable" | "not_applicable"
    yen_risk_signal: str        # "risk_off" | "carry_on" | "neutral" | "unavailable"
    china_fx_stress: str        # "active" | "neutral" | "unavailable"
    fx_materiality: str         # "low" | "medium" | "high"
    materiality_score: int
    drivers: list[str]
    missing: list[str]


def _find_quote(panel: list[FXQuote], *label_fragments: str) -> FXQuote | None:
    """Find the first quote whose label matches any of the given fragments."""
    for fragment in label_fragments:
        frag_lower = fragment.lower()
        for quote in panel:
            if frag_lower in quote.instrument.label.lower():
                return quote
    return None


def _find_dxy(panel: list[FXQuote]) -> FXQuote | None:
    """Find the trade-weighted USD / DXY proxy quote."""
    for quote in panel:
        if quote.instrument.is_dxy_proxy:
            return quote
    return None


def _direction(change_pct: float | None, threshold: float, *, inverse: bool = False) -> str:
    """Return 'stronger'/'weaker'/'neutral' or 'unavailable' if no data."""
    if change_pct is None:
        return "unavailable"
    if not inverse:
        if change_pct > threshold:
            return "stronger"
        if change_pct < -threshold:
            return "weaker"
    else:
        # Inverse: positive change in pair = weaker for base
        if change_pct > threshold:
            return "weaker"
        if change_pct < -threshold:
            return "stronger"
    return "neutral"


def _format_driver(label: str, change_pct: float | None) -> str | None:
    if change_pct is None:
        return None
    sign = "+" if change_pct >= 0 else ""
    return f"{label} {sign}{change_pct:.2f}%"


def build_fx_signals(
    panel: list[FXQuote],
    context: dict | None = None,
) -> FXSignals:
    """Derive deterministic FX signals from a panel of quotes.

    Parameters
    ----------
    panel:
        List of FXQuote objects from fetch_fx_panel().
    context:
        Optional dict with cross-asset context for materiality scoring.
        Recognised keys: gold_change_pct, oil_change_pct,
        us_10y_change_bps, regional_spread_pct.

    Returns
    -------
    FXSignals
        Deterministic signal bundle with materiality score.
    """
    ctx = context or {}
    missing: list[str] = []
    drivers: list[str] = []

    # Collect unavailable instruments
    for quote in panel:
        if quote.status == "unavailable":
            missing.append(quote.instrument.label)

    # --- USD pressure ---
    dxy_quote = _find_dxy(panel)
    eur_usd_quote = _find_quote(panel, "EUR/USD")

    usd_pressure: str
    if dxy_quote and dxy_quote.status == "ok" and dxy_quote.daily_change_pct is not None:
        usd_pressure = _direction(dxy_quote.daily_change_pct, 0.2)
        driver = _format_driver("DXY", dxy_quote.daily_change_pct)
        if driver:
            drivers.append(driver)
    elif eur_usd_quote and eur_usd_quote.status == "ok" and eur_usd_quote.daily_change_pct is not None:
        # EUR/USD down = USD stronger (inverse)
        usd_pressure = _direction(eur_usd_quote.daily_change_pct, 0.2, inverse=True)
    else:
        usd_pressure = "unavailable"

    # --- EUR pressure ---
    eur_pressure: str
    if eur_usd_quote and eur_usd_quote.status == "ok" and eur_usd_quote.daily_change_pct is not None:
        eur_pressure = _direction(eur_usd_quote.daily_change_pct, 0.2)
        driver = _format_driver("EUR/USD", eur_usd_quote.daily_change_pct)
        if driver and "EUR/USD" not in " ".join(drivers):
            drivers.append(driver)
    else:
        eur_pressure = "unavailable"

    # --- Sterling pressure ---
    gbp_usd_quote = _find_quote(panel, "GBP/USD")
    eur_gbp_quote = _find_quote(panel, "EUR/GBP")

    sterling_pressure: str
    if gbp_usd_quote and gbp_usd_quote.status == "ok" and gbp_usd_quote.daily_change_pct is not None:
        sterling_pressure = _direction(gbp_usd_quote.daily_change_pct, 0.2)
        driver = _format_driver("GBP/USD", gbp_usd_quote.daily_change_pct)
        if driver:
            drivers.append(driver)
    elif eur_gbp_quote and eur_gbp_quote.status == "ok" and eur_gbp_quote.daily_change_pct is not None:
        # EUR/GBP up = GBP weaker (inverse perspective)
        sterling_pressure = _direction(eur_gbp_quote.daily_change_pct, 0.2, inverse=True)
        driver = _format_driver("EUR/GBP", eur_gbp_quote.daily_change_pct)
        if driver:
            drivers.append(driver)
    elif gbp_usd_quote is None and eur_gbp_quote is None:
        sterling_pressure = "not_applicable"
    else:
        sterling_pressure = "unavailable"

    # --- Yen risk signal ---
    usd_jpy_quote = _find_quote(panel, "USD/JPY")
    yen_risk_signal: str
    if usd_jpy_quote and usd_jpy_quote.status == "ok" and usd_jpy_quote.daily_change_pct is not None:
        chg = usd_jpy_quote.daily_change_pct
        if chg > 0.5:
            yen_risk_signal = "carry_on"  # yen weakening: carry trades on
        elif chg < -0.5:
            yen_risk_signal = "risk_off"  # yen strengthening: flight to safety
        else:
            yen_risk_signal = "neutral"
        driver = _format_driver("USD/JPY", chg)
        if driver:
            drivers.append(driver)
    else:
        yen_risk_signal = "unavailable"

    # --- China FX stress ---
    usd_cnh_quote = _find_quote(panel, "USD/CNH", "USD/CNY")
    china_fx_stress: str
    if usd_cnh_quote and usd_cnh_quote.status == "ok" and usd_cnh_quote.daily_change_pct is not None:
        chg = usd_cnh_quote.daily_change_pct
        china_fx_stress = "active" if chg > 0.5 else "neutral"
        driver = _format_driver(usd_cnh_quote.instrument.label, chg)
        if driver:
            drivers.append(driver)
    else:
        china_fx_stress = "unavailable"

    # --- Materiality scoring ---
    score = 0

    def _abs_chg(quote: FXQuote | None) -> float:
        if quote and quote.status == "ok" and quote.daily_change_pct is not None:
            return abs(quote.daily_change_pct)
        return 0.0

    # DXY / trade-weighted USD
    if _abs_chg(dxy_quote) > 0.5:
        score += 2
    # EUR/USD
    if _abs_chg(eur_usd_quote) > 0.5:
        score += 2
    # EUR/GBP
    if _abs_chg(eur_gbp_quote) > 0.35:
        score += 1
    # USD/JPY
    if _abs_chg(usd_jpy_quote) > 0.7:
        score += 2
    # USD/CNH
    if _abs_chg(usd_cnh_quote) > 0.5:
        score += 2
    # Cross-asset context
    gold_move = abs(ctx.get("gold_change_pct") or 0.0)
    oil_move = abs(ctx.get("oil_change_pct") or 0.0)
    ten_y_bps = abs(ctx.get("us_10y_change_bps") or 0.0)
    regional_spread = abs(ctx.get("regional_spread_pct") or 0.0)

    if gold_move > 1.0:
        score += 1
    if oil_move > 2.0:
        score += 1
    if ten_y_bps > 5.0:
        score += 1
    if regional_spread > 1.0:
        score += 1

    if score <= 1:
        fx_materiality = "low"
    elif score <= 3:
        fx_materiality = "medium"
    else:
        fx_materiality = "high"

    return FXSignals(
        usd_pressure=usd_pressure,
        eur_pressure=eur_pressure,
        sterling_pressure=sterling_pressure,
        yen_risk_signal=yen_risk_signal,
        china_fx_stress=china_fx_stress,
        fx_materiality=fx_materiality,
        materiality_score=score,
        drivers=drivers,
        missing=missing,
    )
