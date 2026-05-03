"""FinBERT batch scoring utilities.

The core per-event scorer already lives in app/processing/sentiment.py and is
wired into the relevance scoring pipeline. This module adds:
  - score_batch(): efficient batch inference (reduces pipeline overhead ~10x vs N single calls)
  - enrich_events_sentiment(): bulk re-score a list of NormalisedEvent-like objects

Use enrich_events_sentiment() when you want to re-score a batch after the main
pipeline has run (e.g. chart selection, LLM renderer context building).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("finbert_scorer")

_LABEL_MAP = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
_MAX_BATCH = 32


def _pipeline():
    """Return the shared FinBERT pipeline from processing.sentiment (cached)."""
    try:
        from app.processing.sentiment import _get_scorer  # type: ignore
        scorer = _get_scorer()
        if scorer._ensure_pipeline():
            return scorer._pipeline
    except Exception:
        pass
    return None


def score_batch(texts: list[str]) -> list[float]:
    """Score a list of headlines in one pass. Returns scores in [-1, +1]."""
    if not texts:
        return []
    pipe = _pipeline()
    if pipe is None:
        return [0.0] * len(texts)

    scores: list[float] = []
    for i in range(0, len(texts), _MAX_BATCH):
        chunk = [t[:512] for t in texts[i: i + _MAX_BATCH]]
        try:
            results = pipe(chunk)
            for result in results:
                top = result if isinstance(result, dict) else result[0]
                label = str(top.get("label", "neutral")).lower()
                confidence = float(top.get("score", 0.5))
                polarity = _LABEL_MAP.get(label, 0.0)
                scores.append(round(polarity * confidence, 4))
        except Exception as exc:
            logger.debug("FinBERT batch error at chunk %d: %s", i, exc)
            scores.extend([0.0] * len(chunk))
    return scores


def enrich_events_sentiment(events: list[Any]) -> list[Any]:
    """Batch re-score event.sentiment using FinBERT.

    Only overwrites events whose current sentiment is 0.0 (unscored / disabled)
    to avoid overwriting scores already set by the pipeline.
    """
    unscored_indices = [
        i for i, e in enumerate(events)
        if float(getattr(e, "sentiment", 0.0) or 0.0) == 0.0
    ]
    if not unscored_indices:
        return events

    texts = [str(getattr(events[i], "title", "") or "") for i in unscored_indices]
    new_scores = score_batch(texts)

    for idx, score in zip(unscored_indices, new_scores):
        if score == 0.0:
            continue
        event = events[idx]
        try:
            object.__setattr__(event, "sentiment", score)
        except (AttributeError, TypeError):
            try:
                event.sentiment = score
            except Exception:
                pass
    return events
