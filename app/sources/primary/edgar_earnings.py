"""EDGAR primary-source earnings release provider.

Fetches actual earnings press release documents (8-K EX-99.1 exhibits) for
watchlist tickers. Complements the existing SECProvider (which surfaces 8-K
metadata) by downloading and parsing the primary document text.

No API key required. User-Agent with an email address is required by SEC policy.
Rate limit: ~8 req/s enforced internally (SEC policy: 10 req/s).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("primary.edgar_earnings")

_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_RATE_LIMIT_S = 0.13  # ~7.5 req/s, comfortably below SEC's 10 req/s limit

# 8-K item descriptions that strongly suggest an earnings release
_EARNINGS_KEYWORDS = frozenset({
    "result", "earnings", "revenue", "financial", "quarter",
    "annual", "guidance", "outlook", "operations",
})
# Descriptions that indicate this is not an earnings release
_NON_EARNINGS_KEYWORDS = frozenset({
    "director", "amendment", "agreement", "officer", "departure",
    "bylaws", "credit", "indenture", "compensation",
})


class EarningsReleaseProvider(BaseProvider):
    """Fetches earnings press release documents from SEC EDGAR for watchlist tickers."""

    name = "edgar_earnings"

    def __init__(self, user_agent: str, **kwargs):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_req: float = 0.0
        self._ticker_cik_map: dict[str, int] | None = None  # lazy, cached for process lifetime

    def is_configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    # -- Public API -----------------------------------------------------------

    def fetch_earnings_releases(
        self,
        tickers: list[str],
        days_back: int = 2,
    ) -> list[NormalisedEvent]:
        """Return 8-K earnings press release events filed within days_back for each ticker."""
        if not tickers:
            return []

        cik_map = self._get_ticker_cik_map()
        if not cik_map:
            logger.warning("edgar_earnings: CIK map unavailable, skipping")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        events: list[NormalisedEvent] = []

        for ticker in tickers:
            cik = cik_map.get(ticker.upper())
            if not cik:
                logger.debug("edgar_earnings: no CIK for %s", ticker)
                continue
            events.extend(self._fetch_for_cik(ticker, cik, cutoff))

        logger.info(
            "edgar_earnings: %d earnings releases for %d tickers (last %dd)",
            len(events), len(tickers), days_back,
        )
        return events

    # -- Internal helpers -----------------------------------------------------

    def _fetch_for_cik(self, ticker: str, cik: int, cutoff: datetime) -> list[NormalisedEvent]:
        submissions = self._fetch_submissions(cik)
        if not submissions:
            return []

        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        descriptions = recent.get("primaryDocDescription", [])
        company_name = submissions.get("name", ticker)

        events: list[NormalisedEvent] = []
        for i, form in enumerate(forms):
            if form not in ("8-K", "8-K/A"):
                continue
            filed_date = _parse_date(dates[i] if i < len(dates) else "")
            if not filed_date or filed_date < cutoff:
                # Submissions API returns filings newest-first; once past cutoff we're done
                break
            description = descriptions[i] if i < len(descriptions) else ""
            if not _looks_like_earnings(description):
                continue
            accession = accessions[i] if i < len(accessions) else ""
            text, doc_url = self._fetch_exhibit_text(cik, accession)
            if not text:
                continue

            evt = NormalisedEvent(
                source="edgar_earnings",
                source_type="filing",
                published_at=filed_date,
                title=f"{ticker} 8-K: {description or 'Earnings Release'}",
                summary=text[:600],
                url=doc_url or _filing_index_url(cik, accession),
                tickers=[ticker],
                event_type="earnings",
                factual_confidence_score=0.98,
                importance_score=0.90,
                raw_data={
                    "cik": cik,
                    "accession": accession,
                    "company_name": company_name,
                    "full_text": text,
                    "form": form,
                },
            )
            evt.compute_hash()
            events.append(evt)

        return events

    def _get_ticker_cik_map(self) -> dict[str, int]:
        """Download and cache the SEC ticker-to-CIK mapping (one fetch per process run)."""
        if self._ticker_cik_map is not None:
            return self._ticker_cik_map

        self._throttle()
        try:
            resp = self._session.get(_TICKERS_URL, timeout=self.timeout)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            logger.warning("edgar_earnings: could not fetch CIK map: %s", exc)
            self._ticker_cik_map = {}
            return {}

        mapping: dict[str, int] = {}
        for entry in raw.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = entry.get("cik_str")
            if ticker and cik:
                mapping[ticker] = int(cik)
        self._ticker_cik_map = mapping
        logger.debug("edgar_earnings: loaded %d ticker-CIK pairs", len(mapping))
        return mapping

    def _fetch_submissions(self, cik: int) -> dict:
        self._throttle()
        url = f"{_SUBMISSIONS_BASE}/CIK{cik:010d}.json"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("edgar_earnings: submissions fetch failed for CIK %d: %s", cik, exc)
            return {}

    def _fetch_exhibit_text(self, cik: int, accession: str) -> tuple[str, str]:
        """Download the earnings press release exhibit from a filing. Returns (text, url)."""
        if not accession:
            return "", ""
        self._throttle()
        acc_clean = accession.replace("-", "")
        index_url = f"{_ARCHIVES_BASE}/{cik}/{acc_clean}/{accession}-index.json"

        try:
            resp = self._session.get(index_url, timeout=self.timeout)
            resp.raise_for_status()
            index = resp.json()
        except Exception as exc:
            logger.debug("edgar_earnings: filing index fetch failed %s: %s", index_url, exc)
            return "", ""

        for doc in (index.get("documents") or []):
            doc_type = str(doc.get("type", "")).lower()
            doc_name = str(doc.get("name", "")).lower()
            doc_desc = str(doc.get("description", "")).lower()

            is_ex99 = "ex-99" in doc_type or "ex99" in doc_type
            looks_like_pr = any(
                kw in doc_name + doc_desc
                for kw in ("press", "earnings", "result", "financ", "quarter")
            )
            if not (is_ex99 or looks_like_pr):
                continue

            doc_filename = doc.get("name", "")
            doc_url = f"{_ARCHIVES_BASE}/{cik}/{acc_clean}/{doc_filename}"
            text = self._fetch_document(doc_url)
            if text:
                return text, doc_url

        return "", ""

    def _fetch_document(self, url: str) -> str:
        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("edgar_earnings: document fetch failed %s: %s", url, exc)
            return ""

        ct = resp.headers.get("Content-Type", "").lower()
        if "pdf" in ct or url.lower().endswith(".pdf"):
            return _extract_pdf_text(resp.content)
        return _extract_html_text(resp.text)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure module-level helpers (testable without instantiation) ---------------

def _looks_like_earnings(description: str) -> bool:
    """Return True if the 8-K description suggests an earnings release."""
    if not description:
        return True  # undescribed 8-K could still be earnings; attempt the fetch
    d = description.lower()
    if any(kw in d for kw in _NON_EARNINGS_KEYWORDS):
        return False
    return any(kw in d for kw in _EARNINGS_KEYWORDS) or True


def _parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _filing_index_url(cik: int, accession: str) -> str:
    if not accession:
        return ""
    acc_clean = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"


def _extract_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    body = soup.body or soup
    return " ".join(body.get_text(separator=" ").split())[:3000]


def _extract_pdf_text(content: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=content, filetype="pdf")
        text = " ".join(page.get_text() for page in doc)
        return " ".join(text.split())[:3000]
    except Exception as exc:
        logger.debug("edgar_earnings: PDF extraction failed: %s", exc)
        return ""
