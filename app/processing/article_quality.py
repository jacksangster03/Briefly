"""Article-type and source-quality classification for editorial gating.

Cheap regex/string-match heuristics that distinguish hard news from
preview/listicle/SEO content, and bucket sources into reliability tiers.
The deterministic pipeline uses these to gate Top Themes and Sector Scan
so low-signal SEO content does not reach the briefing unless the symbol
is portfolio/watchlist relevant or carries a hard catalyst.
"""

from __future__ import annotations

import re

from app.schemas.events import NormalisedEvent

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

# Institutional taxonomy used by deterministic section routing + hygiene.
# Tier 1: top wire / official primary sources.
# Tier 2: recognised market outlets.
# Tier 3: aggregators/opinion-heavy finance outlets.
# Tier 4: SEO/listicle/low-signal or unknown quality.
TIER1_EXTRA_NAMES = (
    "ft",
    "wsj",
    "central bank",
    "federal reserve",
    "ecb",
    "bank of england",
    "bank of japan",
    "u.s. treasury",
    "sec",
    "investor relations",
)

TIER2_NAMES = (
    "cnbc",
    "marketwatch",
    "barron's",
    "barrons",
    "investing.com",
    "economist",
    "nikkei",
    "financial post",
)

TIER3_NAMES = (
    "seeking alpha",
    "seekingalpha",
    "motley fool",
    "fool.com",
    "benzinga",
    "zacks",
    "yahoo finance",
    "investorplace",
    "thestreet",
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


def classify_source_tier(source: str, source_name: str = "", url: str = "") -> tuple[int, str]:
    """Return institutional source-tier tuple: (tier_number, label)."""
    src = (source or "").lower().strip()
    source_quality = classify_source_quality(source, source_name, url)
    if src in {"sec_edgar", "fred", "bls", "bea"}:
        return 1, "tier1"
    haystack = f"{(source_name or '').lower()} {(url or '').lower()}"
    if source_quality in {"tier1_wire", "tier1_press", "sec_filing"}:
        return 1, "tier1"
    if any(needle in haystack for needle in TIER1_EXTRA_NAMES):
        return 1, "tier1"
    if any(needle in haystack for needle in TIER2_NAMES):
        return 2, "tier2"
    if any(needle in haystack for needle in TIER3_NAMES):
        return 3, "tier3"
    if source_quality == "blog":
        return 3, "tier3"
    return 4, "tier4"


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
    re.compile(r"\bbest\s+cd\s+rates?\b", re.I),
    re.compile(r"\block\s+in\s+up\s+to\s+\d+(?:\.\d+)?%\s*apy\b", re.I),
    re.compile(r"\bhigh[-\s]?yield\s+savings\b", re.I),
    re.compile(r"\bchecking\s+account\s+bonus\b", re.I),
    re.compile(r"\bpersonal\s+finance\b", re.I),
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


_CLICKBAIT_TERMS = (
    "just announced",
    "is a buy",
    "room to run",
    "is a mess",
    "fantastic news",
    "turbocharge",
    "thrown under the bus",
    "should you buy",
    "stock to watch",
    "stocks to watch",
    # Opinion/valuation-commentary patterns (TODO: replace with ML classifier)
    "hiding in plain sight",
    "at a discount",
    "hidden gem",
    "no-brainer",
    "screaming buy",
    "must-own",
    "time to buy",
    "time to sell",
    "worth buying",
    "bargain",
    "undervalued gem",
    "too cheap to ignore",
)

_HARD_CATALYST_TERMS = (
    "earnings",
    "guidance",
    "merger",
    "acquisition",
    "ipo",
    "regulatory",
    "central bank",
    "fed",
    "ecb",
    "boj",
    "boe",
    "macro data",
    "cpi",
    "ppi",
    "payroll",
    "tariff",
    "sanction",
    "geopolitical",
    "lawsuit",
    "filing",
    "8-k",
    "10-q",
    "10-k",
)

_GLOBAL_MACRO_TERMS = (
    "macro",
    "geopolit",
    "central bank",
    "fed",
    "ecb",
    "boj",
    "boe",
    "rates",
    "yield",
    "treasury",
    "oil",
    "crude",
    "brent",
    "wti",
    "fx",
    "usd",
    "dollar",
    "tariff",
    "sanction",
    "sovereign",
    "fiscal",
    "monetary",
)

_SECTOR_SIGNAL_TERMS = (
    "sector",
    "industry",
    "etf",
    "commodity",
    "regulation",
    "guidance",
    "earnings",
    "supply chain",
)


def is_clickbait_headline(title: str) -> bool:
    text = (title or "").lower()
    if any(term in text for term in _CLICKBAIT_TERMS):
        return True
    return classify_article_type(title) in {"seo", "listicle", "preview", "opinion"}


def has_hard_catalyst(event_type: str, title: str, summary: str = "") -> bool:
    text = f"{title} {summary}".lower()
    if event_type in {
        "earnings",
        "guidance",
        "m_and_a",
        "macro_release",
        "fed_decision",
        "geopolitical",
        "regulatory",
        "filing",
        "current_report",
        "annual_report",
        "quarterly_report",
    }:
        return True
    return any(term in text for term in _HARD_CATALYST_TERMS)


def classify_section_fit(event: NormalisedEvent) -> str:
    """Classify event fit for strict morning section routing."""
    text = f"{event.title} {event.summary}".lower()
    macro_hits = sum(1 for term in _GLOBAL_MACRO_TERMS if term in text)
    hard_company_event = event.event_type in {
        "company_news",
        "earnings",
        "guidance",
        "m_and_a",
        "ipo",
        "filing",
        "current_report",
        "quarterly_report",
        "annual_report",
        "insider_trade",
    }

    if event.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory"}:
        return "global_macro_geo"
    if macro_hits >= 2 and not hard_company_event:
        return "global_macro_geo"
    if event.event_type in {"ipo", "m_and_a"} and macro_hits < 3:
        # Corporate deal flow belongs to portfolio/watchlist unless strongly macro-coded.
        return "portfolio_watchlist" if event.tickers else "other"
    if event.sectors and (event.event_type in {"earnings", "guidance", "regulatory"} or any(term in text for term in _SECTOR_SIGNAL_TERMS)):
        return "sector_signals"
    if event.tickers:
        return "portfolio_watchlist"
    return "other"


def event_company_confidence(event: NormalisedEvent) -> float:
    raw = event.raw_data or {}
    for key in ("symbol_confidence", "ticker_confidence", "resolution_confidence"):
        val = raw.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    # If the resolver did not emit an explicit confidence, treat text-match
    # gating as the primary control and avoid hiding otherwise valid labels.
    return 0.85


def neutralize_headline(title: str, *, event_type: str = "", ticker: str = "") -> str:
    """Rewrite low-signal/clickbait headlines into neutral catalyst wording."""
    raw = (title or "").strip()
    if not raw:
        return raw
    if not is_clickbait_headline(raw):
        return raw
    symbol = (ticker or "").upper().strip()
    if has_hard_catalyst(event_type, raw):
        subject = symbol or "Company"
        return f"{subject}: reported catalyst headline; awaiting verified operating details."
    return f"{symbol + ': ' if symbol else ''}Low-signal commentary; no primary market catalyst identified."


_LOW_QUALITY_TYPES = {"preview", "listicle", "seo", "opinion"}
_TRUSTED_TIERS = {"tier1_wire", "tier1_press", "sec_filing"}


def is_low_quality_for_section(article_type: str, source_quality: str) -> bool:
    """True when (low-quality type) AND (untrusted source).

    Used as a hard block in Top Themes and Sector Scan, but only when
    the event has no portfolio/watchlist relevance and no hard catalyst
    (those overrides are applied by the caller).
    """
    return article_type in _LOW_QUALITY_TYPES and source_quality not in _TRUSTED_TIERS
