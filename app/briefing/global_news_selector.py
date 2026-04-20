"""Deterministic selection helpers for global market/geopolitical sections."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable

from app.schemas.events import NormalisedEvent

GLOBAL_MARKET_EVENT_TYPES = {
    "macro_release",
    "fed_decision",
    "geopolitical",
    "regulatory",
}

GLOBAL_MARKET_LINK_TERMS = (
    "inflation",
    "cpi",
    "ppi",
    "pce",
    "payroll",
    "jobs",
    "fomc",
    "fed",
    "ecb",
    "boe",
    "boj",
    "rates",
    "rate hike",
    "rate cut",
    "yield",
    "treasury",
    "sanction",
    "tariff",
    "export restriction",
    "blockade",
    "war",
    "conflict",
    "ceasefire",
    "missile",
    "iran",
    "israel",
    "hormuz",
    "oil",
    "crude",
    "gas",
    "lng",
    "shipping",
    "tanker",
    "freight",
    "dollar",
    "fx",
    "currency",
    "supply chain",
    "manufacturing",
    "imports",
    "exports",
)

GLOBAL_CATALYST_TERMS = (
    "sanction",
    "tariff",
    "blockade",
    "ceasefire",
    "strike",
    "attack",
    "fomc",
    "fed",
    "ecb",
    "boe",
    "boj",
    "inflation",
    "cpi",
    "ppi",
    "pce",
    "payroll",
    "jobs",
    "oil",
    "hormuz",
    "opec",
    "shipping",
    "supply chain",
)

GLOBAL_HARD_BLOCK_PATTERNS = (
    "jim cramer",
    "youtuber",
    "celebrity",
    "salary",
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "stock to buy",
    "stock is a buy",
    "stock is cheap",
    "is it a buy",
    "best stock",
    "top stock",
)

GLOBAL_SOFT_PENALTY_PATTERNS = (
    "analysts say",
    "wall street thinks",
    "price target",
    "reiterates buy",
    "buy rating",
    "valuation",
    "opinion",
    "commentary",
    "pundit",
)

GLOBAL_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"^\s*\d+\s+(?:reasons|stocks|things|ways)\s+to\b"),
]

WEEKEND_LOW_TRUST_SOURCES = (
    "motley fool",
    "fool.com",
    "investorplace",
    "benzinga",
    "zacks",
    "thestreet",
)


def _normalise_text(value: str) -> str:
    return (
        (value or "")
        .lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("—", "-")
        .replace("–", "-")
        .replace("\xa0", " ")
    )


def event_tracking_key(event: NormalisedEvent) -> str:
    if event.cluster_id:
        return f"cluster:{event.cluster_id}"
    if event.content_hash:
        return f"hash:{event.content_hash}"
    return f"title:{_normalise_text(event.title).strip()}"


def _region_profile_keys(region: str) -> list[str]:
    key = _normalise_text(region).replace(" ", "_")
    if key == "global_macro":
        return ["global_macro", "global"]
    return [key]


def _region_overlap_bonus(event: NormalisedEvent, region_weights: dict[str, float] | None) -> float:
    if not region_weights or not event.regions:
        return 0.0
    best = 0.0
    for region in event.regions:
        for key in _region_profile_keys(region):
            best = max(best, float(region_weights.get(key, 0.0)))
    return min(0.03, best * 0.02)


def _has_catalyst_signal(event: NormalisedEvent, text_lower: str) -> bool:
    if event.event_type in GLOBAL_MARKET_EVENT_TYPES:
        return True
    return any(term in text_lower for term in GLOBAL_CATALYST_TERMS)


def _is_market_linked(event: NormalisedEvent, text_lower: str) -> bool:
    if event.event_type in GLOBAL_MARKET_EVENT_TYPES:
        return True
    return any(term in text_lower for term in GLOBAL_MARKET_LINK_TERMS)


def _editorial_score(
    event: NormalisedEvent,
    *,
    session_mode: str,
    text_lower: str,
    title_lower: str,
    has_catalyst: bool,
) -> float:
    score = 0.0
    if event.source == "sec_edgar":
        score += 1.2
    if event.factual_confidence_score >= 0.80:
        score += 0.8
    elif event.factual_confidence_score >= 0.65:
        score += 0.5
    else:
        score += 0.2

    if event.cluster_size >= 4:
        score += 0.35
    elif event.cluster_size >= 2:
        score += 0.2

    if has_catalyst:
        score += 0.5

    for pattern in GLOBAL_SOFT_PENALTY_PATTERNS:
        if pattern in text_lower:
            score -= 0.35
    if any(rx.search(title_lower) for rx in GLOBAL_SOFT_PENALTY_REGEXES):
        score -= 0.55
    if "?" in title_lower and not has_catalyst:
        score -= 0.2

    if session_mode in {"saturday", "sunday"}:
        source_name = _normalise_text(str(event.raw_data.get("source_name", "")))
        url_lower = _normalise_text(event.url)
        if any(marker in source_name or marker in url_lower for marker in WEEKEND_LOW_TRUST_SOURCES):
            score -= 0.45

    return score


def is_global_market_news_worthy(
    event: NormalisedEvent,
    *,
    session_mode: str,
    min_final_score: float,
    min_editorial_score: float,
    actionable_check: Callable[[NormalisedEvent], bool] | None = None,
) -> tuple[bool, float, bool]:
    """Return (worthy, editorial_score, has_catalyst)."""
    if actionable_check and not actionable_check(event):
        return False, 0.0, False

    text_lower = _normalise_text(f"{event.title} {event.summary}")
    title_lower = _normalise_text(event.title)
    has_catalyst = _has_catalyst_signal(event, text_lower)

    if any(pattern in text_lower for pattern in GLOBAL_HARD_BLOCK_PATTERNS) and not has_catalyst:
        return False, 0.0, has_catalyst

    if not _is_market_linked(event, text_lower):
        return False, 0.0, has_catalyst

    if event.final_score < min_final_score:
        return False, 0.0, has_catalyst

    if event.cluster_size < 2 and not has_catalyst and event.factual_confidence_score < 0.75:
        return False, 0.0, has_catalyst

    score = _editorial_score(
        event,
        session_mode=session_mode,
        text_lower=text_lower,
        title_lower=title_lower,
        has_catalyst=has_catalyst,
    )
    return score >= min_editorial_score, score, has_catalyst


def select_global_market_events(
    events: list[NormalisedEvent],
    *,
    session_mode: str,
    max_items: int,
    min_final_score: float = 0.62,
    min_editorial_score: float = 1.1,
    region_weights: dict[str, float] | None = None,
    actionable_check: Callable[[NormalisedEvent], bool] | None = None,
) -> list[NormalisedEvent]:
    """Select high-trust global/geopolitical stories with market linkage."""
    candidates: list[tuple[float, float, float, datetime, NormalisedEvent]] = []
    for event in events:
        worthy, editorial, has_catalyst = is_global_market_news_worthy(
            event,
            session_mode=session_mode,
            min_final_score=min_final_score,
            min_editorial_score=min_editorial_score,
            actionable_check=actionable_check,
        )
        if not worthy:
            continue

        priority = event.final_score
        if has_catalyst:
            priority += 0.03
        if event.cluster_size >= 4:
            priority += 0.02
        priority += _region_overlap_bonus(event, region_weights)

        published = event.published_at
        if published is None:
            published = datetime.fromtimestamp(0, tz=timezone.utc)
        elif published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        else:
            published = published.astimezone(timezone.utc)
        candidates.append((priority, editorial, float(event.cluster_size), published, event))

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)

    selected: list[NormalisedEvent] = []
    seen_keys: set[str] = set()
    for _, _, _, _, event in candidates:
        key = event_tracking_key(event)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(event)
        if len(selected) >= max_items:
            break
    return selected


def build_market_relevance_note(
    event: NormalisedEvent,
    *,
    used_notes: set[str] | None = None,
) -> str:
    """Generate a concise market-impact phrase for global sections."""
    text = _normalise_text(f"{event.title} {event.summary}")
    templates: list[str]
    is_em_fx = any(term in text for term in ("fx", "currency", "dollar", "yuan", "yen", "euro", "devaluation"))
    is_conflict = any(term in text for term in ("iran", "israel", "war", "conflict", "ceasefire", "missile", "naval"))
    if any(term in text for term in ("hormuz", "opec", "oil", "crude", "shipping", "tanker", "freight")):
        templates = [
            "Why market-relevant: energy chokepoints can quickly reprice inflation and transport-sensitive sectors.",
            "Why market-relevant: oil and shipping shocks can move inflation expectations, margins, and risk premia.",
        ]
    elif any(term in text for term in ("fomc", "fed", "ecb", "boe", "boj", "inflation", "cpi", "ppi", "yield", "rates")):
        templates = [
            "Why market-relevant: rates and inflation repricing can rotate equity leadership and valuation multiples.",
            "Why market-relevant: policy-rate expectations can move discount rates, duration assets, and USD direction.",
        ]
    elif any(term in text for term in ("sanction", "tariff", "export restriction", "trade", "blockade")):
        templates = [
            "Why market-relevant: sanctions and trade controls can hit supply chains, earnings guidance, and FX.",
            "Why market-relevant: policy frictions can alter trade volumes, input costs, and cross-border risk appetite.",
        ]
    elif is_conflict:
        templates = [
            "Why market-relevant: conflict headlines can shift energy risk premia and broad risk sentiment.",
            "Why market-relevant: geopolitical escalation/de-escalation can rapidly move commodities, rates, and defensives.",
        ]
    elif is_em_fx:
        templates = [
            "Why market-relevant: FX stress can tighten financial conditions and pressure EM-sensitive equities.",
            "Why market-relevant: currency volatility can reprice funding costs, trade exposure, and regional risk appetite.",
        ]
    elif any(term in text for term in ("supply chain", "manufacturing", "imports", "exports", "factory")):
        templates = [
            "Why market-relevant: supply-chain pressure can feed through to costs, delivery timing, and margin outlooks.",
            "Why market-relevant: production and trade flow changes can alter earnings momentum across cyclicals.",
        ]
    else:
        templates = [
            "Why market-relevant: cross-asset macro moves can quickly shift index direction and sector rotation.",
            "Why market-relevant: headline risk can change liquidity tone, factor leadership, and benchmark dispersion.",
        ]
    return _pick_unique_note(templates, used_notes)


def _pick_unique_note(templates: list[str], used_notes: set[str] | None) -> str:
    if not templates:
        return "Why market-relevant: cross-asset macro moves can quickly shift index direction and sector rotation."
    if used_notes is None:
        return templates[0]
    for candidate in templates:
        if candidate not in used_notes:
            used_notes.add(candidate)
            return candidate
    # Fallback if all templates already used.
    used_notes.add(templates[0])
    return templates[0]
