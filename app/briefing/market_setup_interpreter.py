"""Deterministic interpretation layer for market setup snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.schemas.briefings import MarketSetup
from app.schemas.events import MacroDataPoint, NormalisedEvent


@dataclass
class MarketSetupInterpretation:
    """Human-readable interpretation plus lightweight internal signal tags."""

    narrative: str
    dominant_driver: str
    tags: list[str]
    confidence: str


def interpret_market_setup(
    setup: MarketSetup,
    macro_context: Iterable[MacroDataPoint] | None = None,
    global_news: Iterable[NormalisedEvent] | None = None,
) -> MarketSetupInterpretation:
    """Summarize setup + macro into one deterministic paragraph."""
    index_quotes = list(setup.index_quotes or [])
    macro_quotes = list(setup.macro_quotes or [])
    macro_points = list(macro_context or [])

    if not index_quotes and not macro_quotes and not macro_points:
        return MarketSetupInterpretation(
            narrative="Market data unavailable; no directional read generated.",
            dominant_driver="Provider data outage; market tape unavailable.",
            tags=["data_outage"],
            confidence="low",
        )

    risk_score = _risk_appetite_score(index_quotes)
    rates_score = _rates_impulse_score(setup=setup, macro_points=macro_points)
    commodity_score = _commodity_impulse_score(macro_quotes)
    region_score = _cross_region_confirmation(index_quotes)

    total = risk_score + rates_score + commodity_score + region_score
    confidence = _confidence_label(total=total, signals=[risk_score, rates_score, commodity_score, region_score])

    tone = _tone_phrase(total, index_quotes=index_quotes)
    breadth = _breadth_phrase(setup=setup, index_quotes=index_quotes, region_score=region_score)
    divergence = _regional_divergence_phrase(index_quotes)
    vol = _volatility_phrase(index_quotes)
    rates = _rates_phrase(setup=setup, macro_points=macro_points)
    commodities = _commodities_phrase(macro_quotes)
    takeaway = _net_takeaway(
        total=total,
        commodity_score=commodity_score,
        region_score=region_score,
        global_news=global_news or [],
    )
    dominant_driver = _dominant_tape_driver(
        setup=setup,
        global_news=global_news or [],
        macro_points=macro_points,
    )

    narrative = f"{tone} {breadth} {divergence} {vol} {rates} {commodities} {takeaway}"
    tags = _tags(risk_score=risk_score, rates_score=rates_score, commodity_score=commodity_score, region_score=region_score)
    return MarketSetupInterpretation(
        narrative=narrative,
        dominant_driver=dominant_driver,
        tags=tags,
        confidence=confidence,
    )


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


def _tone_phrase(total: int, *, index_quotes) -> str:
    positive = sum(1 for q in index_quotes if float(q.change_percent or 0.0) > 0.0)
    total_quotes = max(1, len(index_quotes))
    vix_quote = next((q for q in index_quotes if "VIX" in (q.display_name or q.symbol or "").upper()), None)
    vix_level = float(vix_quote.current_price or 0.0) if vix_quote else None
    if total >= 3:
        return "Market tone is broadly risk-on."
    if total <= -3:
        if positive >= int(total_quotes * 0.4) and (vix_level is None or vix_level < 20.0):
            return "Market tone is mixed-to-cautious rather than full risk-off."
        return "Market tone is defensive and risk-off."
    return "Market tone is mixed with no single dominant impulse."


def _breadth_phrase(*, setup: MarketSetup, index_quotes, region_score: int) -> str:
    if not index_quotes:
        return "Breadth signals are limited due to sparse index coverage."
    positive = sum(1 for q in index_quotes if float(q.change_percent or 0.0) > 0.0)
    total = len(index_quotes)
    sector_rows = list(setup.market_breadth or [])
    sector_up = sum(1 for row in sector_rows if float(row.change_percent or 0.0) > 0.0) if sector_rows else None
    sector_total = len(sector_rows) if sector_rows else None

    sector_note = ""
    if sector_total and sector_up is not None:
        if sector_up <= 3:
            sector_note = (
                f" Sector breadth is weak ({sector_up}/{sector_total} positive), so headline index resilience is not broad."
            )
        elif sector_up >= max(8, int(sector_total * 0.7)):
            sector_note = f" Sector breadth is supportive ({sector_up}/{sector_total} positive)."

    if region_score > 0:
        return (
            f"Equity breadth is constructive ({positive}/{total} tracked benchmarks up) with cross-region confirmation."
            + sector_note
        )
    if region_score < 0:
        if sector_total and sector_up is not None and sector_up >= max(8, int(sector_total * 0.7)):
            return (
                f"Index breadth is mixed-to-weak ({positive}/{total} tracked benchmarks up) under cross-region pressure, "
                f"but sector breadth has improved ({sector_up}/{sector_total} positive)."
            )
        return (
            f"Equity breadth is weak ({positive}/{total} tracked benchmarks up) with cross-region pressure."
            + sector_note
        )
    return (
        f"Equity breadth is balanced ({positive}/{total} tracked benchmarks up) without strong regional confirmation."
        + sector_note
    )


def _regional_divergence_phrase(index_quotes) -> str:
    if not index_quotes:
        return "Regional direction is unclear from available benchmarks."
    us = _region_avg(index_quotes, ("S&P", "NASDAQ", "DOW", "RUSSELL"))
    eu = _region_avg(index_quotes, ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
    asia = _region_avg(index_quotes, ("NIKKEI", "HANG SENG"))
    phrases: list[str] = []
    if us is not None:
        phrases.append(f"US {us:+.2f}%")
    if eu is not None:
        phrases.append(f"Europe {eu:+.2f}%")
    if asia is not None:
        phrases.append(f"Asia {asia:+.2f}%")
    if not phrases:
        return "Regional direction is unclear from available benchmarks."
    divergence = (
        us is not None
        and eu is not None
        and ((us > 0 and eu < 0) or (us < 0 and eu > 0))
    )
    if divergence:
        return f"Regional split is visible ({', '.join(phrases)}), signaling divergence across major sessions."
    return f"Regional performance is comparatively aligned ({', '.join(phrases)})."


def _volatility_phrase(index_quotes) -> str:
    vix = next((q for q in index_quotes if "VIX" in (q.display_name or q.symbol or "").upper()), None)
    if vix is None:
        return "Volatility signal is unavailable."
    level = float(vix.current_price or 0.0)
    move = float(vix.change_percent or 0.0)
    if level >= 25:
        regime = "stress"
    elif level >= 19:
        regime = "elevated caution"
    elif level >= 15:
        regime = "watchful but contained"
    else:
        regime = "calm"
    direction = "rising" if move > 0.5 else "falling" if move < -0.5 else "flat"
    return f"Volatility regime is {regime} (VIX {level:.2f}, {move:+.2f}%, {direction})."


def _rates_phrase(*, setup: MarketSetup, macro_points: list[MacroDataPoint]) -> str:
    ten_y_change = _macro_change(macro_points, ("10y treasury", "us 10y", "10y treasury yield"))
    spread_change = _macro_change(macro_points, ("10y-2y yield spread", "yield spread"))
    if ten_y_change is None and setup.treasury_10y:
        ten_y_change = float(setup.treasury_10y.change or 0.0)
    if ten_y_change is None and spread_change is None:
        return "Rates context is neutral from available data."

    # Tiny-move guard: moves this small are noise, not a driver
    ten_y_tiny = ten_y_change is None or abs(ten_y_change) < 0.03
    spread_tiny = spread_change is None or abs(spread_change) < 0.02
    if ten_y_tiny and spread_tiny:
        if ten_y_change is not None and ten_y_change != 0.0:
            nudge = "marginally higher" if ten_y_change > 0 else "marginally lower"
            return f"Rates are little changed from the prior close (US 10Y {nudge}); not the main story."
        return "Rates context is neutral from available data."

    bits: list[str] = []
    if ten_y_change is not None:
        direction = "lower" if ten_y_change < 0 else "higher" if ten_y_change > 0 else "flat"
        bits.append(f"US 10Y is {direction} ({ten_y_change:+.3f})")
    if spread_change is not None:
        # Only call curve direction if consistent with 10Y-2Y arithmetic
        two_y_change = _macro_change(macro_points, ("us 2y treasury", "2y treasury yield", "dgs2"))
        consistent_steepen = (
            spread_change > 0
            and (two_y_change is None or (ten_y_change or 0.0) > (two_y_change or 0.0))
        )
        consistent_flatten = (
            spread_change < 0
            and (two_y_change is None or (two_y_change or 0.0) > (ten_y_change or 0.0))
        )
        if consistent_steepen:
            curve = "steepening"
        elif consistent_flatten:
            curve = "flattening"
        else:
            curve = "little changed"
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


def _net_takeaway(
    *,
    total: int,
    commodity_score: int,
    region_score: int,
    global_news: Iterable[NormalisedEvent],
) -> str:
    text = " ".join(f"{evt.title} {evt.summary}".lower() for evt in global_news)
    has_geo_energy = any(
        term in text
        for term in ("hormuz", "iran", "israel", "ceasefire", "blockade", "tanker", "shipping", "oil")
    )
    if has_geo_energy and commodity_score <= -1:
        return "Net takeaway: this is a geopolitics-driven, oil-led session with tighter risk conditions for Europe and EM-sensitive assets."
    if total >= 3 and region_score >= 0:
        return "Net takeaway: risk appetite is constructive, but monitoring rates and energy remains important for follow-through."
    if total <= -3:
        return "Net takeaway: defensive posture is warranted as volatility and macro pressure outweigh broad equity support."
    return "Net takeaway: conditions are mixed; focus on regional dispersion and macro headlines rather than index direction alone."


_GEO_TERMS = ("iran", "hormuz", "blockade", "naval", "shipping", "tanker", "strike", "missile", "attack", "strait", "persian gulf", "invasion")
_ENERGY_TERMS = ("oil", "crude", "energy", "opec", "wti", "brent")
_TECH_EARNINGS_TOKENS = ("apple", "microsoft", "alphabet", "google", "meta", "amazon", "nvidia", "tesla", "broadcom", "tsmc", "amd")
_AI_MOMENTUM_TOKENS = ("semiconductor", "data center", "data centre", "inference", "ai chip", "gpu demand", "artificial intelligence", "hyperscaler")


def _dominant_tape_driver(
    *,
    setup: MarketSetup,
    global_news: Iterable[NormalisedEvent],
    macro_points: list[MacroDataPoint],
) -> str:
    news = list(global_news)
    text = " ".join(f"{evt.title} {evt.summary}".lower() for evt in news)
    cluster_weight = sum(max(1, int(evt.cluster_size or 1)) for evt in news)

    oil_quote = _find_quote(setup.macro_quotes or [], ("WTI", "CRUDE", "CL1:COM", "CL=F"))
    oil_move = float(oil_quote.change_percent or 0.0) if oil_quote is not None else None
    oil_level = float(oil_quote.current_price or 0.0) if oil_quote is not None else 0.0

    # --- 1. Geo-energy ---
    geo_oil_hit = (
        any(term in text for term in _GEO_TERMS)
        and any(term in text for term in _ENERGY_TERMS)
    )
    oil_is_driver = oil_move is not None and (abs(oil_move) >= 1.5 or oil_level > 90)
    geo_driver: str | None = None
    if geo_oil_hit and oil_is_driver and cluster_weight >= 5:
        _move = oil_move or 0.0
        _OIL_SPIKE_THRESHOLD = 3.0
        _OIL_ELEVATED_THRESHOLD = 1.0
        if abs(_move) > _OIL_SPIKE_THRESHOLD:
            direction = "spiking" if _move > 0 else "collapsing"
        elif abs(_move) > _OIL_ELEVATED_THRESHOLD:
            direction = "elevated" if _move > 0 else "under pressure"
        else:
            direction = "steady but elevated" if oil_level > 90 else "moving"
        level_note = f" at {oil_level:.0f} USD/bbl" if oil_level > 0 else ""
        geo_driver = (
            f"Geo-energy: oil {direction}{level_note} on Middle East/Hormuz tensions "
            f"(WTI {_move:+.1f}%), with inflation and transport-cost risk in focus."
        )

    # --- 2. Tech/AI earnings and momentum ---
    earnings_cluster = sum(
        1
        for evt in news
        if any(kw in f"{evt.title} {evt.summary}".lower() for kw in ("earnings", "results", "beat", "miss", "guidance"))
        and any(token in f"{evt.title} {evt.summary}".lower() for token in _TECH_EARNINGS_TOKENS)
    )
    ai_momentum = sum(
        1
        for evt in news
        if any(token in f"{evt.title} {evt.summary}".lower() for token in _AI_MOMENTUM_TOKENS)
    )
    tech_driver: str | None = None
    if earnings_cluster >= 2:
        tech_driver = "big-tech and AI earnings steering index leadership and intra-sector dispersion"
    elif ai_momentum >= 3:
        tech_driver = "tech and AI momentum (semiconductors, data-centre capex, inference demand) supporting growth equities"

    # --- Combined: when both clusters fire, name them together ---
    if geo_driver and tech_driver:
        return f"Split tape: commodity/geopolitical pressure (oil/Hormuz) alongside {tech_driver}."
    if geo_driver:
        return geo_driver
    if tech_driver:
        return tech_driver[0].upper() + tech_driver[1:] + "."

    # --- 3. Rates repricing ---
    ten_y_change = _macro_change(macro_points, ("10y treasury", "us 10y", "10y treasury yield"))
    if ten_y_change is None and setup.treasury_10y:
        ten_y_change = float(setup.treasury_10y.change or 0.0)
    if ten_y_change is not None and abs(ten_y_change) >= 0.035:
        direction = "higher" if ten_y_change > 0 else "lower"
        us_avg = _region_avg(setup.index_quotes or [], ("S&P", "NASDAQ", "DOW", "RUSSELL"))
        eu_avg = _region_avg(setup.index_quotes or [], ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
        asia_avg = _region_avg(setup.index_quotes or [], ("NIKKEI", "HANG SENG"))
        has_split = (
            us_avg is not None and eu_avg is not None and asia_avg is not None
            and max(us_avg, eu_avg, asia_avg) - min(us_avg, eu_avg, asia_avg) >= 0.8
        )
        if has_split:
            return (
                f"Rates repricing plus regional divergence are driving the tape "
                f"(US 10Y {direction} {ten_y_change:+.3f})."
            )
        return f"Rates repricing is the lead driver (US 10Y {direction} {ten_y_change:+.3f})."

    vix_quote = _find_quote(setup.index_quotes or [], ("VIX",))
    vix_move = float(vix_quote.change_percent or 0.0) if vix_quote is not None else 0.0
    us_avg = _region_avg(setup.index_quotes or [], ("S&P", "NASDAQ", "DOW", "RUSSELL"))
    eu_avg = _region_avg(setup.index_quotes or [], ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
    asia_avg = _region_avg(setup.index_quotes or [], ("NIKKEI", "HANG SENG"))
    has_split = (
        us_avg is not None and eu_avg is not None and asia_avg is not None
        and max(us_avg, eu_avg, asia_avg) - min(us_avg, eu_avg, asia_avg) >= 0.8
    )
    if has_split and (vix_move >= 1.5 or (oil_move is not None and abs(oil_move) >= 0.8)):
        return (
            "Regional divergence and risk-factor pressure are leading the tape; "
            "no single equity catalyst dominates."
        )
    if has_split:
        return "Regional divergence is measurable across major regions; leadership is split rather than broad risk-off."
    return "No single equity catalyst dominates; macro and regional drivers are mixed."


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


def _region_avg(index_quotes, tokens: tuple[str, ...]) -> float | None:
    values = [
        float(q.change_percent or 0.0)
        for q in index_quotes
        if any(token in (q.display_name or q.symbol or "").upper() for token in tokens)
    ]
    if not values:
        return None
    return _mean(values)
