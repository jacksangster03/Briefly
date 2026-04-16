"""Deterministic classification for breaking-alert candidates."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

from app.schemas.briefings import BreakingClassification, BreakingTier
from app.schemas.events import NormalisedEvent

_LOW_SIGNAL_PATTERNS = (
    "stock to buy",
    "stock is a buy",
    "is it a buy",
    "best stock",
    "top stock",
    "picked a winner",
    "grabbing gains",
    "youtuber",
    "celebrity",
    "salary",
    "analysts say",
    "wall street thinks",
    "price target",
    "valuation",
    "pundit",
    "jim cramer",
)

_MARKET_LINK_TERMS = (
    "fed",
    "fomc",
    "ecb",
    "boe",
    "boj",
    "treasury",
    "yield",
    "rates",
    "inflation",
    "cpi",
    "ppi",
    "pce",
    "payroll",
    "jobs",
    "sanction",
    "tariff",
    "export control",
    "blockade",
    "ceasefire",
    "war",
    "conflict",
    "nuclear",
    "iran",
    "israel",
    "russia",
    "ukraine",
    "oil",
    "crude",
    "gas",
    "lng",
    "shipping",
    "tanker",
    "supply chain",
    "earnings",
    "guidance",
    "sec filing",
    "8-k",
    "10-q",
    "10-k",
    "fda",
    "antitrust",
    "merger",
    "acquisition",
)

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "geopolitics": (
        "iran",
        "israel",
        "russia",
        "ukraine",
        "kremlin",
        "ceasefire",
        "blockade",
        "nuclear",
        "sanction",
        "missile",
        "war",
        "conflict",
    ),
    "rates_macro": (
        "fed",
        "fomc",
        "ecb",
        "boe",
        "boj",
        "yellen",
        "powell",
        "treasury",
        "yield",
        "rate cut",
        "rate hike",
        "inflation",
        "cpi",
        "ppi",
        "pce",
        "payroll",
        "jobs",
    ),
    "energy": (
        "oil",
        "crude",
        "lng",
        "gas",
        "opec",
        "pipeline",
        "hormuz",
        "shipping",
        "tanker",
    ),
    "regulation": (
        "fda",
        "antitrust",
        "regulator",
        "probe",
        "lawsuit",
        "export control",
        "tariff",
    ),
    "earnings": (
        "earnings",
        "guidance",
        "eps",
        "revenue",
        "forecast",
        "quarter",
        "8-k",
        "10-q",
        "10-k",
    ),
}

_CATEGORY_WATCH_ASSETS: dict[str, list[str]] = {
    "geopolitics": ["WTI (CL1:COM)", "Gold (GC1:COM)", "VIX (^VIX)", "Energy (XLE)", "S&P 500 (SPY)"],
    "rates_macro": ["US 10Y (^TNX)", "USD Index", "S&P 500 (SPY)", "Nasdaq 100 (QQQ)", "Financials (XLF)"],
    "energy": ["WTI (CL1:COM)", "Energy (XLE)", "Airlines basket", "VIX (^VIX)"],
    "regulation": ["Affected ticker", "Sector ETF", "S&P 500 (SPY)"],
    "earnings": ["Affected ticker", "Sector ETF", "S&P 500 (SPY)"],
    "company": ["Affected ticker", "Sector ETF", "S&P 500 (SPY)"],
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "says",
    "set",
    "says",
    "that",
    "the",
    "to",
    "us",
    "u.s",
    "with",
}

_TICKER_SECTOR_ETF = {
    "technology": "XLK",
    "semiconductors": "SMH",
    "software_internet": "IGV",
    "financials": "XLF",
    "energy": "XLE",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "healthcare": "XLV",
    "pharma_biotech": "XBI",
    "communication_services": "XLC",
    "industrials": "XLI",
    "real_estate": "XLRE",
}


def classify_breaking_event(
    event: NormalisedEvent,
    *,
    now: datetime | None = None,
) -> BreakingClassification:
    """Return deterministic classification metadata for a candidate event."""
    now = now or datetime.now(timezone.utc)
    text = _normalize_text(f"{event.title} {event.summary}")
    title = _normalize_text(event.title)
    category = _infer_category(event, text)
    low_signal = _is_low_signal(text)
    market_linked = _is_market_linked(event, text, category)

    impact = _impact_score(event, category, low_signal)
    confidence = _confidence_score(event)
    novelty = _novelty_score(event)
    immediacy = _immediacy_score(event, now=now)
    breadth = _breadth_score(event, category)

    tier = _decide_tier(
        event=event,
        category=category,
        market_linked=market_linked,
        low_signal=low_signal,
        impact=impact,
        confidence=confidence,
        novelty=novelty,
        immediacy=immediacy,
        breadth=breadth,
    )

    why = _why_markets_care(event, category, low_signal=low_signal, market_linked=market_linked)
    watch_assets = _watch_assets(event, category)
    confirm_signals = _confirm_signals(category, event)
    invalidate_signals = _invalidate_signals(category)
    storyline_key = build_breaking_storyline_key(event, category=category, text_lower=title)

    return BreakingClassification(
        tier=tier,
        category=category,
        impact_score=impact,
        confidence_score=confidence,
        novelty_score=novelty,
        immediacy_score=immediacy,
        breadth_score=breadth,
        why_markets_care=why,
        watch_assets=watch_assets,
        confirm_signals=confirm_signals,
        invalidate_signals=invalidate_signals,
        storyline_key=storyline_key,
    )


def build_breaking_tracking_ids(
    event: NormalisedEvent,
    classification: BreakingClassification,
) -> list[str]:
    ids: list[str] = []
    if classification.storyline_key:
        ids.append(f"storyline:{classification.storyline_key}")
    ids.append(f"breaking_category:{classification.category}")
    ids.append(f"breaking_tier:{classification.tier}")
    if event.cluster_id:
        ids.append(f"cluster:{event.cluster_id}")
    return ids


def build_breaking_storyline_key(
    event: NormalisedEvent,
    *,
    category: str,
    text_lower: str | None = None,
) -> str:
    text = text_lower or _normalize_text(event.title)
    tokens = _storyline_tokens(text=text, category=category, tickers=event.tickers)
    payload = f"{category}|{'|'.join(tokens[:6])}|{','.join(sorted(event.tickers)[:2])}"
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _normalize_text(value: str) -> str:
    return (
        (value or "")
        .lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("—", "-")
        .replace("–", "-")
        .replace("\xa0", " ")
    )


def _is_low_signal(text: str) -> bool:
    if any(pattern in text for pattern in _LOW_SIGNAL_PATTERNS):
        return True
    if re.search(r"^\s*\d+\s+(?:reasons|stocks|ways|things)\s+to\b", text):
        return True
    if re.search(r"\b(?:buy|sell|hold)\b.{0,20}\bstock\b", text):
        return True
    return False


def _is_market_linked(event: NormalisedEvent, text: str, category: str) -> bool:
    if category in {"geopolitics", "rates_macro", "energy"}:
        return True
    if event.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory", "earnings", "guidance"}:
        return True
    if event.tickers and event.sectors:
        return True
    return any(token in text for token in _MARKET_LINK_TERMS)


def _infer_category(event: NormalisedEvent, text: str) -> str:
    event_type = (event.event_type or "").lower()
    if event_type in {"geopolitical", "war_conflict"}:
        return "geopolitics"
    if event_type in {"macro_release", "fed_decision"}:
        return "rates_macro"
    if event_type in {"earnings", "guidance", "filing", "current_report", "annual_report", "quarterly_report"}:
        return "earnings"
    if event_type in {"regulatory", "fda_decision", "legal"}:
        return "regulation"

    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "company"


def _impact_score(event: NormalisedEvent, category: str, low_signal: bool) -> int:
    score = 1
    if event.final_score >= 0.74:
        score += 1
    if event.final_score >= 0.84:
        score += 1
    if event.cluster_size >= 4:
        score += 1
    if category in {"geopolitics", "rates_macro", "energy"}:
        score += 1
    if event.event_type in {"earnings", "guidance", "fed_decision", "macro_release"}:
        score += 1
    if low_signal:
        score -= 2
    return max(1, min(5, score))


def _confidence_score(event: NormalisedEvent) -> int:
    score = 1
    if event.factual_confidence_score >= 0.68:
        score += 1
    if event.factual_confidence_score >= 0.82:
        score += 1
    if event.cluster_size >= 2:
        score += 1
    if event.source in {"sec_edgar", "fred"}:
        score += 1
    return max(1, min(5, score))


def _novelty_score(event: NormalisedEvent) -> int:
    if event.update_status != "new" or event.already_sent:
        return 1
    value = event.novelty_score
    if value >= 0.85:
        return 5
    if value >= 0.65:
        return 4
    if value >= 0.45:
        return 3
    if value >= 0.30:
        return 2
    return 1


def _immediacy_score(event: NormalisedEvent, *, now: datetime) -> int:
    published = event.published_at
    if not published:
        base = 2
    else:
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        else:
            published = published.astimezone(timezone.utc)
        age_minutes = max(0.0, (now.astimezone(timezone.utc) - published).total_seconds() / 60.0)
        if age_minutes <= 30:
            base = 5
        elif age_minutes <= 120:
            base = 4
        elif age_minutes <= 360:
            base = 3
        elif age_minutes <= 720:
            base = 2
        else:
            base = 1
    if event.event_type in {"fed_decision", "macro_release", "geopolitical"}:
        base = min(5, base + 1)
    return base


def _breadth_score(event: NormalisedEvent, category: str) -> int:
    score = 1
    if category in {"geopolitics", "rates_macro", "energy"}:
        score += 2
    if event.cluster_size >= 5:
        score += 1
    if len(event.tickers) >= 2:
        score += 1
    elif len(event.tickers) == 1:
        score += 0
    if event.sectors:
        score += 1
    return max(1, min(5, score))


def _decide_tier(
    *,
    event: NormalisedEvent,
    category: str,
    market_linked: bool,
    low_signal: bool,
    impact: int,
    confidence: int,
    novelty: int,
    immediacy: int,
    breadth: int,
) -> BreakingTier:
    if not market_linked:
        return "ignore"
    if low_signal and category == "company":
        return "ignore"
    if event.update_status != "new":
        return "ignore"

    if (
        impact >= 4
        and confidence >= 3
        and novelty >= 3
        and immediacy >= 3
        and breadth >= 3
        and event.final_score >= 0.80
    ):
        return "breaking"
    if impact >= 3 and confidence >= 2 and novelty >= 2 and event.final_score >= 0.74:
        return "high_priority"
    if impact >= 2 and confidence >= 2 and event.final_score >= 0.65:
        return "regular"
    return "ignore"


def _why_markets_care(
    event: NormalisedEvent,
    category: str,
    *,
    low_signal: bool,
    market_linked: bool,
) -> str:
    if low_signal and category == "company":
        return "Low-signal headline pattern; suppressed from breaking delivery."
    if not market_linked:
        return "No clear rates, inflation, risk, or earnings transmission channel."
    if category == "geopolitics":
        return "Geopolitical risk can rapidly reprice oil, volatility, and cross-asset risk tone."
    if category == "rates_macro":
        return "Rates and inflation expectations can shift equity leadership and discount rates quickly."
    if category == "energy":
        return "Energy shocks can feed inflation expectations, margins, and near-term risk sentiment."
    if category == "regulation":
        return "Regulatory outcomes can alter earnings trajectories and sector valuation assumptions."
    if category == "earnings":
        return "Guidance and earnings revisions can reset near-term expectations for the stock and peers."
    if event.tickers:
        return "Direct company catalyst with potential read-through to sector peers and index breadth."
    return "Cross-asset market catalyst with potential index-level read-through."


def _watch_assets(event: NormalisedEvent, category: str) -> list[str]:
    watch = list(_CATEGORY_WATCH_ASSETS.get(category, _CATEGORY_WATCH_ASSETS["company"]))
    if event.tickers:
        for ticker in event.tickers[:2]:
            watch.insert(0, ticker)
    if event.sectors:
        for sector in event.sectors[:2]:
            etf = _TICKER_SECTOR_ETF.get((sector or "").lower().replace(" ", "_"))
            if etf and etf not in watch:
                watch.append(etf)
    return watch[:6]


def _confirm_signals(category: str, event: NormalisedEvent) -> list[str]:
    if category in {"geopolitics", "energy"}:
        return [
            "WTI crude moves >1% within 15m",
            "VIX trends higher",
            "Energy (XLE) outperforms SPY",
        ]
    if category == "rates_macro":
        return [
            "US 10Y yield moves >=5bp",
            "QQQ/SPY spread widens",
            "USD reprices >0.3%",
        ]
    if event.tickers:
        ticker = event.tickers[0]
        return [
            f"{ticker} moves >1.5% on rising volume",
            "Sector ETF confirms direction",
        ]
    return ["Mapped assets move in expected direction within 10-15m"]


def _invalidate_signals(category: str) -> list[str]:
    if category in {"geopolitics", "energy"}:
        return [
            "Oil and VIX stay flat for 15m",
            "Headline is clarified or de-escalated by officials",
        ]
    if category == "rates_macro":
        return [
            "Treasury yields hold flat despite headline",
            "Risk assets show no rotation after 15m",
        ]
    return [
        "Affected ticker/sector shows no follow-through",
        "No corroborating source updates arrive",
    ]


def _storyline_tokens(*, text: str, category: str, tickers: list[str]) -> list[str]:
    category_tokens = sorted({
        keyword
        for keyword in _CATEGORY_KEYWORDS.get(category, ())
        if keyword in text
    })
    if category_tokens:
        return category_tokens + sorted({ticker.lower() for ticker in tickers[:2]})

    words = re.findall(r"[a-z0-9]+", text)
    filtered = [
        word
        for word in words
        if len(word) >= 4 and word not in _STOPWORDS
    ]
    if tickers:
        filtered.extend(ticker.lower() for ticker in tickers[:2])
    unique: list[str] = []
    for token in filtered:
        if token not in unique:
            unique.append(token)
    return unique[:8]
