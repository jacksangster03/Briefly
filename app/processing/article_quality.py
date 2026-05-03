"""Article-type and source-quality classification for editorial gating.

Cheap regex/string-match heuristics that distinguish hard news from
preview/listicle/SEO content, and bucket sources into reliability tiers.
The deterministic pipeline uses these to gate Top Themes and Sector Scan
so low-signal SEO content does not reach the briefing unless the symbol
is portfolio/watchlist relevant or carries a hard catalyst.
"""

from __future__ import annotations

import re

# ----- Source quality buckets ------------------------------------------------

TIER1_WIRE_NAMES = (
    "reuters",
    "bloomberg",
    "associated press",
    "ap news",
    "dow jones",
    "marketwatch",
    "agence france-presse",
)

TIER1_PRESS_NAMES = (
    "financial times",
    "ft.com",
    "wall street journal",
    "wsj.com",
    "new york times",
    "nytimes.com",
    "the economist",
    "economist.com",
    "barron's",
    "barrons",
    "barrons.com",
    "cnbc",
    "axios",
    "bbc",
    "le monde",
    "handelsblatt",
)

BLOG_LIKE_NAMES = (
    "motley fool",
    "fool.com",
    "investorplace",
    "benzinga",
    "zacks",
    "thestreet",
    "seekingalpha",
    "seeking alpha",
    "247wallst",
    "247 wall st",
    "stocktwits",
)


def classify_source_quality(source: str, source_name: str = "", url: str = "") -> str:
    """Return source tier: tier1_wire | tier1_press | sec_filing | tier2 | blog."""
    src = (source or "").lower().strip()
    if src == "sec_edgar":
        return "sec_filing"
    haystack = f"{(source_name or '').lower()} {(url or '').lower()}"
    if any(needle in haystack for needle in TIER1_WIRE_NAMES):
        return "tier1_wire"
    if any(needle in haystack for needle in TIER1_PRESS_NAMES):
        return "tier1_press"
    if any(needle in haystack for needle in BLOG_LIKE_NAMES):
        return "blog"
    return "tier2"


# ----- Article type classification -------------------------------------------

_LISTICLE_REGEXES = [
    re.compile(
        r"\b(?:\d+|three|five|seven|ten|twelve)\s+"
        r"(?:reasons?|stocks?|things?|ways?|picks?|charts?|buys?|sells?)\b",
        re.I,
    ),
    re.compile(r"\btop\s+\d+\s+(?:\w+\s+)?(?:stocks?|picks?|companies|shares?|etfs?|funds?)\b", re.I),
    re.compile(r"\bbest\s+\d+\s+(?:\w+\s+)?(?:stocks?|etfs?|funds?|picks?)\b", re.I),
]

_PREVIEW_REGEXES = [
    re.compile(
        r"\b(?:will|could|should|can|might|would)\b.{0,40}"
        r"\b(?:rise|fall|crash|jump|surge|tumble|hit|reach|go\s+(?:up|down)|beat|stock|shares?)\b",
        re.I,
    ),
    re.compile(r"^should\s+you\s+(?:buy|sell|own|hold|consider)\b", re.I),
    re.compile(r"\bis\s+\w+\s+(?:a\s+)?(?:buy|sell|hold)\b", re.I),
    re.compile(r"\bhere(?:'s|\s+is)\s+why\b", re.I),
    re.compile(r"\bwhat\s+(?:to\s+)?(?:expect|watch)\b", re.I),
    re.compile(r"\bthis\s+week\s+on\s+wall\s+street\b", re.I),
    re.compile(r"\bhistoric\s+rally\b", re.I),
]

_SEO_REGEXES = [
    re.compile(r"\b(?:everything|all)\s+you\s+need\s+to\s+know\b", re.I),
    re.compile(r"\b\d+\s+things?\s+(?:you|investors)\b", re.I),
    re.compile(r"\bhow\s+to\s+(?:invest|trade|buy|sell|profit)\b", re.I),
    re.compile(r"\bvs\.?\s+\w+:\s+which\s+is\s+better\b", re.I),
    re.compile(r"\bis\s+.+\s+still\s+the\s+best\s+.+\s+to\s+buy\b", re.I),
    re.compile(r"\bbet\s+on\s+these\b", re.I),
]

_OPINION_REGEXES = [
    re.compile(r"\b(?:opinion|commentary|column|editorial)\b", re.I),
    re.compile(r"\bmy\s+(?:take|view|case)\b", re.I),
]

_OPINION_URL_PATHS = ("/opinion/", "/commentary/", "/column/", "/editorial/")


def classify_article_type(title: str, summary: str = "", url: str = "") -> str:
    """Return article type: hard_news | preview | listicle | seo | opinion."""
    text = (title or "").strip()
    if not text:
        return "hard_news"

    for rx in _LISTICLE_REGEXES:
        if rx.search(text):
            return "listicle"
    for rx in _SEO_REGEXES:
        if rx.search(text):
            return "seo"
    for rx in _PREVIEW_REGEXES:
        if rx.search(text):
            return "preview"
    for rx in _OPINION_REGEXES:
        if rx.search(text) or rx.search(summary or ""):
            return "opinion"

    u = (url or "").lower()
    if any(p in u for p in _OPINION_URL_PATHS):
        return "opinion"

    return "hard_news"


_LOW_QUALITY_TYPES = {"preview", "listicle", "seo", "opinion"}
_TRUSTED_TIERS = {"tier1_wire", "tier1_press", "sec_filing"}


def is_low_quality_for_section(article_type: str, source_quality: str) -> bool:
    """True when (low-quality type) AND (untrusted source).

    Used as a hard block in Top Themes and Sector Scan, but only when
    the event has no portfolio/watchlist relevance and no hard catalyst
    (those overrides are applied by the caller).
    """
    return article_type in _LOW_QUALITY_TYPES and source_quality not in _TRUSTED_TIERS
