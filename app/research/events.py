"""Build, rank, diff and summarize analyst-grade research events."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.processing.research_event_builder import build_research_event, composite_research_score
from app.research.confidence import calculate_research_confidence
from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import EvidenceItem, ResearchEvent

DeltaStatus = Literal["new", "material_update", "carried_forward", "repeated", "stale"]


@dataclass(frozen=True)
class ResearchEventDelta:
    status: DeltaStatus
    changed_fields: list[str] = field(default_factory=list)
    previous_event_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ResearchRunFreshness:
    newest_event_at: datetime | None
    provider_contributions: dict[str, int]
    stale_provider_count: int
    outage_provider_count: int
    source_basis_label: str
    build_failures: int = 0


_CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}


def build_research_events(
    events: list[NormalisedEvent],
    *,
    quotes_by_symbol: dict[str, QuoteData] | None = None,
    preferred_symbols: set[str] | None = None,
    previous_snapshot: object | None = None,
    now: datetime | None = None,
    failures_out: list[int] | None = None,
) -> list[ResearchEvent]:
    """Convert already-scored NormalisedEvents into deterministic ResearchEvents.

    If failures_out is provided (a single-element list), it is set to the count
    of events that raised an exception during build. Callers can pass this to
    build_research_freshness_summary for diagnostics.
    """
    built: list[ResearchEvent] = []
    failures = 0
    for event in events:
        try:
            research_event = build_research_event(
                event,
                quotes_by_symbol=quotes_by_symbol or {},
                preferred_symbols=preferred_symbols or set(),
            )
            _attach_evidence(research_event, event)
            _attach_analyst_fields(research_event, event)
            conf = calculate_research_confidence(research_event)
            research_event.confidence_label = conf.label
            research_event.confidence_reason = conf.reason
            research_event.confidence_factors = conf.factors
            research_event.final_score = composite_research_score(research_event)
            built.append(research_event)
        except Exception:
            failures += 1
            continue
    if failures_out is not None and isinstance(failures_out, list):
        if failures_out:
            failures_out[0] = failures
        else:
            failures_out.append(failures)
    return built


def rank_research_events(
    events: list[ResearchEvent],
    *,
    session_key: str,
    max_items: int,
    min_confidence: str = "low",
) -> list[ResearchEvent]:
    """Rank events for briefing use while filtering non-eligible confidence."""
    min_rank = _CONFIDENCE_ORDER.get((min_confidence or "low").lower(), 1)
    eligible = [
        event
        for event in events
        if _CONFIDENCE_ORDER.get(str(event.confidence_label or "none"), 0) >= min_rank
    ]
    key = (session_key or "morning").lower()
    if key != "morning":
        eligible = [
            event for event in eligible
            if event.material_update or event.freshness_state in {"new", "updated", "material_update"}
        ] or eligible
    return sorted(
        eligible,
        key=lambda event: (
            float(event.final_score or 0.0),
            _CONFIDENCE_ORDER.get(event.confidence_label, 0),
            float(event.corroboration_score or 0.0),
            float(event.source_quality_score or 0.0),
        ),
        reverse=True,
    )[:max(0, int(max_items or 0))]


def diff_research_events(
    current: list[ResearchEvent],
    previous: list[ResearchEvent],
) -> list[ResearchEventDelta]:
    """Diff events by source id/title hash and material analyst fields."""
    previous_by_key = {_event_key(event): event for event in previous}
    deltas: list[ResearchEventDelta] = []
    for event in current:
        prev = previous_by_key.get(_event_key(event))
        if prev is None:
            deltas.append(ResearchEventDelta("new", reason="New evidence item not present in prior comparable session."))
            continue
        changed: list[str] = []
        if event.material_update and not prev.material_update:
            changed.append("material_update")
        if event.price_confirmation_status != prev.price_confirmation_status:
            changed.append("price_confirmation")
        if event.source_tier != prev.source_tier:
            changed.append("source_tier")
        if abs(float(event.portfolio_relevance_score or 0.0) - float(prev.portfolio_relevance_score or 0.0)) >= 0.15:
            changed.append("portfolio_relevance")
        if changed:
            deltas.append(
                ResearchEventDelta(
                    "material_update",
                    changed_fields=changed,
                    previous_event_id=prev.event_id,
                    reason=f"Material research fields changed: {', '.join(changed)}.",
                )
            )
        elif str(event.freshness_state).lower() in {"stale", "old_context"}:
            deltas.append(ResearchEventDelta("stale", previous_event_id=prev.event_id, reason="Repeated stale context."))
        else:
            deltas.append(ResearchEventDelta("repeated", previous_event_id=prev.event_id, reason="No material research fields changed."))
    return deltas


def build_research_freshness_summary(
    events: list[ResearchEvent],
    *,
    provider_contributions: dict[str, int] | None = None,
    timezone_label: str = "UTC",
    build_failures: int = 0,
) -> ResearchRunFreshness:
    newest = max((e.published_at for e in events if e.published_at), default=None)
    contributions = dict(provider_contributions or {})
    source_counter = Counter()
    for event in events:
        source = str((event.confidence_factors or {}).get("source_tier") or event.source_tier or "unknown")
        source_counter[source] += 1
    stale_count = sum(1 for event in events if str(event.freshness_state).lower() in {"stale", "old_context", "repeated"})
    active = [name for name, count in contributions.items() if int(count or 0) > 0]
    unavailable = [name for name, count in contributions.items() if int(count or 0) <= 0]
    outage_count = len(unavailable)

    # Convert the newest timestamp to local time so the label shows "08:12 CET"
    # rather than "08:12 UTC Europe/Madrid".
    if newest:
        try:
            local_newest = newest.astimezone(ZoneInfo(timezone_label))
            through = local_newest.strftime("%H:%M %Z").strip()
        except Exception:
            through = newest.strftime("%H:%M UTC")
        label = f"Source basis: fresh through {through}"
    else:
        label = "Source basis: no timestamped evidence cleared the research bar"

    if active:
        # Capitalise provider names for readability: "finnhub" -> "Finnhub"
        active_display = "/".join(n.title() for n in active[:4])
        label += f"; {active_display} active"
    if unavailable:
        unavail_display = "/".join(n.title() for n in unavailable[:3])
        label += f"; {unavail_display} unavailable"
    label += "."

    if build_failures:
        label += f" ({build_failures} event(s) skipped during build)"

    return ResearchRunFreshness(newest, contributions, stale_count, outage_count, label, build_failures)


def _attach_evidence(research_event: ResearchEvent, event: NormalisedEvent) -> None:
    raw = dict(event.raw_data or {})
    source_name = str(raw.get("source_name") or raw.get("source") or event.source or "").strip()
    tier = research_event.source_tier or str(raw.get("source_tier_label") or "")
    if event.url:
        item = EvidenceItem(
            url=event.url,
            source_name=source_name,
            source_tier=tier,
            published_at=event.published_at,
            snippet=(event.summary or event.title or "")[:220],
        )
        research_event.evidence_items = [item]
        research_event.primary_sources = [event.url] if research_event.source_quality_score >= 0.75 else []
        research_event.secondary_sources = [] if research_event.primary_sources else [event.url]


def _attach_analyst_fields(research_event: ResearchEvent, event: NormalisedEvent) -> None:
    tickers = ", ".join(research_event.tickers[:3]) or "affected assets"
    freshness = str(research_event.freshness_state or "unknown").replace("_", " ")
    research_event.why_now = (
        "Fresh material update from provider evidence."
        if research_event.material_update
        else f"Freshness state: {freshness}."
    )
    if research_event.portfolio_relevance_score >= 0.75:
        research_event.portfolio_impact = f"Direct portfolio/watchlist relevance for {tickers}."
    elif research_event.portfolio_relevance_score >= 0.45 or research_event.watchlist_relevance_score >= 0.45:
        research_event.portfolio_impact = f"Relevant read-through for {tickers}."
    else:
        research_event.portfolio_impact = "Market-context item; portfolio impact depends on confirmation."
    if research_event.material_update:
        research_event.what_changed = "Material update versus prior classification."
    elif event.update_status == "new":
        research_event.what_changed = "New item in the current provider run."
    else:
        research_event.what_changed = f"Status: {event.update_status or 'unknown'}."
    if research_event.price_confirmation_status in {"confirmed", "contradicted", "pending"}:
        research_event.what_to_watch_next = research_event.price_confirmation_detail
    elif research_event.causal_channel == "rates":
        research_event.what_to_watch_next = "Watch yields, dollar and valuation-sensitive growth response."
    elif research_event.causal_channel == "earnings":
        research_event.what_to_watch_next = "Watch guidance, revisions and peer read-through."
    elif research_event.causal_channel == "geopolitical":
        research_event.what_to_watch_next = "Watch oil, VIX, gold and FX confirmation."
    else:
        research_event.what_to_watch_next = "Watch for follow-up sources or price confirmation."


def _event_key(event: ResearchEvent) -> str:
    if event.source_event_id:
        return event.source_event_id
    return event.compute_hash()

