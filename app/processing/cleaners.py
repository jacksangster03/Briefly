"""Text cleaning and normalisation utilities for event processing."""

from __future__ import annotations

import re
import unicodedata


def clean_headline(text: str) -> str:
    """Normalise a headline for display and comparison."""
    if not text:
        return ""
    # Normalise unicode
    text = unicodedata.normalize("NFKD", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove control characters
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    return text


def normalise_for_comparison(text: str) -> str:
    """Reduce a headline to its canonical form for dedup matching.

    Lowercases, strips punctuation, collapses whitespace.
    """
    text = clean_headline(text).lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, max_len: int = 300, suffix: str = "...") -> str:
    """Truncate text to max_len characters, adding suffix if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


def extract_tickers_from_text(text: str) -> list[str]:
    """Best-effort extraction of stock tickers from free text.

    Looks for $TICKER patterns and standalone uppercase 1-5 letter words
    that look like tickers. Not perfect, but useful as a supplement.
    """
    # Explicit $TICKER pattern
    dollar_tickers = re.findall(r"\$([A-Z]{1,5})\b", text)

    # Words that look like tickers (uppercase, 1-5 chars, not common words)
    COMMON_WORDS = {
        "A", "I", "AI", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE",
        "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "OUR",
        "SO", "TO", "UP", "US", "WE", "CEO", "CFO", "CTO", "FDA", "SEC", "IPO",
        "GDP", "CPI", "FED", "ECB", "BOJ", "NYSE", "ETF", "EPS", "YOY", "MOM",
        "QOQ", "BPS", "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
        "CAN", "HAS", "HER", "WAS", "ONE", "HOW", "OUT", "NEW", "NOW", "OLD",
        "SEE", "WAY", "MAY", "SAY", "SHE", "TWO", "DAY",
    }
    word_tickers = re.findall(r"\b([A-Z]{1,5})\b", text)
    word_tickers = [t for t in word_tickers if t not in COMMON_WORDS]

    # Combine and deduplicate
    seen = set()
    result = []
    for t in dollar_tickers + word_tickers:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result
