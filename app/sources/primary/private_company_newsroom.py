"""Official company newsroom monitor for IPO and private-company announcements.

Monitors only allowlisted URLs from configs/private_companies.yaml. Never
scrapes arbitrary websites or search engines.

Features:
  - robots.txt check before every domain (cached per process run)
  - Content fingerprinting: skip unchanged pages
  - Poll-rate limiting: minimum 4 hours between polls per URL (via ipo_store)
  - Keyword detection for IPO/funding announcements
  - Bounded text extraction (no full articles stored)
  - Source-authenticity scoring based on domain allowlist
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.schemas.ipo_event import IpoEvent, IpoStatus
from app.sources.primary import ipo_store

logger = get_logger("primary.newsroom")

_RATE_LIMIT_S = 2.0          # between HTTP requests
_MAX_EXCERPT_CHARS = 800     # bounded text extraction
_ROBOTS_CACHE: dict[str, bool] = {}  # netloc -> allowed (cached per process)

# Keywords that indicate an IPO-relevant announcement.
_IPO_KEYWORDS = frozenset({
    "confidentially submitted",
    "draft registration statement",
    "proposed initial public offering",
    "form s-1",
    "form f-1",
    "form s-11",
    "going public",
    "public offering",
    "initial public offering",
    "ipo",
    "listing",
    "registration statement",
    "exchange listing",
    "stock exchange",
    "intends to list",
    "plans to list",
})

# Keywords that indicate a private funding event.
_FUNDING_KEYWORDS = frozenset({
    "funding round",
    "series a",
    "series b",
    "series c",
    "series d",
    "series e",
    "series f",
    "series g",
    "series h",
    "series i",
    "series j",
    "raises",
    "raised",
    "investment",
    "tender offer",
    "secondary offering",
    "valuation",
    "billion",
    "million in funding",
})


class PrivateCompanyNewsroomProvider(BaseProvider):
    """Monitors official company newsrooms for IPO and funding announcements."""

    name = "company_newsroom"

    def __init__(self, user_agent: str, registry: dict, **kwargs):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self.registry = registry
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_req: float = 0.0
        # Build allowlist: set of official domains and URLs
        self._allowed_domains: set[str] = set()
        self._allowed_urls: dict[str, str] = {}  # url -> company_id
        for cid, cfg in registry.items():
            for domain in cfg.get("official_domains", []):
                self._allowed_domains.add(domain.lower().lstrip("www."))
            for url in cfg.get("newsroom_urls", []):
                self._allowed_urls[url] = cid

    def is_configured(self) -> bool:
        return bool(self.user_agent and self._allowed_urls)

    # -- Public API -----------------------------------------------------------

    def fetch_announcements(self) -> list[NormalisedEvent]:
        """Check all configured newsroom URLs and return new announcements."""
        events: list[NormalisedEvent] = []
        for url, company_id in self._allowed_urls.items():
            if not self._is_url_allowed(url):
                logger.debug("newsroom: %s not in allowlist or blocked by robots.txt", url)
                continue
            if not ipo_store.is_poll_due(company_id, url):
                logger.debug("newsroom: poll not yet due for %s", url)
                continue
            page_events = self._check_newsroom(url, company_id)
            events.extend(page_events)
            ipo_store.record_poll(company_id, url)
        logger.info("newsroom: %d announcement events from %d URLs", len(events), len(self._allowed_urls))
        return events

    # -- Internal helpers -----------------------------------------------------

    def _check_newsroom(self, url: str, company_id: str) -> list[NormalisedEvent]:
        """Fetch a newsroom page and return events if content changed."""
        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("newsroom: fetch failed %s: %s", url, exc)
            return []

        html = resp.text
        fingerprint = _content_fingerprint(html)

        if not ipo_store.is_content_new(company_id, url, fingerprint):
            logger.debug("newsroom: no content change for %s", url)
            return []

        ipo_store.record_content(company_id, url, fingerprint)
        return self._parse_announcements(html, url, company_id)

    def _parse_announcements(
        self, html: str, url: str, company_id: str
    ) -> list[NormalisedEvent]:
        """Parse HTML for IPO/funding announcements."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()

        cfg = self.registry.get(company_id, {})
        canonical_name = cfg.get("canonical_name", company_id)

        events: list[NormalisedEvent] = []
        # Check headlines and article links for relevant keywords
        candidates = []
        for el in soup.find_all(["h1", "h2", "h3", "a", "article", "li"]):
            text = el.get_text(separator=" ").strip()
            if text and len(text) > 20:
                candidates.append((text, el))

        for text, el in candidates:
            text_lower = text.lower()
            if _contains_ipo_keywords(text_lower):
                category = "ipo"
                event_type = "central_bank_statement"  # placeholder — overridden below
                event_type = "ipo_filing"
                factual_confidence = 0.90  # official domain, but may be announcement only
                status: IpoStatus = "confidential_filing"
                if any(kw in text_lower for kw in ("form s-1", "registration statement", "initial public offering")):
                    if "confidential" in text_lower or "draft" in text_lower:
                        status = "confidential_filing"
                    else:
                        status = "public_filing"
            elif _contains_funding_keywords(text_lower):
                category = "funding"
                event_type = "private_funding"
                factual_confidence = 0.80
                status = cfg.get("status", "private")
            else:
                continue

            # Extract a bounded excerpt
            excerpt = _extract_excerpt(soup, text, max_chars=_MAX_EXCERPT_CHARS)
            published_at = _extract_date(soup, el)

            ipo_meta = IpoEvent(
                private_company_id=company_id,
                canonical_company_name=canonical_name,
                ipo_status=status,
                confidential_filing_announced=(
                    category == "ipo"
                    and "confidential" in text.lower()
                ),
                confidential_filing_announced_at=published_at,
                official_source_urls=[url],
                status_confidence="medium",  # requires cross-check with EDGAR
                last_material_update_at=published_at,
            )

            title = _clean_title(text)
            evt = NormalisedEvent(
                source="company_newsroom",
                source_type="announcement",
                published_at=published_at,
                title=f"{canonical_name}: {title[:120]}",
                summary=excerpt,
                url=url,
                tickers=[],
                event_type=event_type,
                factual_confidence_score=factual_confidence,
                importance_score=0.88 if category == "ipo" else 0.75,
                raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
            )
            evt.compute_hash()
            events.append(evt)
            logger.info("newsroom: %s announcement detected for %s", category, company_id)
            break  # one event per page check; re-poll detects follow-up updates

        return events

    def _is_url_allowed(self, url: str) -> bool:
        """Check allowlist and robots.txt before fetching."""
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().lstrip("www.")
        if not any(netloc == d or netloc.endswith("." + d) for d in self._allowed_domains):
            return False
        return _robots_allowed(parsed.netloc, url, self.user_agent)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure module-level helpers ------------------------------------------------

def _content_fingerprint(html: str) -> str:
    """SHA-256 fingerprint of page HTML for change detection."""
    return hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:32]


def _contains_ipo_keywords(text_lower: str) -> bool:
    return any(kw in text_lower for kw in _IPO_KEYWORDS)


def _contains_funding_keywords(text_lower: str) -> bool:
    return any(kw in text_lower for kw in _FUNDING_KEYWORDS)


def _robots_allowed(netloc: str, url: str, user_agent: str) -> bool:
    """Check robots.txt for the given URL. Cached per netloc per process."""
    if netloc in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[netloc]
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        allowed = rp.can_fetch(user_agent, url)
    except Exception:
        allowed = True  # inaccessible robots.txt = assume allowed
    _ROBOTS_CACHE[netloc] = allowed
    return allowed


def _extract_excerpt(soup: BeautifulSoup, headline: str, max_chars: int) -> str:
    """Extract bounded text near the matched headline."""
    body = soup.body or soup
    full_text = " ".join(body.get_text(separator=" ").split())
    idx = full_text.lower().find(headline.lower()[:40])
    if idx >= 0:
        start = max(0, idx - 50)
        snippet = full_text[start: start + max_chars]
    else:
        snippet = full_text[:max_chars]
    return snippet


def _extract_date(soup: BeautifulSoup, context_el) -> datetime | None:
    """Try to extract a publication date from metadata or nearby elements."""
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        if prop in ("article:published_time", "pubdate", "date", "DC.date"):
            val = meta.get("content", "")
            if val:
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    pass
    for time_tag in soup.find_all("time", datetime=True):
        try:
            return datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            pass
    return None


def _clean_title(text: str) -> str:
    """Normalise headline text to a clean title."""
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:160]
