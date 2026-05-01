"""Optional FinBERT sentiment scoring with lazy loading and safe fallback."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.logger import get_logger
from app.settings import get_settings

logger = get_logger("sentiment")


@dataclass
class SentimentResult:
    score: float  # -1.0 to 1.0
    label: str


class FinBERTScorer:
    """Sentiment scorer backed by ProsusAI/finbert via transformers pipeline."""

    def __init__(self) -> None:
        self._pipeline = None
        self._load_failed = False

    def score_text(self, text: str) -> SentimentResult:
        text = (text or "").strip()
        if not text:
            return SentimentResult(score=0.0, label="neutral")

        if not self._ensure_pipeline():
            return SentimentResult(score=0.0, label="neutral")

        try:
            result = self._pipeline(text[:512])[0]
            label = str(result.get("label", "neutral")).lower()
            confidence = float(result.get("score", 0.0))
        except Exception as exc:
            logger.warning("FinBERT inference failed; defaulting to neutral: %s", exc)
            return SentimentResult(score=0.0, label="neutral")

        if "positive" in label:
            return SentimentResult(score=min(1.0, confidence), label="positive")
        if "negative" in label:
            return SentimentResult(score=max(-1.0, -confidence), label="negative")
        return SentimentResult(score=0.0, label="neutral")

    def _ensure_pipeline(self) -> bool:
        if self._pipeline is not None:
            return True
        if self._load_failed:
            return False

        try:
            from transformers import pipeline  # type: ignore

            self._pipeline = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
            )
            return True
        except Exception as exc:
            self._load_failed = True
            logger.warning("FinBERT unavailable; sentiment disabled: %s", exc)
            return False


@lru_cache(maxsize=1)
def _get_scorer() -> FinBERTScorer:
    return FinBERTScorer()


def compute_sentiment(text: str) -> SentimentResult:
    settings = get_settings()
    if not settings.enable_finbert:
        return SentimentResult(score=0.0, label="disabled")
    return _get_scorer().score_text(text)
