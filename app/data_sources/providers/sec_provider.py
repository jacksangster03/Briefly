"""SEC EDGAR provider: public company filings via the EDGAR full-text search API.

Free, no API key required. Must provide a User-Agent string per SEC fair-access policy.
Rate limit: 10 requests/second.
Docs: https://efts.sec.gov/LATEST/search-index?q=...
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.data_sources.base import BaseProvider, ProviderError
from app.logger import get_logger
from app.schemas.events import NormalisedEvent

logger = get_logger("sec")

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
FULL_TEXT_URL = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_URL = "https://data.sec.gov/submissions"

# Filing types most relevant to market-moving events
MATERIAL_FORMS = {"8-K", "10-K", "10-Q", "S-1", "4", "SC 13D", "SC 13G", "DEF 14A"}
MATERIAL_KEYWORDS = {
    "acquisition", "agreement", "bankruptcy", "board", "ceo", "chief financial officer",
    "clinical", "dividend", "earnings", "fda", "guidance", "investigation", "merger",
    "officer", "results", "restructuring", "termination",
}


class SECProvider(BaseProvider):
    name = "sec_edgar"

    def __init__(self, user_agent: str, **kwargs):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self._session.headers.update({"User-Agent": user_agent})
        self._last_request = 0.0

    def is_configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    def _throttle(self) -> None:
        """Enforce SEC's 10 req/s fair access policy."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_request = time.monotonic()

    # -- Full-text search -----------------------------------------------------

    def search_filings(
        self,
        query: str = "",
        date_range: str = "",
        forms: list[str] | None = None,
        tickers: list[str] | None = None,
        limit: int = 20,
    ) -> list[NormalisedEvent]:
        """Search EDGAR full-text search for recent filings.

        Uses the EFTS (EDGAR Full-Text Search) API.
        """
        self._throttle()

        params: dict = {"q": query, "dateRange": "custom", "startdt": "", "enddt": ""}

        if date_range and ":" in date_range:
            start, end = date_range.split(":", 1)
            params["startdt"] = start
            params["enddt"] = end
        elif not date_range:
            today = datetime.now(timezone.utc).date()
            params["startdt"] = (today - timedelta(days=1)).isoformat()
            params["enddt"] = today.isoformat()

        target_forms = forms or sorted(MATERIAL_FORMS)
        if target_forms:
            params["forms"] = ",".join(target_forms)
        if tickers:
            params["q"] = " OR ".join(tickers) if not query else query

        try:
            data = self._get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
            )
        except ProviderError:
            logger.warning("EFTS search failed, trying submissions API")
            return []

        hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
        events = []

        for hit in hits[:limit]:
            source = hit.get("_source", {})
            form_type = source.get("form", source.get("root_forms", ["Filing"])[0]
                                   if source.get("root_forms") else "Filing")
            if form_type not in MATERIAL_FORMS:
                continue
            display_names = source.get("display_names", [])
            entity_name = display_names[0] if display_names else "Unknown"
            description = source.get("file_description", "")
            tickers = self._extract_tickers_from_display(display_names)
            adsh = source.get("adsh", "")
            if not self._is_material_hit(form_type, description):
                continue

            title_parts = [form_type, entity_name.split("(CIK")[0].strip()]
            if description:
                title_parts.append(description)
            title = ": ".join(p for p in title_parts if p)

            evt = NormalisedEvent(
                source="sec_edgar",
                source_type="filing",
                published_at=self._parse_date(source.get("file_date", "")),
                title=title,
                summary=description or entity_name,
                url=self._build_adsh_url(adsh) if adsh else self._build_filing_url(source),
                tickers=tickers,
                event_type=self._classify_form(form_type),
                factual_confidence_score=0.95,
                raw_data=source,
            )
            evt.compute_hash()
            events.append(evt)

        logger.info("Fetched %d SEC filings", len(events))
        return events

    def search_insider_trades(
        self,
        tickers: list[str] | None = None,
        days_back: int = 7,
        limit: int = 50,
    ) -> list[NormalisedEvent]:
        """Fetch recent Form 4 insider transaction filings."""
        today = datetime.now(timezone.utc).date()
        start = (today - timedelta(days=days_back)).isoformat()
        end = today.isoformat()
        events = self.search_filings(
            date_range=f"{start}:{end}",
            forms=["4"],
            tickers=tickers,
            limit=limit,
        )
        return [event for event in events if event.event_type == "insider_transaction"]

    # -- Company filings via submissions API ----------------------------------

    def get_recent_filings(self, cik: str, limit: int = 10) -> list[NormalisedEvent]:
        """Fetch recent filings for a company by CIK number."""
        self._throttle()
        cik_padded = cik.zfill(10)

        try:
            data = self._get(f"{SUBMISSIONS_URL}/CIK{cik_padded}.json")
        except ProviderError:
            logger.warning("Failed to fetch submissions for CIK %s", cik)
            return []

        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        descriptions = recent.get("primaryDocDescription", [])
        company_name = data.get("name", "")

        events = []
        for i in range(min(limit, len(forms))):
            form_type = forms[i] if i < len(forms) else ""
            if form_type not in MATERIAL_FORMS:
                continue

            evt = NormalisedEvent(
                source="sec_edgar",
                source_type="filing",
                published_at=self._parse_date(dates[i] if i < len(dates) else ""),
                title=f"{form_type}: {company_name}",
                summary=descriptions[i] if i < len(descriptions) else "",
                url=self._accession_url(cik_padded, accessions[i] if i < len(accessions) else ""),
                event_type=self._classify_form(form_type),
                factual_confidence_score=0.95,
            )
            evt.compute_hash()
            events.append(evt)

        return events

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _build_filing_url(source: dict) -> str:
        file_num = source.get("file_num", "")
        if file_num:
            return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&filenum={file_num}"
        return "https://www.sec.gov/cgi-bin/browse-edgar"

    @staticmethod
    def _accession_url(cik: str, accession: str) -> str:
        if not accession:
            return ""
        acc_clean = accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"

    @staticmethod
    def _extract_tickers_from_filing(source: dict) -> list[str]:
        tickers = source.get("tickers", "")
        if isinstance(tickers, str) and tickers:
            return [t.strip() for t in tickers.split(",") if t.strip()]
        if isinstance(tickers, list):
            return tickers
        return []

    @staticmethod
    def _extract_tickers_from_display(display_names: list[str]) -> list[str]:
        """Extract ticker symbols from EFTS display_names like 'EXXON MOBIL CORP  (XOM)  (CIK ...)'."""
        import re
        tickers = []
        for name in display_names:
            # Match ticker in parentheses before (CIK
            matches = re.findall(r"\(([A-Z]{1,5})\)", name.split("(CIK")[0])
            tickers.extend(matches)
        return tickers

    @staticmethod
    def _build_adsh_url(adsh: str) -> str:
        """Build a URL to the filing from its accession number."""
        if not adsh:
            return ""
        return f"https://www.sec.gov/Archives/edgar/data/{adsh.split('-')[0]}/{adsh.replace('-', '')}/{adsh}-index.htm"

    @staticmethod
    def _classify_form(form_type: str) -> str:
        form = form_type.upper().strip()
        if form in ("8-K", "8-K/A"):
            return "current_report"
        if form in ("10-K", "10-K/A"):
            return "annual_report"
        if form in ("10-Q", "10-Q/A"):
            return "quarterly_report"
        if form == "4":
            return "insider_transaction"
        if form.startswith("SC 13"):
            return "ownership_disclosure"
        if form.startswith("S-"):
            return "registration"
        return "filing"

    @staticmethod
    def _is_material_hit(form_type: str, description: str) -> bool:
        form = form_type.upper().strip()
        if form in {"8-K", "8-K/A", "4", "4/A"}:
            return True
        if not description:
            return form in {"10-K", "10-Q"}
        text = description.lower()
        return any(keyword in text for keyword in MATERIAL_KEYWORDS)
