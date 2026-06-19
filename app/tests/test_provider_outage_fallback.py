"""Tests for provider outage fallback hierarchy and freshness-aware send gating."""
from __future__ import annotations
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent, QuoteData


def _stale_quote(symbol: str, name: str, price: float, pct: float, session: str) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=name,
        current_price=price,
        change_percent=pct,
        source=f"stale_snapshot:{session}",
        timestamp=datetime(2026, 5, 27, 13, 34, tzinfo=timezone.utc),
    )


class TestSnapshotFallbackLabelling:
    def test_stale_snapshot_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_STALE_SNAPSHOT
        q = _stale_quote("SPY", "S&P 500", 500.0, 0.5, "us_pre_open")
        assert source_basis_for_quote(q) == SOURCE_BASIS_STALE_SNAPSHOT

    def test_live_quote_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_LIVE
        q = QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, source="finnhub")
        assert source_basis_for_quote(q) == SOURCE_BASIS_LIVE

    def test_yfinance_quote_source_basis(self):
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_NEAR_REAL_TIME
        q = QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, source="yfinance")
        assert source_basis_for_quote(q) == SOURCE_BASIS_NEAR_REAL_TIME


class TestBriefingStaleSnapshotBehaviour:
    """When intraday provider fetch returns empty but pre-open snapshot exists,
    briefing must use stale_snapshot values and label them."""

    def test_stale_snapshot_not_empty_output(self):
        """A briefing with stale_snapshot quotes should not show 'Market Prices: unavailable'."""
        from app.briefing.formatter import TelegramFormatter
        q = _stale_quote("SPY", "S&P 500", 5000.0, -0.3, "us_pre_open")
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[q], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "Market Prices: unavailable" not in text

    def test_stale_snapshot_banner_present(self):
        """Briefing with stale_snapshot_used=True must show degraded data banner."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "LIVE DATA DEGRADED" in text or "stale" in text.lower()

    def test_no_snapshot_produces_outage_block(self):
        """Briefing with market_data_outage=True and no snapshot shows outage block."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
            market_data_outage=True,
            stale_snapshot_used=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "OUTAGE" in text.upper() or "unavailable" in text.lower()

    def test_stale_snapshot_quotes_not_shown_as_live(self):
        """Quotes from a stale snapshot must not produce 'live' freshness labels."""
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_STALE_SNAPSHOT
        q = _stale_quote("VIX", "VIX", 18.0, -0.5, "us_pre_open")
        assert source_basis_for_quote(q) == SOURCE_BASIS_STALE_SNAPSHOT
        assert source_basis_for_quote(q) != "live"

    def test_provider_outage_no_snapshot_no_regime_shift_from_missing_data(self):
        """No regime shift should be generated when all data is missing."""
        from app.briefing.session_snapshot import build_what_changed_lines
        previous = {"vix_level": 18.0, "us10y": 4.50, "wti_pct": -1.2}
        current = {}  # all missing
        lines = build_what_changed_lines(previous=previous, current=current)
        # Should not generate a regime shift line from empty data
        for line in lines:
            assert "risk_on" not in line.lower() or "->" not in line, (
                f"Unexpected regime shift from empty data: {line}"
            )


class TestOutageEmailBehaviour:
    def test_outage_with_snapshot_not_unavailable(self):
        """Provider outage with snapshot does not output 'Market Prices: unavailable'."""
        from app.briefing.formatter import TelegramFormatter
        q = _stale_quote("SPY", "S&P 500", 5000.0, 0.2, "us_pre_open")
        vix_q = _stale_quote("VIX", "VIX", 18.5, -0.3, "us_pre_open")
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_setup=MarketSetup(index_quotes=[q, vix_q], macro_quotes=[]),
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_data_outage=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "Market Prices: unavailable" not in text

    def test_outage_without_snapshot_shows_outage_email(self):
        """Provider outage with no snapshot shows concise outage email."""
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_outage=True,
            stale_snapshot_used=False,
            market_setup=MarketSetup(index_quotes=[], macro_quotes=[]),
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "OUTAGE" in text.upper() or "unavailable" in text.lower() or "degraded" in text.lower()


class TestFreshnessAwareSendDecision:
    """Tests for the freshness-aware send gating layer."""

    def _briefing_with_status(self, market_status: str, news_status: str, fresh_count: int = 0) -> MorningBriefing:
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc), session_key="us_intraday_risk")
        briefing.market_data_status = market_status
        briefing.news_status = news_status
        briefing.fresh_news_count = fresh_count
        briefing.fresh_news_materiality_score = 3.0 if fresh_count > 0 else 0.0
        briefing.stale_snapshot_used = (market_status == "stale_snapshot")
        return briefing

    def test_live_market_fresh_news_sends_normal(self):
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_NORMAL
        b = self._briefing_with_status("live", "fresh", fresh_count=3)
        dec = make_send_decision(b, is_scheduled=True)
        assert dec.should_send is True
        assert dec.mode == BRIEFING_MODE_NORMAL

    def test_stale_snapshot_no_news_suppresses_scheduled(self):
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_SUPPRESSED
        b = self._briefing_with_status("stale_snapshot", "empty", fresh_count=0)
        dec = make_send_decision(b, is_scheduled=True)
        assert dec.should_send is False
        assert dec.mode == BRIEFING_MODE_SUPPRESSED
        assert "stale_snapshot" in dec.suppress_reason

    def test_unavailable_no_news_suppresses_scheduled(self):
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_SUPPRESSED
        b = self._briefing_with_status("unavailable", "empty", fresh_count=0)
        b.market_data_outage = True
        dec = make_send_decision(b, is_scheduled=True)
        assert dec.should_send is False
        assert dec.mode == BRIEFING_MODE_SUPPRESSED

    def test_stale_snapshot_material_news_sends_degraded_context(self):
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_DEGRADED_CONTEXT
        b = self._briefing_with_status("stale_snapshot", "fresh", fresh_count=2)
        dec = make_send_decision(b, is_scheduled=True)
        assert dec.should_send is True
        assert dec.mode == BRIEFING_MODE_DEGRADED_CONTEXT

    def test_unavailable_market_material_news_sends_news_only(self):
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_NEWS_ONLY
        b = self._briefing_with_status("unavailable", "fresh", fresh_count=2)
        b.market_data_outage = True
        dec = make_send_decision(b, is_scheduled=True)
        assert dec.should_send is True
        assert dec.mode == BRIEFING_MODE_NEWS_ONLY

    def test_dry_run_always_sends(self):
        from app.briefing.send_decision import make_send_decision
        b = self._briefing_with_status("unavailable", "empty", fresh_count=0)
        b.market_data_outage = True
        dec = make_send_decision(b, is_scheduled=False, is_dry_run=True)
        assert dec.should_send is True

    def test_suppressed_log_label_is_correct(self):
        from app.briefing.send_decision import make_send_decision
        b = self._briefing_with_status("stale_snapshot", "empty", fresh_count=0)
        dec = make_send_decision(b, is_scheduled=True)
        assert "skipped_degraded_no_fresh_data" in dec.log_label

    def test_stale_snapshot_not_treated_as_live_for_materiality(self):
        """Stale snapshot quotes must not produce source_basis=live."""
        from app.data_sources.quote_fallback import source_basis_for_quote, SOURCE_BASIS_STALE_SNAPSHOT
        q = QuoteData(symbol="SPY", display_name="S&P 500", current_price=500.0, source="stale_snapshot:us_pre_open")
        assert source_basis_for_quote(q) == SOURCE_BASIS_STALE_SNAPSHOT
        assert source_basis_for_quote(q) != "live"


class TestDegradedFormatterModes:
    """Formatter must render mode-appropriate output."""

    def test_news_only_mode_has_news_led_title(self):
        from app.briefing.formatter import TelegramFormatter
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            briefing_mode="news_only",
            market_data_status="unavailable",
            market_data_outage=True,
            stale_snapshot_used=False,
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert any(tok in text.upper() for tok in ("NEWS", "UPDATE", "FRESH")), (
            f"news_only mode should have a news-led title. Got: {text[:200]}"
        )

    def test_degraded_context_mode_shows_banner(self):
        from app.briefing.formatter import TelegramFormatter
        q = QuoteData(
            symbol="SPY", display_name="S&P 500", current_price=5000.0, change_percent=-0.3,
            source="stale_snapshot:us_pre_open",
            timestamp=datetime(2026, 5, 27, 13, 34, tzinfo=timezone.utc),
        )
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            briefing_mode="degraded_context",
            market_data_status="stale_snapshot",
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
            market_setup=MarketSetup(index_quotes=[q], macro_quotes=[]),
        )
        text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
        assert "DEGRADED" in text.upper() or "stale" in text.lower(), (
            f"degraded_context mode should show degraded banner. Got: {text[:200]}"
        )

    def test_suppressed_mode_not_sent(self):
        """briefing_mode=suppressed: formatter can render, but send decision says no."""
        from app.briefing.send_decision import make_send_decision, BRIEFING_MODE_SUPPRESSED
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_outage=True,
        )
        dec = make_send_decision(briefing, is_scheduled=True)
        assert dec.mode == BRIEFING_MODE_SUPPRESSED
        assert dec.should_send is False

    def test_suppressed_session_has_reason_in_delivery_log_label(self):
        from app.briefing.send_decision import make_send_decision
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_outage=True,
            news_data_outage=True,
        )
        dec = make_send_decision(briefing, is_scheduled=True)
        assert dec.log_label == "skipped_degraded_no_fresh_data"
        assert dec.suppress_reason != ""


class TestNewsFreshness:
    """Tests for fresh news counting and materiality scoring."""

    def _event(self, title: str, score: float, already_sent: bool = False) -> NormalisedEvent:
        e = NormalisedEvent(
            event_id=f"evt_{title[:8]}",
            title=title,
            summary="",
            source="reuters",
            tickers=[],
            cluster_id=f"c_{title[:8]}",
            content_hash=f"h_{title[:8]}",
        )
        e.final_score = score
        e.already_sent = already_sent
        e.update_status = "new"
        return e

    def test_fresh_news_counted_correctly(self):
        from app.briefing.send_decision import _count_fresh_news
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        briefing.global_news = [
            self._event("Fed signals rate cut", 3.0),
            self._event("Old news already sent", 2.0, already_sent=True),
        ]
        count, mat = _count_fresh_news(briefing)
        assert count == 1
        assert mat == 3.0

    def test_already_sent_not_counted_fresh(self):
        from app.briefing.send_decision import _count_fresh_news
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        briefing.global_news = [self._event("Stale headline", 2.0, already_sent=True)]
        count, mat = _count_fresh_news(briefing)
        assert count == 0

    def test_zero_fresh_news_returns_zero_score(self):
        from app.briefing.send_decision import _count_fresh_news
        briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
        count, mat = _count_fresh_news(briefing)
        assert count == 0
        assert mat == 0.0
