"""Source credibility module: trust tiers and confidence scoring.

Implements the distinction between attention (how much buzz something has)
and factual confidence (how likely it is to be true/verified).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("credibility")

# Default trust tiers (overridable via sources.yaml)
DEFAULT_TRUST_TIERS = {
    "highest": 0.95,
    "high": 0.80,
    "medium": 0.55,
    "low": 0.25,
    "experimental": 0.15,
}

# Provider to tier mapping
PROVIDER_TIERS = {
    "sec_edgar": "highest",
    "fred": "high",
    "finnhub": "high",
    "polygon": "high",
    "alpaca": "high",
    "gdelt": "medium",
    "alpha_vantage": "medium",
    "marketaux": "medium",
    "fmp": "medium",
    "mediastack": "medium",
    "newsapi": "medium",
    "yfinance": "medium",
    "x_twitter": "low",
}

# Source name credibility (when available from news metadata)
NEWS_SOURCE_CREDIBILITY = {
    # Wire services and financial press
    "reuters": 0.90,
    "bloomberg": 0.90,
    "associated press": 0.88,
    "wall street journal": 0.88,
    "financial times": 0.88,
    "cnbc": 0.80,
    "barrons": 0.80,
    "marketwatch": 0.75,
    "seeking alpha": 0.55,
    "motley fool": 0.50,
    "benzinga": 0.55,
    "yahoo finance": 0.60,
    "investopedia": 0.60,
    # General press
    "new york times": 0.85,
    "washington post": 0.82,
    "bbc": 0.82,
    "the guardian": 0.78,
    "cnn": 0.70,
    "fox business": 0.65,
}


def apply_credibility_scores(
    events: list[NormalisedEvent],
    sources_config_path: Path | None = None,
) -> list[NormalisedEvent]:
    """Set factual_confidence_score and attention_score on each event."""
    trust_tiers = DEFAULT_TRUST_TIERS

    if sources_config_path and sources_config_path.exists():
        with open(sources_config_path) as f:
            config = yaml.safe_load(f) or {}
        trust_tiers = config.get("trust_tiers", DEFAULT_TRUST_TIERS)

    for evt in events:
        # Base confidence from provider tier
        tier = PROVIDER_TIERS.get(evt.source, "medium")
        base_confidence = trust_tiers.get(tier, 0.50)

        # Adjust by specific news source if available
        source_name = (evt.raw_data.get("source_name", "") or "").lower()
        if source_name and source_name in NEWS_SOURCE_CREDIBILITY:
            base_confidence = max(base_confidence, NEWS_SOURCE_CREDIBILITY[source_name])

        # Filing types get highest confidence
        if evt.source_type == "filing":
            base_confidence = max(base_confidence, 0.90)

        # Macro data from FRED is highly reliable
        if evt.source == "fred":
            base_confidence = max(base_confidence, 0.90)

        evt.factual_confidence_score = base_confidence

        # Attention is intentionally distinct from factual confidence.
        text = f"{evt.title} {evt.summary}".lower()
        attention = 0.30
        if any(word in text for word in ("breaking", "surge", "plunge", "shock", "war", "fed", "fda")):
            attention += 0.20
        if evt.tickers:
            attention += 0.15
        if evt.source == "sec_edgar":
            attention += 0.15
        evt.attention_score = min(1.0, attention)

    logger.debug("Applied credibility scores to %d events", len(events))
    return events
