"""Tests for delivery log entries: suppressed vs failed sessions."""
from __future__ import annotations
from datetime import datetime, timezone, date

import pytest

from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent


class TestSendDecisionLogLabels:
    """Delivery log must distinguish suppressed from failed."""

    def test_suppressed_log_label_distinct_from_failed(self):
        from app.briefing.send_decision import make_send_decision
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_outage=True,
        )
        dec = make_send_decision(briefing, is_scheduled=True)
        assert dec.log_label != "failed"
        assert "skipped" in dec.log_label or "suppressed" in dec.log_label

    def test_sent_normal_log_label(self):
        from app.briefing.send_decision import make_send_decision
        e = NormalisedEvent(
            event_id="e1", title="Fed news", summary="", source="reuters",
            tickers=[], cluster_id="c1", content_hash="h1",
        )
        e.final_score = 3.0
        e.already_sent = False
        e.update_status = "new"
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            market_data_status="live",
            news_status="fresh",
            fresh_news_count=1,
            fresh_news_materiality_score=3.0,
        )
        briefing.global_news = [e]
        dec = make_send_decision(briefing, is_scheduled=True)
        assert dec.should_send is True
        assert "sent" in dec.log_label

    def test_stale_snapshot_no_news_log_label(self):
        from app.briefing.send_decision import make_send_decision
        briefing = MorningBriefing(
            generated_at=datetime.now(timezone.utc),
            session_key="us_intraday_risk",
            stale_snapshot_used=True,
            stale_snapshot_session="us_pre_open",
            stale_snapshot_time="13:34",
        )
        dec = make_send_decision(briefing, is_scheduled=True)
        assert dec.should_send is False
        assert "degraded" in dec.log_label or "stale" in dec.log_label or "skipped" in dec.log_label
