"""Deterministic regional lens builder for briefing output.

Holiday-aware Europe partial-open logic:
- If UK is closed but continental European exchanges (DAX, EURO STOXX, CAC, IBEX)
  have valid data, Europe status is "partial" not "unavailable".
- The regional skew must not say "Europe unavailable" while continental data exists.
"""

from __future__ import annotations

from app.schemas.events import QuoteData, NormalisedEvent


def _uk_closed_today() -> bool:
    """Return True if today is a UK bank holiday or weekend (UTC date)."""
    try:
        from datetime import date, timezone, datetime
        from app.markets.calendar import is_uk_market_holiday
        d = datetime.now(timezone.utc).date()
        return d.weekday() >= 5 or is_uk_market_holiday(d)
    except Exception:
        return False


def build_regional_lens(
    *,
    index_quotes: list[QuoteData],
    global_news: list[NormalisedEvent],
) -> tuple[list[dict[str, str]], str]:
    """Return region cards + one-line skew summary."""
    regions = [
        _region_card("US", index_quotes, ("S&P", "NASDAQ", "DOW", "RUSSELL"), global_news),
        _europe_region_card(index_quotes, global_news),
        _asia_region_card(index_quotes, global_news),
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


def _europe_region_card(
    quotes: list[QuoteData],
    global_news: list[NormalisedEvent],
) -> dict[str, str]:
    """Build the Europe region card with partial-open awareness.

    Rules:
    - If >= 2 valid continental index inputs (DAX, EURO STOXX, CAC, IBEX) are
      present, Europe is "active" or "partial", never "unavailable".
    - If UK is closed but continental data exists, status is "partial".
    - If only FTSE is present and UK is closed, status is "unavailable".
    - Asia prior/closed context is labelled as such, not unavailable.
    """
    # Separate UK (FTSE) from continental (DAX, STOXX, CAC, IBEX) quotes.
    continental_tokens = ("STOXX", "DAX", "CAC", "IBEX")
    uk_tokens = ("FTSE",)

    continental = [
        q for q in quotes
        if any(tok in (q.display_name or q.symbol or "").upper() for tok in continental_tokens)
    ]
    uk = [
        q for q in quotes
        if any(tok in (q.display_name or q.symbol or "").upper() for tok in uk_tokens)
    ]
    all_europe = continental + uk

    uk_closed = _uk_closed_today()

    if not all_europe:
        return {
            "region": "Europe",
            "direction": "unavailable",
            "status": "unavailable",
            "driver": "insufficient live market inputs",
            "implication": "Europe directional read unavailable; wait for provider recovery.",
        }

    # When UK is closed but continental data is present, use partial status.
    if uk_closed and len(continental) >= 1:
        source_quotes = continental if continental else all_europe
        avg_move = _avg([float(q.change_percent or 0.0) for q in source_quotes])
        direction = "up" if avg_move > 0.15 else "down" if avg_move < -0.15 else "mixed"
        driver = _driver_phrase(global_news)
        implication = _implication(direction=direction, region="Europe")
        return {
            "region": "Europe",
            "direction": direction,
            "status": "partial",
            "partial_detail": "UK closed, continental open",
            "driver": driver,
            "implication": implication,
        }

    # Standard case: use all available Europe quotes.
    avg_move = _avg([float(q.change_percent or 0.0) for q in all_europe])
    direction = "up" if avg_move > 0.15 else "down" if avg_move < -0.15 else "mixed"
    status = "lead" if abs(avg_move) >= 1.0 else "active" if abs(avg_move) >= 0.4 else "monitor"
    driver = _driver_phrase(global_news)
    implication = _implication(direction=direction, region="Europe")
    return {
        "region": "Europe",
        "direction": direction,
        "status": status,
        "driver": driver,
        "implication": implication,
    }


def _asia_region_card(
    quotes: list[QuoteData],
    global_news: list[NormalisedEvent],
) -> dict[str, str]:
    """Build the Asia region card.

    If Asia has valid closed-session values, label as "prior/closed context"
    rather than "unavailable".
    """
    tokens = ("NIKKEI", "HANG SENG", "HSI", "KOSPI", "ASX")
    selected = [q for q in quotes if any(tok in (q.display_name or q.symbol or "").upper() for tok in tokens)]
    if not selected:
        return {
            "region": "Asia",
            "direction": "unavailable",
            "status": "unavailable",
            "driver": "insufficient live market inputs",
            "implication": "Asia directional read unavailable; wait for provider recovery.",
        }
    avg_move = _avg([float(q.change_percent or 0.0) for q in selected])
    direction = "up" if avg_move > 0.15 else "down" if avg_move < -0.15 else "mixed"
    # Asia markets are typically closed during EMEA/US sessions; use prior context label.
    status = "prior_closed_context"
    driver = _driver_phrase(global_news)
    implication = _implication(direction=direction, region="Asia")
    return {
        "region": "Asia",
        "direction": direction,
        "status": status,
        "driver": driver,
        "implication": implication,
    }


def _region_card(
    label: str,
    quotes: list[QuoteData],
    tokens: tuple[str, ...],
    global_news: list[NormalisedEvent],
) -> dict[str, str]:
    selected = [q for q in quotes if any(token in (q.display_name or q.symbol or "").upper() for token in tokens)]
    if not selected:
        return {
            "region": label,
            "direction": "unavailable",
            "status": "unavailable",
            "driver": "insufficient live market inputs",
            "implication": f"{label} directional read unavailable; wait for provider recovery.",
        }
    avg_move = _avg([float(q.change_percent or 0.0) for q in selected])
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
