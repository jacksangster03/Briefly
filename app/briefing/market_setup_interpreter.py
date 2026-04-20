"""Deterministic interpretation layer for market setup snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.schemas.briefings import MarketSetup
from app.schemas.events import MacroDataPoint


@dataclass
class MarketSetupInterpretation:
    """Human-readable interpretation plus lightweight internal signal tags."""

    narrative: str
    tags: list[str]
    confidence: str


def interpret_market_setup(
    setup: MarketSetup,
    macro_context: Iterable[MacroDataPoint] | None = None,
) -> MarketSetupInterpretation:
    """Summarize setup + macro into one deterministic paragraph."""
    index_quotes = list(setup.index_quotes or [])
    macro_quotes = list(setup.macro_quotes or [])
    macro_points = list(macro_context or [])

    risk_score = _risk_appetite_score(index_quotes)
    rates_score = _rates_impulse_score(setup=setup, macro_points=macro_points)
    commodity_score = _commodity_impulse_score(macro_quotes)
    region_score = _cross_region_confirmation(index_quotes)

    total = risk_score + rates_score + commodity_score + region_score
    confidence = _confidence_label(total=total, signals=[risk_score, rates_score, commodity_score, region_score])

    tone = _tone_phrase(total)
    breadth = _breadth_phrase(index_quotes=index_quotes, region_score=region_score)
    rates = _rates_phrase(setup=setup, macro_points=macro_points)
    commodities = _commodities_phrase(macro_quotes)

    narrative = f"{tone} {breadth} {rates} {commodities}"
    tags = _tags(risk_score=risk_score, rates_score=rates_score, commodity_score=commodity_score, region_score=region_score)
    return MarketSetupInterpretation(narrative=narrative, tags=tags, confidence=confidence)


def _risk_appetite_score(index_quotes) -> int:
    if not index_quotes:
        return 0
    equities = [q for q in index_quotes if "VIX" not in (q.display_name or q.symbol or "").upper()]
    winners = sum(1 for q in equities if float(q.change_percent or 0.0) > 0.0)
    losers = sum(1 for q in equities if float(q.change_percent or 0.0) < 0.0)
    vix = next((q for q in index_quotes if "VIX" in (q.display_name or q.symbol or "").upper()), None)
    score = 0
    if winners >= max(4, losers + 2):
        score += 2
    elif winners > losers:
        score += 1
    elif losers > winners:
        score -= 1
    if vix is not None:
        vix_move = float(vix.change_percent or 0.0)
        if vix_move <= -1.0:
            score += 1
        elif vix_move >= 1.0:
            score -= 1
    return score


def _rates_impulse_score(*, setup: MarketSetup, macro_points: list[MacroDataPoint]) -> int:
    ten_y_change = _macro_change(macro_points, ("10y treasury", "us 10y", "10y treasury yield"))
    spread_change = _macro_change(macro_points, ("10y-2y yield spread", "yield spread"))
    if ten_y_change is None and setup.treasury_10y:
        ten_y_change = float(setup.treasury_10y.change or 0.0)
    score = 0
    if ten_y_change is not None:
        if ten_y_change <= -0.03:
            score += 1
        elif ten_y_change >= 0.03:
            score -= 1
    if spread_change is not None:
        if spread_change >= 0.01:
            score += 1
        elif spread_change <= -0.01:
            score -= 1
    return score


def _commodity_impulse_score(macro_quotes) -> int:
    if not macro_quotes:
        return 0
    oil = _find_quote(macro_quotes, ("WTI", "CRUDE", "CL1:COM", "CL=F"))
    gold = _find_quote(macro_quotes, ("GOLD", "GC1:COM", "GC=F"))
    score = 0
    if oil is not None:
        oil_move = float(oil.change_percent or 0.0)
        if oil_move <= -2.0:
            score += 1
        elif oil_move >= 2.0:
            score -= 1
    if gold is not None:
        gold_move = float(gold.change_percent or 0.0)
        if gold_move >= 1.0:
            score -= 1
        elif gold_move <= -1.0:
            score += 1
    return score


def _cross_region_confirmation(index_quotes) -> int:
    if not index_quotes:
        return 0
    regions = {"us": [], "europe": [], "asia": []}
    for quote in index_quotes:
        name = (quote.display_name or quote.symbol or "").upper()
        move = float(quote.change_percent or 0.0)
        if any(token in name for token in ("S&P", "NASDAQ", "DOW", "RUSSELL")):
            regions["us"].append(move)
        elif any(token in name for token in ("STOXX", "FTSE", "DAX", "CAC", "IBEX")):
            regions["europe"].append(move)
        elif any(token in name for token in ("NIKKEI", "HANG SENG")):
            regions["asia"].append(move)
    positive_regions = sum(1 for values in regions.values() if values and _mean(values) > 0.0)
    negative_regions = sum(1 for values in regions.values() if values and _mean(values) < 0.0)
    if positive_regions >= 2 and negative_regions == 0:
        return 1
    if negative_regions >= 2 and positive_regions == 0:
        return -1
    return 0


def _tone_phrase(total: int) -> str:
    if total >= 3:
        return "Market tone is broadly risk-on."
    if total <= -3:
        return "Market tone is defensive and risk-off."
    return "Market tone is mixed with no single dominant impulse."


def _breadth_phrase(*, index_quotes, region_score: int) -> str:
    if not index_quotes:
        return "Breadth signals are limited due to sparse index coverage."
    positive = sum(1 for q in index_quotes if float(q.change_percent or 0.0) > 0.0)
    total = len(index_quotes)
    if region_score > 0:
        return f"Equity breadth is constructive ({positive}/{total} tracked benchmarks up) with cross-region confirmation."
    if region_score < 0:
        return f"Equity breadth is weak ({positive}/{total} tracked benchmarks up) with cross-region pressure."
    return f"Equity breadth is balanced ({positive}/{total} tracked benchmarks up) without strong regional confirmation."


def _rates_phrase(*, setup: MarketSetup, macro_points: list[MacroDataPoint]) -> str:
    ten_y_change = _macro_change(macro_points, ("10y treasury", "us 10y", "10y treasury yield"))
    spread_change = _macro_change(macro_points, ("10y-2y yield spread", "yield spread"))
    if ten_y_change is None and setup.treasury_10y:
        ten_y_change = float(setup.treasury_10y.change or 0.0)
    if ten_y_change is None and spread_change is None:
        return "Rates context is neutral from available data."
    bits: list[str] = []
    if ten_y_change is not None:
        direction = "lower" if ten_y_change < 0 else "higher" if ten_y_change > 0 else "flat"
        bits.append(f"US 10Y is {direction} ({ten_y_change:+.3f})")
    if spread_change is not None:
        curve = "steepening" if spread_change > 0 else "flattening" if spread_change < 0 else "flat"
        bits.append(f"curve is {curve} ({spread_change:+.3f})")
    return "Rates impulse: " + ", ".join(bits) + "."


def _commodities_phrase(macro_quotes) -> str:
    oil = _find_quote(macro_quotes, ("WTI", "CRUDE", "CL1:COM", "CL=F"))
    gold = _find_quote(macro_quotes, ("GOLD", "GC1:COM", "GC=F"))
    if oil is None and gold is None:
        return "Commodity signal is limited."
    parts: list[str] = []
    if oil is not None:
        parts.append(f"WTI {float(oil.change_percent or 0.0):+.2f}%")
    if gold is not None:
        parts.append(f"gold {float(gold.change_percent or 0.0):+.2f}%")
    return "Commodity impulse: " + ", ".join(parts) + "."


def _confidence_label(*, total: int, signals: list[int]) -> str:
    dispersion = max(signals) - min(signals) if signals else 0
    if abs(total) >= 3 and dispersion <= 2:
        return "high"
    if abs(total) >= 2:
        return "medium"
    return "low"


def _tags(*, risk_score: int, rates_score: int, commodity_score: int, region_score: int) -> list[str]:
    tags: list[str] = []
    if risk_score >= 2:
        tags.append("risk_on")
    elif risk_score <= -1:
        tags.append("risk_off")
    if rates_score > 0:
        tags.append("rates_supportive")
    elif rates_score < 0:
        tags.append("rates_headwind")
    if commodity_score > 0:
        tags.append("commodity_easing")
    elif commodity_score < 0:
        tags.append("commodity_pressure")
    if region_score > 0:
        tags.append("cross_region_confirmed")
    elif region_score < 0:
        tags.append("cross_region_divergence")
    return tags


def _find_quote(quotes, tokens: tuple[str, ...]):
    for quote in quotes:
        text = (quote.display_name or quote.symbol or "").upper()
        if any(token in text for token in tokens):
            return quote
    return None


def _macro_change(points: list[MacroDataPoint], labels: tuple[str, ...]) -> float | None:
    for point in points:
        name = (point.name or "").strip().lower()
        if any(label in name for label in labels):
            return None if point.change is None else float(point.change)
    return None


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))
