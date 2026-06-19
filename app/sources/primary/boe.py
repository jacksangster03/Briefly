"""Bank of England primary-source provider.

Fetches Monetary Policy Committee decisions directly from bankofengland.co.uk.
No API key required.
Rate limit: 1.5 s between requests.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("primary.boe")

_MPC_INDEX_URL = "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/"
_BASE_URL = "https://www.bankofengland.co.uk"
_RATE_LIMIT_S = 1.5

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class BoEProvider(BaseProvider):
    """Provides primary-source Bank of England MPC decisions."""

    name = "bank_of_england"

    def __init__(self, user_agent: str, **kwargs):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._last_req: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.user_agent)

    # -- Public API -----------------------------------------------------------

    def fetch_recent_decisions(self, days_back: int = 45) -> list[NormalisedEvent]:
        """Return recent MPC decisions as NormalisedEvents."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        decision_links = self._fetch_decision_links()
        events: list[NormalisedEvent] = []

        for title, url in decision_links[:4]:  # at most 4 most recent
            text, published_at = self._fetch_decision_page(url)
            if published_at and published_at < cutoff:
                continue
            if not text:
                continue

            evt = NormalisedEvent(
                source="bank_of_england",
                source_type="central_bank",
                published_at=published_at,
                title=f"BoE MPC: {title}",
                summary=text[:800],
                url=url,
                event_type="central_bank_statement",
                factual_confidence_score=1.0,
                importance_score=0.90,
                raw_data={
                    "institution": "Bank of England",
                    "full_text": text,
                },
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Bank of England: %d MPC decisions (last %dd)", len(events), days_back)
        return events

    # -- Internal helpers -----------------------------------------------------

    def _fetch_decision_links(self) -> list[tuple[str, str]]:
        """Return [(title, url)] for the most recent MPC summary pages."""
        self._throttle()
        try:
            resp = self._session.get(_MPC_INDEX_URL, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("BoE MPC index fetch failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        seen: set[str] = set()
        result: list[tuple[str, str]] = []

        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            text = a.get_text(strip=True)
            if "/monetary-policy-summary-and-minutes/" not in href:
                continue
            full_url = href if href.startswith("http") else _BASE_URL + href
            if full_url.rstrip("/") == _MPC_INDEX_URL.rstrip("/"):
                continue
            if full_url not in seen and text:
                seen.add(full_url)
                result.append((text, full_url))

        return result[:6]

    def _fetch_decision_page(self, url: str) -> tuple[str, datetime | None]:
        """Download an MPC decision summary page. Returns (text, published_at)."""
        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("BoE decision page unavailable %s: %s", url, exc)
            return "", None

        soup = BeautifulSoup(resp.text, "lxml")
        published_at = _extract_boe_date(soup, url)

        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        article = (
            soup.find("article")
            or soup.find("div", class_="page-content")
            or soup.find("main")
            or soup.find("div", {"role": "main"})
        )
        target = article or soup.body or soup
        text = " ".join(target.get_text(separator=" ").split())
        return text[:2500], published_at

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure module-level helpers (testable without instantiation) ---------------

def _extract_boe_date(soup: BeautifulSoup, url: str) -> datetime | None:
    """Extract publication date from BoE page metadata, time tags, or URL."""
    # 1. OpenGraph / Dublin Core meta tags
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "") or meta.get("name", "")
        if prop in ("article:published_time", "pubdate", "date", "DC.date"):
            val = meta.get("content", "")
            if val:
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    pass

    # 2. <time datetime="..."> elements
    for time_tag in soup.find_all("time", datetime=True):
        try:
            return datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            pass

    # 3. Extract year/month from URL: /monetary-policy-summary-and-minutes/2025/may-2025
    match = re.search(r"/(\d{4})/([\w]+)-\d{4}$", url)
    if match:
        year = int(match.group(1))
        month = _MONTH_MAP.get(match.group(2).lower())
        if month:
            return datetime(year, month, 1, tzinfo=timezone.utc)

    return None
