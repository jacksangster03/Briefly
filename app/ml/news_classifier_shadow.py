"""Audit-only ML shadow classifier interface (Phase 1 foundation)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.schemas.events import NormalisedEvent
from app.settings import Settings


@dataclass
class MLNewsClassifierResult:
    story_type: str = ""
    suppression_reason: str = ""
    breaking_eligible: bool = False
    confidence: float = 0.0
    model_name: str = ""
    model_version: str = ""
    unavailable_reason: str = ""


def ml_news_classifier_enabled(settings: Settings) -> bool:
    return bool(settings.enable_ml_news_classifier and settings.ml_news_classifier_shadow_mode)


def classify_news_event_shadow(event: NormalisedEvent, settings: Settings) -> MLNewsClassifierResult | None:
    """Phase 1: no real ML model yet; returns graceful skip unless model path exists.

    This function must never raise and must never influence deterministic output.
    """
    if not ml_news_classifier_enabled(settings):
        return None
    model_path = str(settings.ml_news_classifier_model_path or "").strip()
    if not model_path:
        return MLNewsClassifierResult(unavailable_reason="missing_model_path")
    if not Path(model_path).exists():
        return MLNewsClassifierResult(unavailable_reason="model_path_not_found")

    # Phase 1 placeholder: deterministic mirror with explicit shadow flag.
    raw = event.raw_data or {}
    return MLNewsClassifierResult(
        story_type=str(raw.get("news_story_type", "")),
        suppression_reason=str(raw.get("news_suppress_reason", "")),
        breaking_eligible=bool(raw.get("news_breaking_eligible", False)),
        confidence=float(raw.get("news_classifier_confidence", 0.0) or 0.0),
        model_name="local_ml_placeholder",
        model_version="phase1",
        unavailable_reason="",
    )


def compare_deterministic_vs_ml(event: NormalisedEvent, ml_result: MLNewsClassifierResult | None) -> dict:
    raw = event.raw_data or {}
    det_story = str(raw.get("news_story_type", ""))
    det_suppress = str(raw.get("news_suppress_reason", ""))
    det_break = bool(raw.get("news_breaking_eligible", False))

    if ml_result is None or ml_result.unavailable_reason:
        return {
            "agreement": None,
            "disagreement_reason": ml_result.unavailable_reason if ml_result else "ml_disabled",
            "deterministic_story_type": det_story,
            "ml_story_type": "",
            "deterministic_suppression_reason": det_suppress,
            "ml_suppression_reason": "",
            "deterministic_breaking_eligible": det_break,
            "ml_breaking_eligible": None,
            "ml_confidence": 0.0,
            "model_name": ml_result.model_name if ml_result else "",
            "model_version": ml_result.model_version if ml_result else "",
        }

    agreement = (
        det_story == ml_result.story_type
        and det_suppress == ml_result.suppression_reason
        and det_break == ml_result.breaking_eligible
    )
    reason = ""
    if not agreement:
        parts = []
        if det_story != ml_result.story_type:
            parts.append("story_type")
        if det_suppress != ml_result.suppression_reason:
            parts.append("suppression_reason")
        if det_break != ml_result.breaking_eligible:
            parts.append("breaking_eligible")
        reason = ",".join(parts)
    return {
        "agreement": agreement,
        "disagreement_reason": reason,
        "deterministic_story_type": det_story,
        "ml_story_type": ml_result.story_type,
        "deterministic_suppression_reason": det_suppress,
        "ml_suppression_reason": ml_result.suppression_reason,
        "deterministic_breaking_eligible": det_break,
        "ml_breaking_eligible": ml_result.breaking_eligible,
        "ml_confidence": float(ml_result.confidence or 0.0),
        "model_name": ml_result.model_name,
        "model_version": ml_result.model_version,
    }
