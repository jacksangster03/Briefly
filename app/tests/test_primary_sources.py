"""Tests for app/sources/primary/ providers.

All HTTP calls are mocked; no network access required.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.sources.primary.boe import BoEProvider, _extract_boe_date
from app.sources.primary.edgar_earnings import (
    EarningsReleaseProvider,
    _looks_like_earnings,
    _parse_date,
)
from app.sources.primary.fed import FedPressReleaseProvider, _extract_fed_html, _parse_rss_date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resp(
    status: int,
    content: bytes,
    content_type: str = "application/json",
) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.content = content
    m.text = content.decode("utf-8", errors="replace")
    m.headers = {"Content-Type": content_type}
    m.raise_for_status = MagicMock()
    try:
        m.json.return_value = json.loads(content)
    except Exception:
        m.json.side_effect = ValueError("not json")
    return m


_SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Federal Reserve Press Release - Monetary Policy</title>
    <item>
      <title>Federal Reserve issues FOMC statement</title>
      <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20250129a.htm</link>
      <pubDate>Wed, 29 Jan 2025 19:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Federal Reserve issues FOMC statement June 2025</title>
      <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20250611a.htm</link>
      <pubDate>Wed, 11 Jun 2025 18:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

_FOMC_HTML = b"""
<html><body>
<nav>Site navigation</nav>
<div id="article">
  <p>The Federal Reserve decided to maintain the target range for the federal funds rate
  at 5-1/4 to 5-1/2 percent. The Committee judges that the risks to achieving its
  employment and inflation goals are moving into better balance.</p>
</div>
<footer>Footer content</footer>
</body></html>
"""

_BOE_INDEX_HTML = b"""
<html><body>
  <a href="/monetary-policy-summary-and-minutes/2025/may-2025">May 2025</a>
  <a href="/monetary-policy-summary-and-minutes/2025/march-2025">March 2025</a>
  <a href="/other-page">Unrelated link</a>
  <a href="https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/">Index</a>
</body></html>
"""

_BOE_DECISION_HTML = b"""
<html>
<head>
  <meta property="article:published_time" content="2025-05-08T11:00:00Z"/>
</head>
<body>
<article>
  <h1>Monetary Policy Summary, May 2025</h1>
  <p>The Bank of England's Monetary Policy Committee (MPC) voted 7-2 to maintain
  Bank Rate at 4.25%. Two members voted to reduce Bank Rate by 0.25 percentage points.</p>
</article>
</body>
</html>
"""

_TICKERS_JSON = json.dumps({
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}).encode()

_SUBMISSIONS_JSON = json.dumps({
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "8-K"],
            "filingDate": ["2025-01-30", "2025-01-25", "2024-10-31"],
            "accessionNumber": [
                "0000320193-25-000010",
                "0000320193-25-000008",
                "0000320193-24-000150",
            ],
            "primaryDocDescription": [
                "Results of Operations and Financial Condition",
                "Quarterly Report",
                "Results of Operations and Financial Condition",
            ],
        }
    },
}).encode()

_INDEX_JSON = json.dumps({
    "documents": [
        {
            "name": "aapl_press_release.htm",
            "type": "EX-99.1",
            "description": "Press Release",
        },
        {
            "name": "aapl_8k.htm",
            "type": "8-K",
            "description": "Form 8-K",
        },
    ]
}).encode()

_EXHIBIT_HTML = b"""
<html><body>
<h1>Apple Reports First Quarter Results</h1>
<p>Revenue: $124.3 billion, up 4% year over year.</p>
<p>EPS diluted: $2.40. Management expects Q2 revenue between $88.5B and $91.5B.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# FedPressReleaseProvider
# ---------------------------------------------------------------------------

class TestFedPressReleaseProvider:
    def _make(self) -> FedPressReleaseProvider:
        return FedPressReleaseProvider(user_agent="Briefly test@example.com", timeout=5)

    def test_is_configured_with_email(self):
        assert self._make().is_configured()

    def test_is_configured_without_at_sign(self):
        p = FedPressReleaseProvider(user_agent="BrieflyNoEmail", timeout=5)
        assert not p.is_configured()

    def test_fetch_recent_releases_produces_events(self):
        p = self._make()
        responses = [
            _make_resp(200, _SAMPLE_RSS, "application/xml"),
            _make_resp(200, _FOMC_HTML, "text/html"),
            _make_resp(200, _FOMC_HTML, "text/html"),
        ]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_recent_releases(days_back=9999)

        assert len(events) == 2
        evt = events[0]
        assert evt.source == "federal_reserve"
        assert evt.source_type == "central_bank"
        assert evt.event_type == "central_bank_statement"
        assert evt.factual_confidence_score == 1.0
        assert "FOMC" in evt.title
        assert evt.url.startswith("https://www.federalreserve.gov")
        assert evt.content_hash  # compute_hash was called

    def test_cutoff_filters_old_releases(self):
        p = self._make()
        # days_back=1 will exclude the Jan 2025 item (>1 day old), but the Jun 2025
        # item might also be filtered depending on test date; use a small rss with
        # obviously stale items to confirm the filter works
        stale_rss = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item>
            <title>Old release</title>
            <link>https://www.federalreserve.gov/old.htm</link>
            <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
          </item>
        </channel></rss>"""
        p2 = self._make()
        with patch.object(p2._session, "get", return_value=_make_resp(200, stale_rss, "application/xml")):
            events = p2.fetch_recent_releases(days_back=7)
        assert len(events) == 0

    def test_rss_fetch_failure_returns_empty(self):
        p = self._make()
        bad_resp = MagicMock()
        bad_resp.raise_for_status.side_effect = Exception("timeout")
        with patch.object(p._session, "get", return_value=bad_resp):
            events = p.fetch_recent_releases()
        assert events == []

    def test_statement_page_failure_uses_title_as_summary(self):
        p = self._make()
        ok_rss = _make_resp(200, _SAMPLE_RSS, "application/xml")
        fail = MagicMock()
        fail.raise_for_status.side_effect = Exception("403")
        with patch.object(p._session, "get", side_effect=[ok_rss, fail, fail]):
            events = p.fetch_recent_releases(days_back=9999)
        assert len(events) == 2
        # summary falls back to rss title when page fetch fails
        assert events[0].summary  # not empty


class TestFedHelpers:
    def test_parse_rss_date_rfc822(self):
        dt = _parse_rss_date("Wed, 29 Jan 2025 19:00:00 +0000")
        assert dt is not None
        assert dt.year == 2025 and dt.month == 1 and dt.day == 29
        assert dt.tzinfo is not None

    def test_parse_rss_date_iso(self):
        dt = _parse_rss_date("2025-06-11T18:00:00Z")
        assert dt is not None and dt.month == 6

    def test_parse_rss_date_empty(self):
        assert _parse_rss_date("") is None

    def test_extract_fed_html_strips_nav_footer(self):
        text = _extract_fed_html(_FOMC_HTML.decode())
        assert "federal funds rate" in text.lower()
        assert "navigation" not in text.lower()
        assert "footer" not in text.lower()

    def test_extract_fed_html_fallback_to_body(self):
        html = "<html><body><p>Some statement text.</p></body></html>"
        text = _extract_fed_html(html)
        assert "statement text" in text


# ---------------------------------------------------------------------------
# EarningsReleaseProvider
# ---------------------------------------------------------------------------

class TestEarningsReleaseProvider:
    def _make(self) -> EarningsReleaseProvider:
        return EarningsReleaseProvider(user_agent="Briefly test@example.com", timeout=5)

    def test_is_configured(self):
        assert self._make().is_configured()

    def test_empty_tickers_returns_empty(self):
        assert self._make().fetch_earnings_releases([]) == []

    def test_cik_map_failure_returns_empty(self):
        p = self._make()
        bad = MagicMock()
        bad.raise_for_status.side_effect = Exception("503")
        with patch.object(p._session, "get", return_value=bad):
            events = p.fetch_earnings_releases(["AAPL"])
        assert events == []

    def test_fetch_earnings_full_pipeline(self):
        p = self._make()
        responses = [
            _make_resp(200, _TICKERS_JSON),          # CIK map
            _make_resp(200, _SUBMISSIONS_JSON),       # Apple submissions
            _make_resp(200, _INDEX_JSON),             # Filing index
            _make_resp(200, _EXHIBIT_HTML, "text/html"),  # Exhibit document
        ]
        with patch.object(p._session, "get", side_effect=responses):
            # days_back=10 ensures the 2025-01-30 filing is within range
            events = p.fetch_earnings_releases(["AAPL"], days_back=1000)

        assert len(events) == 1
        evt = events[0]
        assert evt.source == "edgar_earnings"
        assert evt.event_type == "earnings"
        assert "AAPL" in evt.tickers
        assert evt.factual_confidence_score == 0.98
        assert "Apple" in evt.raw_data["company_name"]
        assert "Revenue" in evt.raw_data["full_text"]
        assert evt.content_hash

    def test_unknown_ticker_skipped_gracefully(self):
        p = self._make()
        responses = [_make_resp(200, _TICKERS_JSON)]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_earnings_releases(["FAKEXYZ"], days_back=7)
        assert events == []

    def test_non_earnings_8k_skipped(self):
        non_earnings_submissions = json.dumps({
            "name": "Apple Inc.",
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2025-01-30"],
                    "accessionNumber": ["0000320193-25-000010"],
                    "primaryDocDescription": ["Director Departure"],
                }
            },
        }).encode()
        p = self._make()
        responses = [
            _make_resp(200, _TICKERS_JSON),
            _make_resp(200, non_earnings_submissions),
        ]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_earnings_releases(["AAPL"], days_back=1000)
        assert events == []

    def test_cik_map_cached_after_first_fetch(self):
        p = self._make()
        responses = [
            _make_resp(200, _TICKERS_JSON),
            _make_resp(200, _SUBMISSIONS_JSON),
            _make_resp(200, json.dumps({"documents": []}).encode()),  # no usable exhibit
        ]
        with patch.object(p._session, "get", side_effect=responses) as mock_get:
            p.fetch_earnings_releases(["AAPL"], days_back=1000)
            p.fetch_earnings_releases(["MSFT"], days_back=1000)
        # CIK URL should only have been fetched once even across two calls
        cik_calls = [
            c for c in mock_get.call_args_list
            if "company_tickers" in str(c)
        ]
        assert len(cik_calls) == 1


class TestEdgarHelpers:
    def test_looks_like_earnings_positive(self):
        assert _looks_like_earnings("Results of Operations and Financial Condition")
        assert _looks_like_earnings("Quarterly Earnings Release")
        assert _looks_like_earnings("Annual Revenue and Guidance")

    def test_looks_like_earnings_negative(self):
        assert not _looks_like_earnings("Director Departure")
        assert not _looks_like_earnings("Amendment to Credit Agreement")
        assert not _looks_like_earnings("Officer Compensation")

    def test_looks_like_earnings_empty_description(self):
        assert _looks_like_earnings("")  # undescribed 8-K: attempt the fetch

    def test_parse_date_valid(self):
        dt = _parse_date("2025-01-29")
        assert dt is not None
        assert dt == datetime(2025, 1, 29, tzinfo=timezone.utc)

    def test_parse_date_empty(self):
        assert _parse_date("") is None

    def test_parse_date_invalid(self):
        assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# BoEProvider
# ---------------------------------------------------------------------------

class TestBoEProvider:
    def _make(self) -> BoEProvider:
        return BoEProvider(user_agent="Briefly test@example.com", timeout=5)

    def test_is_configured(self):
        assert self._make().is_configured()

    def test_index_fetch_failure_returns_empty(self):
        p = self._make()
        bad = MagicMock()
        bad.raise_for_status.side_effect = Exception("timeout")
        with patch.object(p._session, "get", return_value=bad):
            events = p.fetch_recent_decisions()
        assert events == []

    def test_fetch_recent_decisions_produces_events(self):
        p = self._make()
        responses = [
            _make_resp(200, _BOE_INDEX_HTML, "text/html"),   # index page
            _make_resp(200, _BOE_DECISION_HTML, "text/html"),  # may-2025
            _make_resp(200, _BOE_DECISION_HTML, "text/html"),  # march-2025
        ]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_recent_decisions(days_back=9999)

        assert len(events) >= 1
        evt = events[0]
        assert evt.source == "bank_of_england"
        assert evt.event_type == "central_bank_statement"
        assert evt.factual_confidence_score == 1.0
        assert "MPC" in evt.title or "BoE" in evt.title
        assert evt.published_at is not None
        assert "4.25" in evt.summary or "MPC" in evt.summary

    def test_cutoff_filters_old_decisions(self):
        old_html = b"""
        <html><head><meta property="article:published_time" content="2020-01-01T12:00:00Z"/></head>
        <body><article>Old MPC decision text.</article></body></html>"""
        p = self._make()
        responses = [
            _make_resp(200, _BOE_INDEX_HTML, "text/html"),
            _make_resp(200, old_html, "text/html"),
            _make_resp(200, old_html, "text/html"),
        ]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_recent_decisions(days_back=7)
        assert events == []

    def test_index_deduplicates_links(self):
        dup_html = b"""
        <html><body>
          <a href="/monetary-policy-summary-and-minutes/2025/may-2025">May 2025</a>
          <a href="/monetary-policy-summary-and-minutes/2025/may-2025">May 2025 duplicate</a>
        </body></html>"""
        p = self._make()
        responses = [
            _make_resp(200, dup_html, "text/html"),
            _make_resp(200, _BOE_DECISION_HTML, "text/html"),
        ]
        with patch.object(p._session, "get", side_effect=responses):
            events = p.fetch_recent_decisions(days_back=9999)
        assert len(events) == 1  # duplicate URL collapsed


class TestBoEHelpers:
    def test_extract_date_from_og_meta(self):
        from bs4 import BeautifulSoup
        html = '<html><head><meta property="article:published_time" content="2025-05-08T11:00:00Z"/></head></html>'
        soup = BeautifulSoup(html, "lxml")
        dt = _extract_boe_date(soup, "https://example.com")
        assert dt is not None and dt.month == 5 and dt.year == 2025

    def test_extract_date_from_time_tag(self):
        from bs4 import BeautifulSoup
        html = '<html><body><time datetime="2025-03-20T12:00:00Z">20 March 2025</time></body></html>'
        soup = BeautifulSoup(html, "lxml")
        dt = _extract_boe_date(soup, "https://example.com")
        assert dt is not None and dt.month == 3

    def test_extract_date_from_url(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html></html>", "lxml")
        dt = _extract_boe_date(
            soup,
            "https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2025/june-2025",
        )
        assert dt is not None and dt.year == 2025 and dt.month == 6

    def test_extract_date_returns_none_when_unparseable(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html></html>", "lxml")
        dt = _extract_boe_date(soup, "https://www.bankofengland.co.uk/other")
        assert dt is None
