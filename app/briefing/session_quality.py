"""Deterministic session quality score: numeric Q in [-1, +1] mapped to 5 regime buckets.

Algorithm:
  breadth    (0.30) - sector ETF up/down ratio
  index dir  (0.20) - weighted US/EU/Asia move, normalised at ±1.5%
  VIX        (0.20) - level + change penalty, always >= 0
  rates      (0.15) - 10Y move and curve support, positive = supportive
  commodities(0.10) - oil/gold spike stress penalty, always >= 0
  geo risk   (0.05) - LOW→0 … EXTREME→0.8 penalty

Buckets:
  Q <= -0.60  SEVERE_STRESS  #5C1111
  -0.60…-0.25 CAUTIOUS       #9B2C2C
  -0.25…0.25  MIXED          #2F3744
  0.25…0.60   CONSTRUCTIVE   #0F7A4A
  >= 0.60     STRONG_RISK_ON #16A34A
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.events import MacroDataPoint, MarketBreadth, QuoteData


@dataclass
class SessionQuality:
    score: float
    bucket: str
    color_hex: str
    label: str


_BUCKETS: list[tuple[float, float, str, str]] = [
    (-1.01, -0.60, "SEVERE_STRESS",  "#5C1111"),
    (-0.60, -0.25, "CAUTIOUS",       "#9B2C2C"),
    (-0.25,  0.25, "MIXED",          "#2F3744"),
    ( 0.25,  0.60, "CONSTRUCTIVE",   "#0F7A4A"),
    ( 0.60,  1.01, "STRONG_RISK_ON", "#16A34A"),
]

_GEO_PENALTY: dict[str, float] = {
    "low":      0.0,
    "moderate": 0.2,
    "elevated": 0.4,
    "high":     0.6,
    "extreme":  0.8,
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _region_avg(quotes: list[QuoteData], tokens: tuple[str, ...]) -> float | None:
    moves = [
        float(q.change_percent or 0.0)
        for q in quotes
        if any(t in (q.display_name or q.symbol or "").upper() for t in tokens)
        and q.change_percent is not None
    ]
    return sum(moves) / len(moves) if moves else None


def _find_macro(points: list[MacroDataPoint], labels: tuple[str, ...]) -> MacroDataPoint | None:
    for p in points:
        name = (p.name or "").strip().lower()
        if any(label in name for label in labels):
            return p
    return None


def _find_strip(strip: list[MacroDataPoint], tokens: tuple[str, ...]) -> MacroDataPoint | None:
    for p in strip:
        key = ((p.name or "") + " " + (p.series_id or "")).upper()
        if any(t in key for t in tokens):
            return p
    return None


def _derive_label(
    bucket: str,
    *,
    breadth_score: float,
    vix_score: float,
    rates_combined: float,
    commodity_score: float,
    geo_score: float,
) -> str:
    _BASE = {
        "SEVERE_STRESS":  "Severe stress",
        "CAUTIOUS":       "Cautious",
        "MIXED":          "Mixed tape",
        "CONSTRUCTIVE":   "Constructive",
        "STRONG_RISK_ON": "Strong risk-on",
    }
    base = _BASE[bucket]
    qualifiers: list[str] = []
    if commodity_score > 0.4:
        qualifiers.append("energy-driven macro stress")
    elif commodity_score > 0.2:
        qualifiers.append("commodity pressure")
    if vix_score > 0.5:
        qualifiers.append("elevated volatility")
    if rates_combined < -0.35:
        qualifiers.append("rates headwind")
    elif rates_combined > 0.35 and bucket in ("CONSTRUCTIVE", "STRONG_RISK_ON"):
        qualifiers.append("supportive rates")
    if geo_score >= 0.4:
        qualifiers.append("elevated geo risk")
    if breadth_score < -0.3 and bucket in ("MIXED", "CONSTRUCTIVE"):
        qualifiers.append("breadth divergence")
    if not qualifiers:
        return base
    return f"{base} with {', '.join(qualifiers)}"


def compute_session_quality(
    *,
    market_breadth: list[MarketBreadth],
    index_quotes: list[QuoteData],
    macro_context: list[MacroDataPoint],
    commodity_strip: list[MacroDataPoint],
    geo_risk_level: str = "",
) -> SessionQuality:
    """Compute a deterministic session quality score Q in [-1, +1].

    All inputs should already be populated on the MorningBriefing before
    this is called. Returns a SessionQuality with score, bucket, hex color,
    and a short contextual label derived from the dominant driver.
    """
    # --- 1. Breadth: (up - down) / total → [-1, +1] --------------------------
    if market_breadth:
        up = sum(1 for b in market_breadth if float(b.change_percent or 0.0) > 0)
        down = sum(1 for b in market_breadth if float(b.change_percent or 0.0) < 0)
        breadth_score = float(up - down) / len(market_breadth)
    else:
        equity_qs = [
            q for q in index_quotes
            if "VIX" not in (q.display_name or q.symbol or "").upper()
        ]
        if equity_qs:
            up = sum(1 for q in equity_qs if float(q.change_percent or 0.0) > 0)
            down = sum(1 for q in equity_qs if float(q.change_percent or 0.0) < 0)
            breadth_score = float(up - down) / len(equity_qs)
        else:
            breadth_score = 0.0

    # --- 2. Index direction: weighted US/EU/Asia avg, scaled at ±1.5% --------
    us_avg  = _region_avg(index_quotes, ("S&P", "NASDAQ", "DOW", "RUSSELL", "SPY", "QQQ", "DIA", "IWM", "SPX", "COMP"))
    eu_avg  = _region_avg(index_quotes, ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
    asia_avg = _region_avg(index_quotes, ("NIKKEI", "HANG SENG", "N225", "HSI"))
    w_sum, w_total = 0.0, 0.0
    for avg, w in [(us_avg, 0.50), (eu_avg, 0.30), (asia_avg, 0.20)]:
        if avg is not None:
            w_sum += avg * w
            w_total += w
    raw_index = (w_sum / w_total) if w_total > 0 else 0.0
    index_score = _clamp(raw_index / 1.5, -1.0, 1.0)

    # --- 3. VIX: level + change penalty; always >= 0 -------------------------
    vix_q = next(
        (q for q in index_quotes if "VIX" in (q.display_name or q.symbol or "").upper()),
        None,
    )
    if vix_q is not None:
        vix_level_score  = _clamp((float(vix_q.current_price or 17.0) - 15.0) / 10.0, -1.0, 1.0)
        vix_change_score = _clamp(float(vix_q.change_percent or 0.0) / 15.0, -1.0, 1.0)
        vix_score = max(vix_level_score, vix_change_score, 0.0)
    else:
        vix_score = 0.0

    # --- 4. Rates: positive = supportive (10Y falling, curve steepening) -----
    ten_y_pt = _find_macro(macro_context, ("us 10y treasury", "10y treasury yield", "us 10y yield"))
    curve_pt = _find_macro(macro_context, ("10y-2y yield spread", "t10y2y", "yield spread"))
    ten_y_change = float(ten_y_pt.change or 0.0) if ten_y_pt is not None else None
    curve_change  = float(curve_pt.change or 0.0)  if curve_pt is not None  else None
    rates_support = _clamp(-(ten_y_change) / 0.20, -1.0, 1.0) if ten_y_change is not None else 0.0
    curve_support = _clamp(curve_change / 0.20, -1.0, 1.0)    if curve_change  is not None else 0.0
    rates_combined = 0.65 * rates_support + 0.35 * curve_support

    # --- 5. Commodity stress: oil/gold spike penalty; always >= 0 -------------
    wti_pt  = _find_strip(commodity_strip, ("WTI", "DCOILWTICO", "CRUDE"))
    gold_pt = _find_strip(commodity_strip, ("GOLD", "GC=F"))
    oil_pct  = float(wti_pt.change_percent  or 0.0) if wti_pt  is not None else 0.0
    gold_pct = float(gold_pt.change_percent or 0.0) if gold_pt is not None else 0.0
    oil_stress  = _clamp((oil_pct  - 0.5) / 2.5, -1.0, 1.0)
    gold_stress = _clamp((gold_pct - 0.5) / 3.0, -1.0, 1.0)
    commodity_score = max(oil_stress, gold_stress, 0.0)

    # --- 6. Geo risk penalty --------------------------------------------------
    geo_score = _GEO_PENALTY.get((geo_risk_level or "").strip().lower(), 0.0)

    # --- 7. Combine -----------------------------------------------------------
    Q = _clamp(
        0.30 * breadth_score
        + 0.20 * index_score
        - 0.20 * vix_score
        + 0.15 * rates_combined
        - 0.10 * commodity_score
        - 0.05 * geo_score,
        -1.0,
        1.0,
    )

    # --- 8. Bucket lookup -----------------------------------------------------
    bucket_name = "MIXED"
    color_hex   = "#2F3744"
    for lo, hi, bname, bhex in _BUCKETS:
        if lo <= Q < hi:
            bucket_name = bname
            color_hex   = bhex
            break

    label = _derive_label(
        bucket_name,
        breadth_score=breadth_score,
        vix_score=vix_score,
        rates_combined=rates_combined,
        commodity_score=commodity_score,
        geo_score=geo_score,
    )

    return SessionQuality(score=Q, bucket=bucket_name, color_hex=color_hex, label=label)
