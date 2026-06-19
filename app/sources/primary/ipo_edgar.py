"""EDGAR IPO filing monitor.

Detects public S-1, F-1, 424B4, EFFECT, RW, and related filings on SEC EDGAR
using the EFTS full-text search API and the submissions API.

Two discovery modes:
  1. EFTS search by form type (recent filings) — catches new entrants.
  2. Submissions API polling for companies with known CIKs — catches amendments.

Both modes produce NormalisedEvents. The IpoEvent metadata is embedded in
raw_data["ipo_metadata"] for downstream scoring and read-through generation.

No API key required. User-Agent with an email address is required per SEC policy.
Rate limit: ~7.5 req/s enforced internally.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.schemas.ipo_event import (
    EFFECTIVENESS_FORMS,
    IPO_FILING_FORMS,
    PRICING_FORMS,
    WITHDRAWAL_FORMS,
    IpoEvent,
    IpoStatus,
    IpoTerms,
)
from app.sources.primary import ipo_store

logger = get_logger("primary.ipo_edgar")

_EFTS_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_RATE_LIMIT_S = 0.13
_MAX_EXCERPT_CHARS = 1500
_PRICE_RANGE_RE = re.compile(
    r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:to|and|-|–)\s*\$\s*(\d+(?:\.\d{1,2})?)"
    r"(?:\s*per\s+(?:share|ADS|unit))?",
    re.IGNORECASE,
)
_TICKER_RE = re.compile(
    r"(?:proposed\s+|symbol[:\s]*)(?:\"|“)?([A-Z]{1,5})(?:\"|”)?",
    re.IGNORECASE,
)
_EXCHANGE_RE = re.compile(
    r"(?:list|trade|quoted)\s+on\s+(?:the\s+)?(NASDAQ|NYSE|Nasdaq|New York Stock Exchange)",
    re.IGNORECASE,
)


class IpoEdgarProvider(BaseProvider):
    """Monitors SEC EDGAR for IPO-related filings."""

    name = "ipo_edgar"

    def __init__(
        self,
        user_agent: str,
        registry: dict,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.user_agent = user_agent
        self.registry = registry  # loaded from private_companies.yaml
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._last_req: float = 0.0
        # Build alias -> company_id and CIK -> company_id lookup tables
        self._alias_map: dict[str, str] = {}   # lowercase alias -> company_id
        self._cik_map: dict[str, str] = {}     # CIK string -> company_id
        for cid, cfg in registry.items():
            for alias in [cfg.get("canonical_name", "")] + cfg.get("aliases", []):
                self._alias_map[alias.lower()] = cid
            cik = str(cfg.get("sec_cik") or "")
            if cik:
                self._cik_map[cik] = cid

    def is_configured(self) -> bool:
        return bool(self.user_agent and "@" in self.user_agent)

    # -- Public API -----------------------------------------------------------

    def fetch_ipo_filings(self, days_back: int = 7) -> list[NormalisedEvent]:
        """Return NormalisedEvents for IPO-related EDGAR filings."""
        events: list[NormalisedEvent] = []
        # Mode 1: EFTS discovery of all recent IPO-form filings
        events.extend(self._fetch_efts_filings(days_back=days_back))
        # Mode 2: Submissions API for registered companies with known CIKs
        for cik, company_id in self._cik_map.items():
            events.extend(self._fetch_submissions_filings(cik, company_id, days_back=days_back))
        logger.info("ipo_edgar: %d IPO filing events (last %dd)", len(events), days_back)
        return events

    # -- EFTS discovery -------------------------------------------------------

    def _fetch_efts_filings(self, days_back: int) -> list[NormalisedEvent]:
        today = datetime.now(timezone.utc).date()
        start = (today - timedelta(days=days_back)).isoformat()
        forms = ",".join(sorted(IPO_FILING_FORMS - {"DRS", "DRS/A"}))
        self._throttle()
        try:
            data = self._session.get(
                _EFTS_SEARCH,
                params={
                    "forms": forms,
                    "dateRange": "custom",
                    "startdt": start,
                    "enddt": today.isoformat(),
                    "q": "",
                },
                timeout=self.timeout,
            ).json()
        except Exception as exc:
            logger.warning("ipo_edgar: EFTS search failed: %s", exc)
            return []

        hits = data.get("hits", {}).get("hits", []) or []
        events: list[NormalisedEvent] = []
        for hit in hits[:50]:
            src = hit.get("_source", {})
            form = src.get("form", "")
            adsh = src.get("adsh", "")
            if not form or form not in IPO_FILING_FORMS:
                continue
            if adsh and not ipo_store.is_accession_new("_efts", adsh):
                continue

            display_names = src.get("display_names", [])
            entity_name = _first_entity_name(display_names)
            company_id = self._resolve_company_id(entity_name, src.get("ciks", []))
            filed_at = _parse_edgar_date(src.get("file_date", ""))

            evt = self._build_filing_event(
                form=form,
                adsh=adsh,
                entity_name=entity_name,
                company_id=company_id,
                filed_at=filed_at,
                source_dict=src,
            )
            if evt:
                events.append(evt)
                if adsh:
                    ipo_store.record_accession("_efts", adsh)
        return events

    # -- Submissions API polling ----------------------------------------------

    def _fetch_submissions_filings(
        self, cik: str, company_id: str, days_back: int
    ) -> list[NormalisedEvent]:
        self._throttle()
        url = f"{_SUBMISSIONS_BASE}/CIK{int(cik):010d}.json"
        try:
            resp = self._session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            submissions = resp.json()
        except Exception as exc:
            logger.debug("ipo_edgar: submissions fetch failed CIK %s: %s", cik, exc)
            return []

        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        entity_name = submissions.get("name", company_id)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

        events: list[NormalisedEvent] = []
        for i, form in enumerate(forms):
            if form not in IPO_FILING_FORMS:
                continue
            filed_at = _parse_edgar_date(dates[i] if i < len(dates) else "")
            if filed_at and filed_at < cutoff:
                break
            adsh = accessions[i] if i < len(accessions) else ""
            if adsh and not ipo_store.is_accession_new(company_id, adsh):
                continue
            evt = self._build_filing_event(
                form=form,
                adsh=adsh,
                entity_name=entity_name,
                company_id=company_id,
                filed_at=filed_at,
                source_dict={"file_date": (dates[i] if i < len(dates) else "")},
            )
            if evt:
                events.append(evt)
                if adsh:
                    ipo_store.record_accession(company_id, adsh)
        return events

    # -- Event construction ---------------------------------------------------

    def _build_filing_event(
        self,
        form: str,
        adsh: str,
        entity_name: str,
        company_id: str | None,
        filed_at: datetime | None,
        source_dict: dict,
    ) -> NormalisedEvent | None:
        if adsh and company_id and not ipo_store.is_accession_new(company_id, adsh):
            return None
        cfg = self.registry.get(company_id, {}) if company_id else {}
        canonical_name = cfg.get("canonical_name", entity_name)
        event_type = _form_to_event_type(form)
        ipo_status = _form_to_ipo_status(form, company_id, self.registry)

        # Try to fetch cover-page excerpt and extract terms
        terms = IpoTerms(terms_confidence="unavailable")
        excerpt = ""
        filing_url = _filing_index_url_from_adsh(adsh)
        if adsh and form in PRICING_FORMS:
            excerpt, terms = self._extract_prospectus_terms(adsh, entity_name)

        # Build the structured IPO metadata
        ipo_meta = IpoEvent(
            private_company_id=company_id or "",
            canonical_company_name=canonical_name,
            ipo_status=ipo_status,
            status_changed=(company_id is not None),
            filing_type=form,
            public_filing_date=filed_at.date() if filed_at else None,
            sec_cik=str(source_dict.get("ciks", [None])[0] or "") or None,
            accession_number=adsh or None,
            terms=terms,
            official_source_urls=[filing_url] if filing_url else [],
            source_document_ids=[f"{adsh}|{form}"] if adsh else [],
            status_confidence="high",
            last_material_update_at=filed_at,
        )

        title = _filing_title(form, canonical_name)
        summary = excerpt or _filing_summary(form, canonical_name)

        evt = NormalisedEvent(
            source="ipo_edgar",
            source_type="filing",
            published_at=filed_at,
            title=title,
            summary=summary[:600],
            url=filing_url,
            tickers=list(cfg.get("public_peers", {}).keys())[:0],  # no peer tickers on filing event
            event_type=event_type,
            factual_confidence_score=0.97,
            importance_score=_importance_for_form(form),
            raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
        )
        evt.compute_hash()

        # Record status change in store
        if company_id and ipo_status:
            prev = ipo_store.get_current_status(company_id)
            if prev != ipo_status:
                ipo_store.record_status_change(company_id, ipo_status, "ipo_edgar", form)

        ipo_store.record_filing_event(company_id or "_unknown", {
            "form": form,
            "adsh": adsh,
            "entity": canonical_name,
            "filed_at": filed_at.isoformat() if filed_at else None,
            "event_type": event_type,
        })

        return evt

    # -- Prospectus parsing ---------------------------------------------------

    def _extract_prospectus_terms(
        self, adsh: str, entity_name: str
    ) -> tuple[str, IpoTerms]:
        """Download the main prospectus document and extract key terms."""
        self._throttle()
        acc_clean = adsh.replace("-", "")
        index_url = f"{_ARCHIVES_BASE}/{acc_clean[:10]}/{acc_clean}/{adsh}-index.json"
        try:
            resp = self._session.get(index_url, timeout=self.timeout)
            resp.raise_for_status()
            index = resp.json()
        except Exception:
            return "", IpoTerms(terms_confidence="unavailable")

        # Find the primary prospectus document
        doc_url = ""
        for doc in (index.get("documents") or []):
            name = str(doc.get("name", "")).lower()
            doc_type = str(doc.get("type", "")).lower()
            if "424b" in doc_type or (("prosp" in name or "s-1" in name) and name.endswith(".htm")):
                cik = acc_clean[:10].lstrip("0") or "0"
                doc_url = f"{_ARCHIVES_BASE}/{cik}/{acc_clean}/{doc.get('name', '')}"
                break

        if not doc_url:
            return "", IpoTerms(terms_confidence="unavailable")

        self._throttle()
        try:
            resp = self._session.get(doc_url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception:
            return "", IpoTerms(terms_confidence="unavailable")

        html = resp.text
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()
        text = " ".join((soup.body or soup).get_text(separator=" ").split())
        excerpt = text[:_MAX_EXCERPT_CHARS]

        terms = _extract_terms_from_text(text)
        return excerpt, terms

    # -- Helpers --------------------------------------------------------------

    def _resolve_company_id(self, entity_name: str, ciks: list) -> str | None:
        """Match entity name or CIK against the registry."""
        name_lower = entity_name.lower()
        for alias, cid in self._alias_map.items():
            if alias in name_lower or name_lower in alias:
                return cid
        for cik in ciks:
            cid = self._cik_map.get(str(cik))
            if cid:
                return cid
        return None

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < _RATE_LIMIT_S:
            time.sleep(_RATE_LIMIT_S - elapsed)
        self._last_req = time.monotonic()


# -- Pure module-level helpers (testable) ------------------------------------

def _form_to_event_type(form: str) -> str:
    f = form.upper()
    if f in {"S-1", "F-1"}:
        return "ipo_filing"
    if f in {"S-1/A", "F-1/A"}:
        return "ipo_amendment"
    if f in PRICING_FORMS:
        return "ipo_pricing"
    if f in EFFECTIVENESS_FORMS:
        return "ipo_listing"
    if f in WITHDRAWAL_FORMS:
        return "ipo_withdrawal"
    return "ipo_filing"


def _form_to_ipo_status(form: str, company_id: str | None, registry: dict) -> IpoStatus:
    f = form.upper()
    if f in EFFECTIVENESS_FORMS:
        return "listed"
    if f in WITHDRAWAL_FORMS:
        return "withdrawn"
    if f in PRICING_FORMS:
        return "priced"
    if f in {"S-1/A", "F-1/A"}:
        return "public_filing"
    if f in {"S-1", "F-1"}:
        return "public_filing"
    if company_id:
        return registry.get(company_id, {}).get("status", "private")
    return "private"


def _importance_for_form(form: str) -> float:
    mapping = {
        "S-1": 0.92, "F-1": 0.92,
        "S-1/A": 0.78, "F-1/A": 0.78,
        "424B4": 0.95, "424B3": 0.80,
        "8-A12B": 0.88,
        "EFFECT": 0.88,
        "RW": 0.85,
    }
    return mapping.get(form.upper(), 0.70)


def _filing_title(form: str, company: str) -> str:
    labels = {
        "S-1": "IPO Filing: {c} files S-1 registration statement",
        "S-1/A": "IPO Amendment: {c} files S-1/A",
        "F-1": "IPO Filing: {c} files F-1 registration (foreign issuer)",
        "F-1/A": "IPO Amendment: {c} files F-1/A",
        "424B4": "IPO Pricing: {c} final prospectus filed",
        "424B3": "IPO Prospectus: {c} preliminary prospectus",
        "8-A12B": "IPO: {c} registers securities on exchange",
        "EFFECT": "IPO: {c} registration declared effective by SEC",
        "RW": "IPO Withdrawal: {c} withdraws registration",
    }
    tpl = labels.get(form.upper(), "SEC filing: {c} ({f})")
    return tpl.format(c=company, f=form)


def _filing_summary(form: str, company: str) -> str:
    summaries = {
        "S-1": f"{company} submitted its initial S-1 registration statement to the SEC, beginning the public IPO process.",
        "F-1": f"{company} submitted its initial F-1 registration statement as a foreign private issuer.",
        "S-1/A": f"{company} filed an amendment to its S-1 registration statement.",
        "F-1/A": f"{company} filed an amendment to its F-1 registration statement.",
        "424B4": f"{company} filed its final prospectus (424B4). Pricing and terms are now confirmed.",
        "424B3": f"{company} filed a preliminary prospectus outlining proposed offering terms.",
        "8-A12B": f"{company} registered its securities class for listing on a national exchange.",
        "EFFECT": f"The SEC declared {company}'s registration statement effective. Trading is imminent.",
        "RW": f"{company} filed a registration withdrawal, indicating the IPO has been cancelled or postponed.",
    }
    return summaries.get(form.upper(), f"{company} filed {form} with the SEC.")


def _filing_index_url_from_adsh(adsh: str) -> str:
    if not adsh:
        return ""
    acc_clean = adsh.replace("-", "")
    cik_prefix = acc_clean[:10].lstrip("0") or "0"
    return f"https://www.sec.gov/Archives/edgar/data/{cik_prefix}/{acc_clean}/{adsh}-index.htm"


def _parse_edgar_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first_entity_name(display_names: list) -> str:
    for name in display_names:
        clean = re.sub(r"\s*\(CIK[^)]*\)", "", str(name)).split("(")[0].strip()
        if clean:
            return clean
    return "Unknown"


def _extract_terms_from_text(text: str) -> IpoTerms:
    """Extract price range and proposed ticker from prospectus text."""
    price_low: float | None = None
    price_high: float | None = None
    proposed_ticker: str | None = None
    proposed_exchange: str | None = None

    m = _PRICE_RANGE_RE.search(text[:5000])
    if m:
        try:
            price_low = float(m.group(1))
            price_high = float(m.group(2))
        except ValueError:
            pass

    tm = _TICKER_RE.search(text[:3000])
    if tm:
        proposed_ticker = tm.group(1).upper()

    em = _EXCHANGE_RE.search(text[:3000])
    if em:
        raw = em.group(1)
        proposed_exchange = "NASDAQ" if "nasdaq" in raw.lower() else "NYSE"

    confidence = "unavailable"
    if price_low is not None:
        confidence = "confirmed"
    elif proposed_ticker or proposed_exchange:
        confidence = "estimated"

    return IpoTerms(
        price_range_low=price_low,
        price_range_high=price_high,
        proposed_ticker=proposed_ticker,
        proposed_exchange=proposed_exchange,
        terms_confidence=confidence,
    )
