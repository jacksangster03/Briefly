"""Deterministic regional lens builder for briefing output."""

from __future__ import annotations

from app.schemas.events import QuoteData, NormalisedEvent


def build_regional_lens(
    *,
    index_quotes: list[QuoteData],
    global_news: list[NormalisedEvent],
) -> tuple[list[dict[str, str]], str]:
    """Return region cards + one-line skew summary."""
    regions = [
        _region_card("US", index_quotes, ("S&P", "NASDAQ", "DOW", "RUSSELL"), global_news),
        _region_card("Europe", index_quotes, ("STOXX", "FTSE", "DAX", "CAC", "IBEX"), global_news),
        _region_card("Asia", index_quotes, ("NIKKEI", "HANG SENG"), global_news),
    ]

    optional = [
        _optional_region_card("Middle East", global_news, ("iran", "israel", "gulf", "hormuz", "ceasefire")),
        _optional_region_card("Russia/Ukraine", global_news, ("russia", "ukraine", "sanction")),
        _optional_region_card("Latin America", global_news, ("latam", "latin america", "brazil", "mexico")),
        _optional_region_card("Cross-Asset Spillovers", global_news, ("fx", "dollar", "yield", "credit", "spread")),
    ]
    for item in optional:
        if item:
            regions.append(item)

    us = regions[0]["direction"]
    eu = regions[1]["direction"]
    asia = regions[2]["direction"]
    skew = f"Regional skew: US {us}, Europe {eu}, Asia {asia}."
    return regions, skew


def _region_card(
    label: str,
    quotes: list[QuoteData],
    tokens: tuple[str, ...],
    global_news: list[NormalisedEvent],
) -> dict[str, str]:
    selected = [q for q in quotes if any(token in (q.display_name or q.symbol or "").upper() for token in tokens)]
    avg_move = _avg([float(q.change_percent or 0.0) for q in selected]) if selected else 0.0
    direction = "up" if avg_move > 0.15 else "down" if avg_move < -0.15 else "mixed"
    status = "lead" if abs(avg_move) >= 1.0 else "active" if abs(avg_move) >= 0.4 else "monitor"
    driver = _driver_phrase(global_news)
    implication = _implication(direction=direction, region=label)
    return {
        "region": label,
        "direction": direction,
        "status": status,
        "driver": driver,
        "implication": implication,
    }


def _optional_region_card(
    label: str,
    global_news: list[NormalisedEvent],
    terms: tuple[str, ...],
) -> dict[str, str] | None:
    text = " ".join(f"{evt.title} {evt.summary}".lower() for evt in global_news)
    if not any(term in text for term in terms):
        return None
    return {
        "region": label,
        "direction": "active",
        "status": "active",
        "driver": "headline-driven macro transmission",
        "implication": "Watch spillover into energy, FX, and risk premium channels.",
    }


def _driver_phrase(events: list[NormalisedEvent]) -> str:
    text = " ".join(f"{evt.title} {evt.summary}".lower() for evt in events)
    if any(term in text for term in ("hormuz", "iran", "israel", "oil", "shipping")):
        return "geopolitics and energy"
    if any(term in text for term in ("fed", "ecb", "rates", "yield", "inflation")):
        return "rates and inflation repricing"
    if any(term in text for term in ("earnings", "guidance", "results")):
        return "earnings and guidance dispersion"
    return "mixed macro and flow dynamics"


def _implication(*, direction: str, region: str) -> str:
    if direction == "up":
        return f"{region} participation supports broad risk appetite if volatility stays contained."
    if direction == "down":
        return f"{region} weakness increases downside sensitivity and can pressure cyclicals."
    return f"{region} is mixed, so confirmation should come from cross-asset signals."


def _avg(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0

