"""Text cleaning and normalisation utilities for event processing."""

from __future__ import annotations

import re
import unicodedata

_SENTENCE_END_RE = re.compile(r'[.!?](?:\s|$)')

_KNOWN_SOURCES = {
    "reuters", "bloomberg", "cnbc", "yahoo finance", "marketwatch",
    "benzinga", "seeking alpha", "the wall street journal", "wsj",
    "financial times", "ft", "ap news", "associated press",
    "bbc", "bbc news", "cnn", "cnn business", "fox business",
    "barron's", "barrons", "motley fool", "the motley fool",
    "investorplace", "zacks", "tipranks", "cbs news", "nbc news",
    "abc news", "the guardian", "npr", "axios", "business insider",
    "the verge", "techcrunch", "investopedia",
}


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
    """Truncate text preferring a clean sentence boundary.

    Falls back to word boundary, then hard cut.
    """
    if len(text) <= max_len:
        return text

    candidate = text[:max_len]

    # Find the last sentence boundary within the limit
    matches = list(_SENTENCE_END_RE.finditer(candidate))
    if matches:
        cut = matches[-1].start() + 1  # include the punctuation
        if cut > max_len // 3:
            return candidate[:cut].rstrip()

    # Fall back to word boundary
    space_idx = candidate.rfind(" ")
    if space_idx > max_len // 3:
        return candidate[:space_idx].rstrip() + suffix

    return candidate.rstrip() + suffix


def strip_title_suffix(title: str) -> str:
    """Strip trailing source attribution like ' - Reuters' from headlines."""
    for sep in (" - ", " | ", " \u2014 ", " \u2013 "):
        idx = title.rfind(sep)
        if idx <= 0:
            continue
        after = title[idx + len(sep):].strip().lower()
        if after in _KNOWN_SOURCES:
            return title[:idx].rstrip()
        if any(after.startswith(s) for s in _KNOWN_SOURCES):
            return title[:idx].rstrip()
        # .gov / .com / .org parenthetical (e.g., "FDIC (.gov)")
        if re.search(r"\.(gov|com|org)\)", after):
            return title[:idx].rstrip()
    return title


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)
