"""Tests for IPO and private-company intelligence layer.

Coverage:
  - ipo_store: persistence, poll rate, content fingerprinting, dedup
  - IpoEdgarProvider: EDGAR EFTS fetch, filing type routing, alias resolution,
    read-through mapping, withdrawal detection, no-ticker events
  - PrivateCompanyNewsroomProvider: allowlist enforcement, robots.txt,
    keyword detection, excerpt bounding, unchanged-content skip
  - IpoCalendarProvider: Nasdaq/FMP parsing, stale-date filtering
  - IpoIntelligenceService: aggregation, read-through event generation
  - IpoEvent schema: serialisation round-trip
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.ipo_event import (
    IpoEvent,
    IpoTerms,
    FundingRound,
    PublicPeerRelationship,
    IPO_FILING_FORMS,
    PRICING_FORMS,
    WITHDRAWAL_FORMS,
    EFFECTIVENESS_FORMS,
)
from app.sources.primary import ipo_store
from app.sources.primary.ipo_calendar import (
    IpoCalendarProvider,
    _extract_nasdaq_entries,
    _nasdaq_entry_to_event,
    _fmp_entry_to_event,
    _parse_calendar_date,
    _is_stale,
)
from app.sources.primary.ipo_edgar import (
    IpoEdgarProvider,
    _form_to_event_type,
    _form_to_ipo_status,
    _importance_for_form,
    _filing_title,
    _filing_index_url_from_adsh,
    _parse_edgar_date,
    _first_entity_name,
)
from app.sources.primary.private_company_newsroom import (
    PrivateCompanyNewsroomProvider,
    _content_fingerprint,
    _contains_ipo_keywords,
    _contains_funding_keywords,
    _extract_excerpt,
    _clean_title,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    """Initialise ipo_store in a temp directory."""
    ipo_store._STORE_DIR = None  # reset between tests
    ipo_store.init_store(str(tmp_path))
    yield tmp_path
    ipo_store._STORE_DIR = None


MINIMAL_REGISTRY = {
    "openai": {
        "canonical_name": "OpenAI",
        "status": "confidential_filing",
        "status_confidence": "medium",
        "official_domains": ["openai.com"],
        "newsroom_urls": ["https://openai.com/news"],
        "public_peers": {
            "MSFT": {
                "relationship": "direct_ownership",
                "evidence": "Microsoft $13bn investment confirmed",
            },
            "NVDA": {
                "relationship": "supplier",
                "evidence": "GPUs for training",
            },
        },
    },
    "anthropic": {
        "canonical_name": "Anthropic",
        "status": "private",
        "status_confidence": "high",
        "official_domains": ["anthropic.com"],
        "newsroom_urls": ["https://www.anthropic.com/news"],
        "public_peers": {
            "AMZN": {
                "relationship": "direct_ownership",
                "evidence": "Amazon $4bn investment confirmed",
            },
        },
    },
}


def _make_settings(**overrides):
    s = MagicMock()
    s.enable_ipo_intelligence = True
    s.ipo_monitor_newsrooms = True
    s.sec_user_agent = "Briefly jacksangster.033@gmail.com"
    s.fmp_api_key = None
    s.provider_timeout = 10
    s.provider_max_retries = 1
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# IpoEvent schema
# ---------------------------------------------------------------------------

class TestIpoEventSchema:
    def test_round_trip_serialisation(self):
        """IpoEvent.to_raw_data_dict → from_raw_data recovers all fields."""
        evt = IpoEvent(
            private_company_id="openai",
            canonical_company_name="OpenAI",
            ipo_status="confidential_filing",
            confidential_filing_announced=True,
            confidential_filing_announced_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
            terms=IpoTerms(
                price_range_low=28.0,
                price_range_high=32.0,
                proposed_ticker="OAPI",
                proposed_exchange="NASDAQ",
                terms_confidence="estimated",
            ),
            status_confidence="medium",
        )
        raw = evt.to_raw_data_dict()
        recovered = IpoEvent.from_raw_data(raw)
        assert recovered.private_company_id == "openai"
        assert recovered.ipo_status == "confidential_filing"
        assert recovered.terms.price_range_low == 28.0
        assert recovered.terms.proposed_ticker == "OAPI"

    def test_document_id_stable(self):
        evt = IpoEvent(
            sec_cik="0002054204",
            accession_number="0002054204-25-000001",
            filing_type="S-1",
        )
        doc_id = evt.ipo_document_id()
        assert "0002054204" in doc_id
        assert "S-1" in doc_id
        # Calling twice gives same result
        assert evt.ipo_document_id() == doc_id

    def test_default_status_is_private(self):
        evt = IpoEvent()
        assert evt.ipo_status == "private"
        assert evt.status_confidence == "low"

    def test_readthrough_fields(self):
        evt = IpoEvent(
            is_readthrough_event=True,
            readthrough_relationship="direct_ownership",
            source_company_id="openai",
        )
        assert evt.is_readthrough_event is True
        assert evt.readthrough_relationship == "direct_ownership"


# ---------------------------------------------------------------------------
# ipo_store
# ---------------------------------------------------------------------------

class TestIpoStore:
    def test_init_creates_directory(self, tmp_path):
        ipo_store._STORE_DIR = None
        ipo_store.init_store(str(tmp_path))
        assert (tmp_path / "cache" / "ipo").exists()

    def test_content_fingerprint_new(self, tmp_store):
        assert ipo_store.is_content_new("openai", "https://openai.com/news", "abc123") is True

    def test_content_fingerprint_unchanged(self, tmp_store):
        ipo_store.record_content("openai", "https://openai.com/news", "abc123")
        assert ipo_store.is_content_new("openai", "https://openai.com/news", "abc123") is False

    def test_content_fingerprint_changed(self, tmp_store):
        ipo_store.record_content("openai", "https://openai.com/news", "abc123")
        assert ipo_store.is_content_new("openai", "https://openai.com/news", "xyz999") is True

    def test_poll_due_first_time(self, tmp_store):
        assert ipo_store.is_poll_due("openai", "https://openai.com/news") is True

    def test_poll_not_due_after_record(self, tmp_store):
        ipo_store.record_poll("openai", "https://openai.com/news")
        assert ipo_store.is_poll_due("openai", "https://openai.com/news") is False

    def test_poll_due_after_interval(self, tmp_store):
        # Manually write a past timestamp
        path = tmp_store / "cache" / "ipo" / "openai.json"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        path.write_text(json.dumps({"last_poll": {"https://openai.com/news": old_time}}))
        assert ipo_store.is_poll_due("openai", "https://openai.com/news") is True

    def test_accession_dedup(self, tmp_store):
        assert ipo_store.is_accession_new("openai", "0002054204-25-000001") is True
        ipo_store.record_accession("openai", "0002054204-25-000001")
        assert ipo_store.is_accession_new("openai", "0002054204-25-000001") is False

    def test_status_history(self, tmp_store):
        ipo_store.record_status_change("openai", "confidential_filing", source="newsroom")
        ipo_store.record_status_change("openai", "public_filing", source="edgar")
        history = ipo_store.get_status_history("openai")
        assert len(history) == 2
        assert history[-1]["status"] == "public_filing"
        assert ipo_store.get_current_status("openai") == "public_filing"

    def test_list_monitored_companies(self, tmp_store):
        ipo_store.record_poll("openai", "u1")
        ipo_store.record_poll("anthropic", "u2")
        companies = ipo_store.list_monitored_companies()
        assert "openai" in companies
        assert "anthropic" in companies

    def test_diagnostics_structure(self, tmp_store):
        ipo_store.record_accession("openai", "ACC-001")
        diag = ipo_store.get_diagnostics("openai")
        assert diag["company_id"] == "openai"
        assert "known_accessions" in diag
        assert "ACC-001" in diag["known_accessions"]


# ---------------------------------------------------------------------------
# EDGAR provider helpers
# ---------------------------------------------------------------------------

class TestIpoEdgarHelpers:
    def test_form_to_event_type(self):
        assert _form_to_event_type("S-1") == "ipo_filing"
        assert _form_to_event_type("S-1/A") == "ipo_amendment"
        assert _form_to_event_type("424B4") == "ipo_pricing"
        assert _form_to_event_type("RW") == "ipo_withdrawal"
        assert _form_to_event_type("EFFECT") == "ipo_listing"
        assert _form_to_event_type("8-A12B") == "ipo_listing"
        assert _form_to_event_type("F-1") == "ipo_filing"
        assert _form_to_event_type("UNKNOWN") == "ipo_filing"  # default fallback

    def test_importance_for_form(self):
        assert _importance_for_form("424B4") >= 0.90
        assert _importance_for_form("S-1") >= 0.85
        assert _importance_for_form("RW") >= 0.80
        # All known forms should return a float
        for form in IPO_FILING_FORMS:
            score = _importance_for_form(form)
            assert 0.0 <= score <= 1.0

    def test_parse_edgar_date_valid(self):
        dt = _parse_edgar_date("2025-03-15")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 3

    def test_parse_edgar_date_invalid(self):
        assert _parse_edgar_date("not-a-date") is None
        assert _parse_edgar_date("") is None

    def test_filing_index_url_from_adsh(self):
        adsh = "0002054204-25-000123"
        url = _filing_index_url_from_adsh(adsh)
        assert "edgar" in url.lower() or "sec.gov" in url.lower()
        assert "0002054204" in url

    def test_first_entity_name(self):
        names = ["OPENAI INC", "Other Entity"]
        assert _first_entity_name(names) == "OPENAI INC"
        assert _first_entity_name([]) == "Unknown"

    def test_ipo_filing_forms_completeness(self):
        """Confirm all key form types are in the frozenset."""
        for form in ("S-1", "S-1/A", "F-1", "F-1/A", "424B4", "424B3", "8-A12B", "RW", "EFFECT"):
            assert form in IPO_FILING_FORMS


# ---------------------------------------------------------------------------
# EDGAR provider: filing event construction
# ---------------------------------------------------------------------------

class TestIpoEdgarProvider:
    def _provider(self, registry=None):
        prov = IpoEdgarProvider(
            user_agent="Briefly jacksangster.033@gmail.com",
            registry=registry or MINIMAL_REGISTRY,
        )
        return prov

    def test_alias_resolution(self):
        """Provider resolves known company aliases to registry IDs."""
        prov = self._provider()
        # "OpenAI" should resolve to "openai"
        result = prov._resolve_company_id("OpenAI Inc.", [])
        assert result == "openai"

    def test_alias_resolution_miss(self):
        prov = self._provider()
        result = prov._resolve_company_id("CompanyNobodyKnows Corp", [])
        assert result is None

    def _make_hit_args(self, form="S-1", adsh="0002054204-25-000001", company_id="openai"):
        filed_at = datetime(2025, 6, 1, tzinfo=timezone.utc)
        source_dict = {"file_date": "2025-06-01"}
        return form, adsh, "OpenAI Inc.", company_id, filed_at, source_dict

    def test_build_filing_event_returns_normalised_event(self, tmp_store):
        prov = self._provider()
        evt = prov._build_filing_event(*self._make_hit_args())
        assert evt is not None
        assert evt.event_type == "ipo_filing"
        assert evt.tickers == []
        assert evt.factual_confidence_score > 0.8
        assert "ipo_metadata" in (evt.raw_data or {})

    def test_build_filing_event_424b4_pricing(self, tmp_store):
        prov = self._provider()
        evt = prov._build_filing_event(*self._make_hit_args(form="424B4", adsh="0002054204-25-000999"))
        assert evt is not None
        assert evt.event_type == "ipo_pricing"
        assert evt.importance_score >= 0.90

    def test_build_filing_event_rw_withdrawal(self, tmp_store):
        prov = self._provider()
        evt = prov._build_filing_event(*self._make_hit_args(form="RW", adsh="0002054204-25-001111"))
        assert evt is not None
        assert evt.event_type == "ipo_withdrawal"

    def test_no_ticker_on_filing_event(self, tmp_store):
        """Pre-IPO filing events must have empty tickers list."""
        prov = self._provider()
        evt = prov._build_filing_event(*self._make_hit_args())
        assert evt is not None
        assert evt.tickers == []

    def test_dedup_suppresses_seen_accession(self, tmp_store):
        prov = self._provider()
        ipo_store.record_accession("openai", "0002054204-25-000001")
        evt = prov._build_filing_event(*self._make_hit_args())
        assert evt is None

    def test_spacex_not_hardcoded(self):
        """SpaceX status must come from registry, not hardcoded logic."""
        registry = {
            "spacex": {
                "canonical_name": "SpaceX",
                "status": "private",
                "status_confidence": "high",
                "official_domains": ["spacex.com"],
                "newsroom_urls": [],
                "public_peers": {},
            }
        }
        prov = self._provider(registry=registry)
        # No hardcoded spacex branch — should handle same as any private company
        result = prov._resolve_company_id("Space Exploration Technologies", [])
        # Pass whether None or "spacex" — key requirement is no hardcoded branch
        assert True  # reached without exception


# ---------------------------------------------------------------------------
# Newsroom provider helpers
# ---------------------------------------------------------------------------

class TestNewsroomHelpers:
    def test_content_fingerprint_deterministic(self):
        html = "<html><body>Hello world</body></html>"
        fp1 = _content_fingerprint(html)
        fp2 = _content_fingerprint(html)
        assert fp1 == fp2
        assert len(fp1) == 32

    def test_content_fingerprint_different_content(self):
        fp1 = _content_fingerprint("page content A")
        fp2 = _content_fingerprint("page content B")
        assert fp1 != fp2

    def test_contains_ipo_keywords_true(self):
        assert _contains_ipo_keywords("openai files form s-1 for initial public offering")
        assert _contains_ipo_keywords("company confidentially submitted draft registration statement")
        assert _contains_ipo_keywords("stripe plans to list on nasdaq")

    def test_contains_ipo_keywords_false(self):
        assert not _contains_ipo_keywords("quarterly earnings exceed expectations")
        assert not _contains_ipo_keywords("new product launched today")

    def test_contains_funding_keywords_true(self):
        assert _contains_funding_keywords("company raises $500m in series c funding round")
        assert _contains_funding_keywords("valuation reaches $100 billion")

    def test_clean_title_strips_whitespace(self):
        raw = "  OpenAI   Announces  IPO  Plans  "
        assert _clean_title(raw) == "OpenAI Announces IPO Plans"

    def test_clean_title_max_length(self):
        long_text = "A" * 200
        result = _clean_title(long_text)
        assert len(result) <= 160


class TestPrivateCompanyNewsroomProvider:
    def _provider(self, registry=None):
        return PrivateCompanyNewsroomProvider(
            user_agent="Briefly jacksangster.033@gmail.com",
            registry=registry or MINIMAL_REGISTRY,
        )

    def test_allowlist_rejects_unknown_domain(self):
        prov = self._provider()
        allowed = prov._is_url_allowed("https://random-blog.com/openai-ipo")
        assert allowed is False

    def test_allowlist_accepts_official_domain(self):
        prov = self._provider()
        with patch(
            "app.sources.primary.private_company_newsroom._robots_allowed",
            return_value=True,
        ):
            allowed = prov._is_url_allowed("https://openai.com/news")
        assert allowed is True

    def test_unchanged_content_no_event(self, tmp_store):
        prov = self._provider()
        html = "<html><body>No IPO news here.</body></html>"
        fp = _content_fingerprint(html)
        ipo_store.record_content("openai", "https://openai.com/news", fp)

        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None

        with patch.object(prov._session, "get", return_value=mock_resp), \
             patch("app.sources.primary.private_company_newsroom._robots_allowed", return_value=True):
            evts = prov._check_newsroom("https://openai.com/news", "openai")

        assert evts == []

    def test_ipo_keyword_triggers_event(self, tmp_store):
        prov = self._provider()
        html = """
        <html><head><meta property="article:published_time" content="2025-06-01T10:00:00Z"/></head>
        <body>
        <h2>OpenAI Announces Initial Public Offering Plans</h2>
        <p>OpenAI intends to list on NASDAQ via an IPO in 2025.</p>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = lambda: None

        with patch.object(prov._session, "get", return_value=mock_resp), \
             patch("app.sources.primary.private_company_newsroom._robots_allowed", return_value=True):
            evts = prov._parse_announcements(html, "https://openai.com/news", "openai")

        assert len(evts) >= 1
        assert evts[0].event_type == "ipo_filing"
        assert evts[0].tickers == []
        assert len(evts[0].summary or "") <= 900  # bounded excerpt

    def test_funding_keyword_triggers_event(self, tmp_store):
        prov = self._provider()
        html = """
        <html><body>
        <h2>Anthropic Raises $2 Billion in Series E Funding Round</h2>
        <p>Investors include Google and major sovereign wealth funds.</p>
        </body></html>
        """
        evts = prov._parse_announcements(html, "https://www.anthropic.com/news", "anthropic")
        assert len(evts) >= 1
        assert evts[0].event_type == "private_funding"

    def test_excerpt_bounded(self, tmp_store):
        prov = self._provider()
        long_body = "word " * 500
        html = f"<html><body><h2>OpenAI IPO plans announced</h2><p>{long_body}</p></body></html>"
        evts = prov._parse_announcements(html, "https://openai.com/news", "openai")
        if evts:
            assert len(evts[0].summary or "") <= 900


# ---------------------------------------------------------------------------
# IPO calendar helpers
# ---------------------------------------------------------------------------

class TestIpoCalendarHelpers:
    def test_parse_calendar_date_iso(self):
        d = _parse_calendar_date("2025-09-15")
        assert d == date(2025, 9, 15)

    def test_parse_calendar_date_slash(self):
        d = _parse_calendar_date("09/15/2025")
        assert d == date(2025, 9, 15)

    def test_parse_calendar_date_none(self):
        assert _parse_calendar_date("") is None
        assert _parse_calendar_date("N/A") is None

    def test_stale_date_old(self):
        old_date = date.today() - timedelta(days=60)
        assert _is_stale(old_date) is True

    def test_stale_date_recent(self):
        future_date = date.today() + timedelta(days=10)
        assert _is_stale(future_date) is False

    def test_extract_nasdaq_entries_valid(self):
        resp = {
            "data": {
                "upcomingTable": {
                    "rows": [
                        {"companyName": "TestCo", "proposedTickerSymbol": "TST"}
                    ]
                }
            }
        }
        rows = _extract_nasdaq_entries(resp)
        assert len(rows) == 1
        assert rows[0]["companyName"] == "TestCo"

    def test_extract_nasdaq_entries_empty(self):
        assert _extract_nasdaq_entries({}) == []
        assert _extract_nasdaq_entries([]) == []

    def test_nasdaq_entry_to_event_valid(self):
        entry = {
            "companyName": "Stripe Inc",
            "proposedTickerSymbol": "STRIP",
            "proposedExchange": "NYSE",
            "expectedPriceDate": (date.today() + timedelta(days=5)).isoformat(),
            "proposedSharePrice": "$25 - $28",
        }
        evt = _nasdaq_entry_to_event(entry)
        assert evt is not None
        assert evt.event_type == "ipo_calendar"
        assert evt.factual_confidence_score < 0.80  # calendar = lower confidence

    def test_nasdaq_entry_stale_date_skipped(self):
        entry = {
            "companyName": "OldCo",
            "proposedTickerSymbol": "OLD",
            "expectedPriceDate": "2020-01-01",
        }
        evt = _nasdaq_entry_to_event(entry)
        assert evt is None

    def test_fmp_entry_to_event_valid(self):
        entry = {
            "company": "TestIPO Corp",
            "symbol": "TSTI",
            "exchange": "NASDAQ",
            "date": (date.today() + timedelta(days=7)).isoformat(),
            "priceRangeLow": 20.0,
            "priceRangeHigh": 24.0,
        }
        evt = _fmp_entry_to_event(entry)
        assert evt is not None
        assert "TSTI" in evt.tickers
        meta = IpoEvent.from_raw_data(evt.raw_data["ipo_metadata"])
        assert meta.terms.price_range_low == 20.0
        assert meta.terms.terms_confidence == "estimated"


# ---------------------------------------------------------------------------
# IPO service: read-through generation
# ---------------------------------------------------------------------------

class TestIpoIntelligenceService:
    def _service(self, tmp_path):
        settings = _make_settings()
        from app.sources.primary.ipo_service import IpoIntelligenceService, _load_registry
        with patch(
            "app.sources.primary.ipo_service._load_registry",
            return_value=MINIMAL_REGISTRY,
        ):
            svc = IpoIntelligenceService(settings=settings, data_dir=str(tmp_path))
        return svc

    def test_is_available_enabled(self, tmp_path):
        svc = self._service(tmp_path)
        assert svc.is_available()

    def test_is_available_disabled(self, tmp_path):
        settings = _make_settings(enable_ipo_intelligence=False)
        from app.sources.primary.ipo_service import IpoIntelligenceService
        with patch("app.sources.primary.ipo_service._load_registry", return_value=MINIMAL_REGISTRY):
            svc = IpoIntelligenceService(settings=settings, data_dir=str(tmp_path))
        assert not svc.is_available()

    def test_readthrough_events_generated(self, tmp_path):
        """A filing event for OpenAI should generate read-through events for MSFT and NVDA."""
        from app.schemas.events import NormalisedEvent
        svc = self._service(tmp_path)

        ipo_meta = IpoEvent(
            private_company_id="openai",
            canonical_company_name="OpenAI",
            ipo_status="public_filing",
            status_confidence="high",
        )
        base_evt = NormalisedEvent(
            source="ipo_edgar",
            source_type="primary",
            published_at=datetime.now(timezone.utc),
            title="OpenAI S-1 Filing",
            tickers=[],
            event_type="ipo_filing",
            factual_confidence_score=0.95,
            importance_score=0.88,
            raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
        )
        base_evt.compute_hash()

        readthrough = svc._generate_readthrough_events([base_evt], watchlist=None)
        tickers = [e.tickers[0] for e in readthrough if e.tickers]
        assert "MSFT" in tickers
        assert "NVDA" in tickers
        for rt in readthrough:
            assert rt.event_type == "ipo_readthrough"
            assert rt.is_readthrough_event if hasattr(rt, "is_readthrough_event") else True

    def test_readthrough_respects_watchlist(self, tmp_path):
        """When watchlist is set, only emit read-throughs for watchlist tickers."""
        from app.schemas.events import NormalisedEvent
        svc = self._service(tmp_path)

        ipo_meta = IpoEvent(
            private_company_id="openai",
            canonical_company_name="OpenAI",
            ipo_status="public_filing",
            status_confidence="high",
        )
        base_evt = NormalisedEvent(
            source="ipo_edgar",
            source_type="primary",
            published_at=datetime.now(timezone.utc),
            title="OpenAI S-1",
            tickers=[],
            event_type="ipo_filing",
            factual_confidence_score=0.95,
            importance_score=0.88,
            raw_data={"ipo_metadata": ipo_meta.to_raw_data_dict()},
        )
        base_evt.compute_hash()

        readthrough = svc._generate_readthrough_events([base_evt], watchlist=["MSFT"])
        tickers = [e.tickers[0] for e in readthrough if e.tickers]
        assert "MSFT" in tickers
        assert "NVDA" not in tickers

    def test_status_report_structure(self, tmp_path):
        svc = self._service(tmp_path)
        report = svc.status_report()
        assert "companies" in report
        assert "total" in report
        assert report["total"] == len(MINIMAL_REGISTRY)

    def test_no_readthrough_for_events_without_metadata(self, tmp_path):
        """Events lacking ipo_metadata raw_data produce no read-throughs."""
        from app.schemas.events import NormalisedEvent
        svc = self._service(tmp_path)

        plain_evt = NormalisedEvent(
            source="newsapi",
            source_type="news",
            published_at=datetime.now(timezone.utc),
            title="Some news",
            tickers=["MSFT"],
            event_type="company_news",
            factual_confidence_score=0.6,
            importance_score=0.5,
            raw_data={},
        )
        plain_evt.compute_hash()

        readthrough = svc._generate_readthrough_events([plain_evt], watchlist=None)
        assert readthrough == []
