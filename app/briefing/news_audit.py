"""Audit-only aggregation for news classifier diagnostics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.briefing.news_classifier import is_stale_breaking_candidate
from app.schemas.events import NormalisedEvent

STORY_TYPE_ORDER = [
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

FRESHNESS_ORDER = ["new", "updated", "repeated", "stale", "old_context", "late_discovery", "unknown"]
UPDATE_STATUS_ORDER = ["new", "duplicate", "material_update", "repeated", "stale"]
SUPPRESSION_REASON_ORDER = [
    "low_signal",
    "commentary_valuation",
    "generic_market_wrap",
    "stale",
    "old_context",
    "late_discovery",
    "ticker_mismatch",
    "low_confidence_ticker",
    "weak_etf_proxy",
    "no_direct_market_mechanism",
    "duplicate",
    "provider_stale",
    "unknown",
]


@dataclass
class ClassifierAuditDiagnostics:
    story_type_counts: dict[str, int]
    freshness_state_counts: dict[str, int]
    update_status_counts: dict[str, int]
    suppression_reason_counts: dict[str, int]
    included_examples: list[dict]
    suppressed_examples: list[dict]
    breaking_rejected_examples: list[dict]


def build_classifier_audit(
    *,
    candidate_events: Iterable[NormalisedEvent],
    included_events: Iterable[NormalisedEvent],
    max_examples: int = 3,
) -> ClassifierAuditDiagnostics:
    candidate_list = list(candidate_events)
    included_list = list(included_events)

    story = Counter()
    fresh = Counter()
    update = Counter()
    suppress = Counter()
    suppressed_examples: list[dict] = []
    rejected_examples: list[dict] = []

    for evt in candidate_list:
        raw = evt.raw_data or {}
        story_type = str(raw.get("news_story_type", "unknown")).strip().lower() or "unknown"
        story[story_type] += 1

        freshness = str(raw.get("news_freshness_state", "unknown")).strip().lower() or "unknown"
        breaking_label = str(raw.get("breaking_label", "")).strip().upper()
        if breaking_label == "LATE DISCOVERY":
            freshness = "late_discovery"
        fresh[freshness] += 1

        status = str(evt.update_status or "unknown").strip().lower() or "unknown"
        if status == "duplicate":
            update["duplicate"] += 1
            update["repeated"] += 1
        else:
            update[status] += 1
        if freshness == "stale":
            update["stale"] += 1

        reason = _map_suppression_reason(
            suppress_reason=str(raw.get("news_suppress_reason", "")).strip(),
            raw=raw,
        )
        if reason:
            suppress[reason] += 1
            if len(suppressed_examples) < max_examples:
                suppressed_examples.append(
                    {
                        "title": evt.title,
                        "suppress_reason": reason,
                        "story_type": story_type,
                        "freshness_state": freshness,
                    }
                )

        blocked, rejection = is_stale_breaking_candidate(evt)
        if blocked and len(rejected_examples) < max_examples:
            published = _as_utc(evt.published_at)
            first_seen = _as_utc(_datetime_from_raw(raw.get("first_seen_at")))
            age = ""
            if published:
                age_td = datetime.now(timezone.utc) - published
                age = f"{int(age_td.total_seconds() // 3600)}h"
            rejected_examples.append(
                {
                    "title": evt.title,
                    "rejection_reason": rejection or reason or "unknown",
                    "published_time": published.isoformat() if published else "unknown",
                    "first_seen_time": first_seen.isoformat() if first_seen else "unknown",
                    "age": age or "unknown",
                }
            )

    included_examples: list[dict] = []
    for evt in included_list[:max_examples]:
        raw = evt.raw_data or {}
        included_examples.append(
            {
                "title": evt.title,
                "story_type": str(raw.get("news_story_type", "unknown")),
                "freshness_state": str(raw.get("news_freshness_state", "unknown")),
                "score": round(float(evt.final_score or 0.0), 3),
                "confidence": round(float(raw.get("news_classifier_confidence", evt.factual_confidence_score or 0.0)), 3),
            }
        )

    return ClassifierAuditDiagnostics(
        story_type_counts=_ordered_counts(story, STORY_TYPE_ORDER),
        freshness_state_counts=_ordered_counts(fresh, FRESHNESS_ORDER),
        update_status_counts=_ordered_counts(update, UPDATE_STATUS_ORDER),
        suppression_reason_counts=_ordered_counts(suppress, SUPPRESSION_REASON_ORDER),
        included_examples=included_examples,
        suppressed_examples=suppressed_examples,
        breaking_rejected_examples=rejected_examples,
    )


def format_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k}:{v}" for k, v in counts.items())


def _ordered_counts(counter: Counter, order: list[str]) -> dict[str, int]:
    return {k: int(counter.get(k, 0)) for k in order}


def _map_suppression_reason(*, suppress_reason: str, raw: dict) -> str:
    reason = suppress_reason.lower()
    if reason in {"low_signal_format"}:
        return "low_signal"
    if reason in {"valuation_commentary_without_catalyst"}:
        return "commentary_valuation"
    if reason in {"generic_polling_with_etf_proxy_only"}:
        return "weak_etf_proxy"
    if reason in {"no_hard_catalyst"}:
        return "no_direct_market_mechanism"
    if reason:
        return reason
    if raw.get("ticker_label_suppressed"):
        return "low_confidence_ticker"
    return ""


def _datetime_from_raw(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
