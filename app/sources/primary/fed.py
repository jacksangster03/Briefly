"""Federal Reserve primary-source provider.

Fetches monetary policy press releases directly from federalreserve.gov via the
official RSS feed, then retrieves statement text from individual press release pages.

No API key required. A User-Agent with an email address is polite practice.
Rate limit: 1.5 s between requests.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("primary.fed")

_RSS_MONETARY = "https://www.federalreserve.gov/feeds/press_monetary.xml"
_RATE_LIMIT_S = 1.5


class FedPressReleaseProvider(BaseProvider):
    """Provides primary-source Federal Reserve monetary policy press releases."""

    name = "federal_reserve"

    def __init__(self, user_agent: str, **kwargs):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_req: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    # -- Public API -----------------------------------------------------------

    def fetch_recent_releases(self, days_back: int = 7) -> list[NormalisedEvent]:
        """Return recent monetary policy press releases as NormalisedEvents."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        items = self._fetch_rss_items()
        events: list[NormalisedEvent] = []

        for title, link, published_at in items:
            if published_at and published_at < cutoff:
                continue
            statement = self._fetch_statement_text(link)
            evt = NormalisedEvent(
                source="federal_reserve",
                source_type="central_bank",
                published_at=published_at,
                title=f"Fed: {title}",
                summary=statement[:800] if statement else title,
                url=link,
                event_type="central_bank_statement",
                factual_confidence_score=1.0,
                importance_score=0.95,
                raw_data={
                    "institution": "Federal Reserve",
                    "full_text": statement,
                    "rss_title": title,
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Federal Reserve: %d press releases (last %dd)", len(events), days_back)
        return events

    # -- Internal helpers -----------------------------------------------------

    def _fetch_rss_items(self) -> list[tuple[str, str, datetime | None]]:
        """Return (title, link, published_at) tuples from the monetary RSS feed."""
        self._throttle()
        try:
            resp = self._session.get(_RSS_MONETARY, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Fed RSS fetch failed: %s", exc)
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("Fed RSS parse error: %s", exc)
            return []

        channel = root.find("channel")
        raw_items = channel.findall("item") if channel is not None else root.findall("item")
        result = []
        for item in raw_items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            if title and link:
                result.append((title, link, _parse_rss_date(pub_raw)))
        return result

    def _fetch_statement_text(self, url: str) -> str:
        """Download and extract plain text from a Fed press release page or PDF."""
        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("Fed release page unavailable %s: %s", url, exc)
            return ""

        ct = resp.headers.get("Content-Type", "").lower()
        if "pdf" in ct or url.lower().endswith(".pdf"):
            return _extract_pdf_text(resp.content)
        return _extract_fed_html(resp.text)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure module-level helpers (testable without instantiation) ---------------

def _parse_rss_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None


def _extract_fed_html(html: str) -> str:
    """Extract statement body from a Federal Reserve press release HTML page."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    # Fed press releases: main content lives in #article or a column div
    article = (
        soup.find("div", id="article")
        or soup.find("div", class_="col-md-8")
        or soup.find("div", class_="col-xs-12 col-sm-8")
        or soup.find("main")
    )
    target = article or soup.body or soup
    return " ".join(target.get_text(separator=" ").split())[:2000]


def _extract_pdf_text(content: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        text = " ".join(page.get_text() for page in doc)
        return " ".join(text.split())[:2000]
    except Exception as exc:
        logger.debug("PDF text extraction failed: %s", exc)
        return ""
