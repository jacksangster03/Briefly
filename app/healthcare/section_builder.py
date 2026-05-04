"""Healthcare / biotech section assembly."""

from __future__ import annotations

from typing import Any

from app.healthcare.classifier import classify_healthcare_event
from app.healthcare.schemas import HealthcareBriefingItem, HealthcareBriefingSection, HealthcareEvent
from app.healthcare.scorer import score_healthcare_event, severity_rank
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent


def build_healthcare_section(
    *,
    profile: UserProfile,
    session_key: str,
    events: list[NormalisedEvent],
    verbose_when_empty: bool = False,
) -> HealthcareBriefingSection | None:
    prefs = profile.healthcare_preferences
    enabled = bool(prefs.get("enabled", False))
    if not enabled:
        return None

    classified: list[HealthcareEvent] = []
    suppressed = 0
    source_names: set[str] = set()
    for evt in events:
        hevt = classify_healthcare_event(evt, healthcare_prefs=prefs)
        if hevt is None:
            suppressed += 1
            continue
        hevt = score_healthcare_event(hevt, profile=profile, healthcare_prefs=prefs)
        source_names.add(hevt.source or "unknown")
        classified.append(hevt)

    min_severity = _min_severity_for_session(session_key=session_key, prefs=prefs)
    min_rank = severity_rank(min_severity)
    ranked = [evt for evt in classified if severity_rank(evt.severity) >= min_rank]
    ranked.sort(
        key=lambda evt: (severity_rank(evt.severity), evt.relevance_score, evt.published_at.timestamp() if evt.published_at else 0.0),
        reverse=True,
    )

    # Ensure one high-relevance manufacturing/supply-chain line is retained for morning.
    if session_key == "morning":
        if not any(evt.event_type in {"manufacturing_capacity", "api_supply_chain", "shortage"} for evt in ranked):
            supply = next(
                (
                    evt for evt in sorted(classified, key=lambda row: row.relevance_score, reverse=True)
                    if evt.event_type in {"manufacturing_capacity", "api_supply_chain", "shortage"}
                ),
                None,
            )
            if supply is not None:
                ranked.append(supply)

    max_items = int(prefs.get("max_items_morning", 4)) if session_key == "morning" else int(prefs.get("max_items_intraday", 3))
    items = [_to_item(evt) for evt in ranked[:max_items]]

    if not items:
        if not verbose_when_empty:
            return None
        return HealthcareBriefingSection(
            enabled=True,
            title="HEALTHCARE / BIOTECH INTELLIGENCE",
            read="No high-signal healthcare/biotech developments this cycle.",
            items=[],
            confidence="LOW",
            source_count=len(source_names),
            suppressed_count=suppressed,
            unavailable_reason="No high-signal healthcare/biotech developments this cycle.",
        )

    read = _build_read_line(items)
    conf = _confidence_label(items=items, source_count=len(source_names))
    return HealthcareBriefingSection(
        enabled=True,
        title="HEALTHCARE / BIOTECH INTELLIGENCE",
        read=read,
        items=items,
        confidence=conf,
        source_count=len(source_names),
        suppressed_count=suppressed,
    )


def filter_breaking_healthcare_events(
    *,
    profile: UserProfile,
    events: list[NormalisedEvent],
) -> list[HealthcareEvent]:
    prefs = profile.healthcare_preferences
    if not bool(prefs.get("enabled", False)) or not bool(prefs.get("breaking_alerts", False)):
        return []
    min_rank = severity_rank(str(prefs.get("minimum_severity_breaking", "critical")))
    out: list[HealthcareEvent] = []
    for evt in events:
        hevt = classify_healthcare_event(evt, healthcare_prefs=prefs)
        if hevt is None:
            continue
        hevt = score_healthcare_event(hevt, profile=profile, healthcare_prefs=prefs)
        if severity_rank(hevt.severity) >= min_rank:
            out.append(hevt)
    out.sort(key=lambda row: (severity_rank(row.severity), row.relevance_score), reverse=True)
    return out


def _min_severity_for_session(*, session_key: str, prefs: dict[str, Any]) -> str:
    key = (session_key or "morning").lower()
    if key == "morning":
        return str(prefs.get("minimum_severity_morning", "medium")).lower()
    if key in {"us_intraday_risk", "into_close", "europe_midday", "us_pre_open"}:
        return str(prefs.get("minimum_severity_intraday", "high")).lower()
    return "medium"


def _to_item(event: HealthcareEvent) -> HealthcareBriefingItem:
    company_display = ", ".join(event.company_tickers[:3]) if event.company_tickers else ""
    asset_display = ", ".join(event.asset_names[:3]) if event.asset_names else ""
    source_line = event.source
    if event.regulator:
        source_line = f"{event.regulator} · {event.source}"
    theme_tags = list(dict.fromkeys(event.modality + event.therapy_areas))
    return HealthcareBriefingItem(
        title=event.title,
        summary=event.summary,
        event_type=event.event_type,
        severity=event.severity,
        company_display=company_display,
        asset_display=asset_display,
        theme_tags=theme_tags[:5],
        market_relevance=event.market_relevance,
        portfolio_lens=event.portfolio_lens,
        source_line=source_line,
        published_at=event.published_at,
    )


def _build_read_line(items: list[HealthcareBriefingItem]) -> str:
    tags = {tag for item in items for tag in item.theme_tags}
    if not tags:
        return "High-signal healthcare items are focused on regulatory, clinical, and manufacturing catalysts."
    top_tags = ", ".join(sorted(tags)[:4])
    return f"High-signal healthcare items are focused on {top_tags} catalysts."


def _confidence_label(*, items: list[HealthcareBriefingItem], source_count: int) -> str:
    critical = any(item.severity == "critical" for item in items)
    if critical and source_count >= 2:
        return "HIGH"
    if source_count >= 1:
        return "MEDIUM"
    return "LOW"

