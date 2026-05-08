"""Deterministic-first news intelligence classification helpers.

This layer augments events with structured story-type and freshness metadata.
It never makes delivery decisions directly; downstream rule gates remain the
final authority for send/suppress actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Literal

from app.schemas.events import NormalisedEvent

StoryType = Literal[
    "breaking_market_moving",
    "earnings_results",
    "guidance_change",
    "analyst_action",
    "macro_policy",
    "geopolitical_energy",
    "regulatory_legal",
    "mna_deal",
    "product_partnership",
    "filing_sec",
    "insider_transaction",
    "credit_debt",
    "commentary_valuation",
    "generic_market_wrap",
    "low_signal",
    "ignore",
]

FreshnessState = Literal["new", "updated", "repeated", "stale", "old_context", "unknown"]
BreakingLabel = Literal["BREAKING", "UPDATE", "CONTEXT", "LATE DISCOVERY"]


@dataclass(frozen=True)
class NewsClassification:
    story_type: StoryType
    freshness_state: FreshnessState
    event_time: datetime | None
    published_time: datetime | None
    first_seen_time: datetime | None
    last_seen_time: datetime | None
    material_update: bool
    market_relevance_score: float
    portfolio_relevance_score: float
    confidence: float
    affected_assets: list[str]
    why_market_relevant: str
    suppress_reason: str
    breaking_label: BreakingLabel


_LOW_SIGNAL_PATTERNS = (
    "stock looks cheap",
    "smart buy",
    "risky move",
    "losing its edge",
    "should you buy",
    "price prediction",
    "no-brainer",
    "what's behind",
    "what’s behind",
    "market wrap",
)

_POLLING_PATTERNS = (
    "approval poll",
    "approval rating",
    "polling",
    "election poll",
)

_HARD_CATALYST_TERMS = (
    "earnings",
    "guidance",
    "fomc",
    "fed",
    "ecb",
    "cpi",
    "ppi",
    "pce",
    "payroll",
    "fda",
    "chmp",
    "trial",
    "lawsuit",
    "antitrust",
    "merger",
    "acquisition",
    "deal",
    "sanction",
    "tariff",
    "hormuz",
    "oil",
    "crude",
    "gas",
    "blockade",
)

_COMMENTARY_TERMS = (
    "valuation",
    "commentary",
    "opinion",
    "column",
    "editorial",
    "analysts say",
)

_SEC_TYPES = {"sec_filing", "filing", "8k", "10q", "10k"}


def classify_news_event(
    event: NormalisedEvent,
    *,
    now: datetime | None = None,
    breaking_max_age_hours: int = 6,
) -> NewsClassification:
    """Classify event deterministically for freshness and story type."""
    now = now or datetime.now(timezone.utc)
    text = _norm(f"{event.title} {event.summary}")
    published = _to_utc(event.published_at)
    first_seen = _to_utc(_dt_from_raw(event, "first_seen_at"))
    last_seen = _to_utc(_dt_from_raw(event, "last_seen_at"))
    material_update = event.update_status == "material_update"

    freshness = _freshness_state(
        event=event,
        now=now,
        published=published,
        first_seen=first_seen,
        breaking_max_age_hours=breaking_max_age_hours,
    )
    story_type = _story_type(event, text)
    suppress_reason = _suppress_reason(event, text, story_type)
    market_score = _market_relevance(event, text, story_type)
    portfolio_score = _portfolio_relevance(event, text)
    confidence = _confidence(event, story_type, freshness)
    breaking_label = _breaking_label(
        freshness=freshness,
        material_update=material_update,
        market_score=market_score,
    )

    return NewsClassification(
        story_type=story_type,
        freshness_state=freshness,
        event_time=published,
        published_time=published,
        first_seen_time=first_seen,
        last_seen_time=last_seen,
        material_update=material_update,
        market_relevance_score=market_score,
        portfolio_relevance_score=portfolio_score,
        confidence=confidence,
        affected_assets=_affected_assets(event),
        why_market_relevant=_why_market_relevant(story_type, text),
        suppress_reason=suppress_reason,
        breaking_label=breaking_label,
    )


def annotate_news_events(
    events: list[NormalisedEvent],
    *,
    now: datetime | None = None,
    breaking_max_age_hours: int = 6,
) -> list[NormalisedEvent]:
    """Attach classification metadata to raw_data for downstream gating/audit."""
    for event in events:
        cls = classify_news_event(
            event,
            now=now,
            breaking_max_age_hours=breaking_max_age_hours,
        )
        raw = dict(event.raw_data or {})
        raw["news_story_type"] = cls.story_type
        raw["news_freshness_state"] = cls.freshness_state
        raw["news_material_update"] = cls.material_update
        raw["news_market_relevance_score"] = round(cls.market_relevance_score, 3)
        raw["news_portfolio_relevance_score"] = round(cls.portfolio_relevance_score, 3)
        raw["news_classifier_confidence"] = round(cls.confidence, 3)
        raw["news_affected_assets"] = cls.affected_assets
        raw["news_why_market_relevant"] = cls.why_market_relevant
        raw["news_suppress_reason"] = cls.suppress_reason
        raw["breaking_label"] = cls.breaking_label
        if cls.published_time:
            raw["news_published_at"] = cls.published_time.isoformat()
        event.raw_data = raw
    return events


def should_suppress_low_signal(event: NormalisedEvent) -> bool:
    story_type = str((event.raw_data or {}).get("news_story_type", "")).lower()
    suppress_reason = str((event.raw_data or {}).get("news_suppress_reason", "")).strip()
    if story_type in {"low_signal", "ignore", "generic_market_wrap", "commentary_valuation"} and suppress_reason:
        return True
    return False


def is_stale_breaking_candidate(event: NormalisedEvent) -> tuple[bool, str]:
    """Return whether event must be blocked from BREAKING + reason."""
    raw = event.raw_data or {}
    freshness = str(raw.get("news_freshness_state", "unknown")).lower()
    label = str(raw.get("breaking_label", "CONTEXT")).upper()
    if label == "LATE DISCOVERY":
        return True, "late_discovery_old_published_time"
    if freshness in {"old_context", "stale"}:
        return True, f"freshness_{freshness}"
    return False, ""


def _story_type(event: NormalisedEvent, text: str) -> StoryType:
    event_type = (event.event_type or "").lower()
    if event_type in {"earnings", "earnings_results"}:
        return "earnings_results"
    if "guidance" in text:
        return "guidance_change"
    if event_type in {"macro_release", "fed_decision"} or any(term in text for term in ("fomc", "fed", "ecb", "cpi", "pce", "payroll")):
        return "macro_policy"
    if event_type == "geopolitical" or any(term in text for term in ("hormuz", "iran", "israel", "sanction", "oil", "crude")):
        return "geopolitical_energy"
    if event_type in {"regulatory", "fda", "legal"} or any(term in text for term in ("fda", "lawsuit", "antitrust", "chmp")):
        return "regulatory_legal"
    if any(term in text for term in ("merger", "acquisition", "m&a", "takeover", "buyout")):
        return "mna_deal"
    if any(term in text for term in ("partnership", "collaboration", "product launch")):
        return "product_partnership"
    if event.source == "sec_edgar" or event_type in _SEC_TYPES:
        return "filing_sec"
    if any(term in text for term in ("insider", "director bought", "ceo sold")):
        return "insider_transaction"
    if any(term in text for term in ("debt", "credit", "downgrade", "default", "refinancing")):
        return "credit_debt"
    if any(term in text for term in _COMMENTARY_TERMS):
        return "commentary_valuation"
    if any(term in text for term in _LOW_SIGNAL_PATTERNS):
        return "low_signal"
    if "wrap" in text or "recap" in text:
        return "generic_market_wrap"
    if event_type in {"analyst", "analyst_action"}:
        return "analyst_action"
    return "breaking_market_moving" if any(term in text for term in _HARD_CATALYST_TERMS) else "ignore"


def _freshness_state(
    *,
    event: NormalisedEvent,
    now: datetime,
    published: datetime | None,
    first_seen: datetime | None,
    breaking_max_age_hours: int,
) -> FreshnessState:
    if event.update_status == "material_update":
        return "updated"
    if event.update_status == "duplicate":
        return "repeated"
    if not published:
        return "unknown"
    age = now - published
    if age > timedelta(hours=max(1, breaking_max_age_hours)):
        if first_seen and (now - first_seen) <= timedelta(hours=2):
            return "old_context"
        return "stale"
    if age <= timedelta(hours=24):
        return "new"
    return "old_context"


def _suppress_reason(event: NormalisedEvent, text: str, story_type: StoryType) -> str:
    if story_type == "ignore":
        return "no_hard_catalyst"
    if story_type in {"low_signal", "generic_market_wrap"}:
        return "low_signal_format"
    if story_type == "commentary_valuation" and not _has_hard_link(text):
        return "valuation_commentary_without_catalyst"
    if any(term in text for term in _POLLING_PATTERNS):
        only_etf = bool(event.tickers) and all(t in {"SPY", "QQQ", "ACWI"} for t in event.tickers)
        if only_etf:
            return "generic_polling_with_etf_proxy_only"
    return ""


def _market_relevance(event: NormalisedEvent, text: str, story_type: StoryType) -> float:
    score = float(event.final_score or 0.0)
    if story_type in {"earnings_results", "guidance_change", "macro_policy", "geopolitical_energy", "mna_deal"}:
        score += 0.15
    if _has_hard_link(text):
        score += 0.1
    if story_type in {"low_signal", "generic_market_wrap", "ignore"}:
        score -= 0.25
    return max(0.0, min(1.0, score))


def _portfolio_relevance(event: NormalisedEvent, text: str) -> float:
    score = float(event.personal_relevance_score or 0.0)
    if event.tickers:
        score += 0.1
    if any(term in text for term in ("holding", "watchlist", "portfolio")):
        score += 0.05
    return max(0.0, min(1.0, score))


def _confidence(event: NormalisedEvent, story_type: StoryType, freshness: FreshnessState) -> float:
    score = float(event.factual_confidence_score or 0.0)
    if story_type in {"low_signal", "ignore", "generic_market_wrap"}:
        score -= 0.15
    if freshness in {"stale", "old_context", "unknown"}:
        score -= 0.1
    return max(0.0, min(1.0, score))


def _breaking_label(*, freshness: FreshnessState, material_update: bool, market_score: float) -> BreakingLabel:
    if freshness == "old_context":
        return "LATE DISCOVERY"
    if freshness == "stale":
        return "CONTEXT"
    if material_update:
        return "UPDATE"
    if freshness == "new" and market_score >= 0.65:
        return "BREAKING"
    return "CONTEXT"


def _affected_assets(event: NormalisedEvent) -> list[str]:
    assets: list[str] = []
    for ticker in (event.tickers or [])[:6]:
        if ticker not in assets:
            assets.append(ticker)
    return assets


def _why_market_relevant(story_type: StoryType, text: str) -> str:
    if story_type == "guidance_change":
        return "Guidance updates can reprice near-term growth and margin expectations."
    if story_type == "earnings_results":
        return "Earnings results reset expectations and can move sector leadership."
    if story_type == "macro_policy":
        return "Macro policy and rates signals can reprice discount-rate and risk premia."
    if story_type == "geopolitical_energy":
        return "Energy/geopolitical developments can transmit through inflation and risk sentiment."
    if story_type == "mna_deal":
        return "Deal activity can reprice strategic value and peer multiples."
    if _has_hard_link(text):
        return "This headline maps to a direct market catalyst."
    return "Context item; limited direct catalyst evidence."


def _has_hard_link(text: str) -> bool:
    return any(term in text for term in _HARD_CATALYST_TERMS)


def _dt_from_raw(event: NormalisedEvent, key: str) -> datetime | None:
    raw = event.raw_data or {}
    value = raw.get(key)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("’", "'").replace("‘", "'").replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()
