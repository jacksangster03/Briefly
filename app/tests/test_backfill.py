"""Tests for historical backfill labelling (Task 3 of session delivery hardening)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.main import (
    BackfillContext,
    _apply_backfill_email_banner,
    _apply_backfill_telegram_banner,
    _parse_catch_up_date,
)
from app.schemas.delivery import EmailRenderResult


# ---------------------------------------------------------------------------
# BackfillContext helpers
# ---------------------------------------------------------------------------

def _make_ctx(session_date: date | None = None) -> BackfillContext:
    sd = session_date or date(2026, 5, 5)
    return BackfillContext(
        session_date=sd,
        generated_at_local=datetime(2026, 5, 6, 1, 15, tzinfo=timezone.utc),
        timezone_name="Europe/Madrid",
    )


def _make_email(subject: str = "Morning Briefing") -> EmailRenderResult:
    return EmailRenderResult(
        subject=subject,
        plain_text="Body text.",
        html_body="<p>Body text.</p>",
        inline_assets=[],
    )


# ---------------------------------------------------------------------------
# Telegram banner tests
# ---------------------------------------------------------------------------

class TestApplyBackfillTelegramBanner:
    def test_banner_prepended_to_first_message(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner(["Hello world"], ctx)
        assert result[0].startswith("<b>HISTORICAL BACKFILL - NOT LIVE</b>")
        assert "Hello world" in result[0]

    def test_subsequent_messages_unchanged(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner(["First", "Second", "Third"], ctx)
        assert len(result) == 3
        assert result[1] == "Second"
        assert result[2] == "Third"

    def test_banner_contains_session_date(self):
        ctx = _make_ctx(session_date=date(2026, 5, 5))
        result = _apply_backfill_telegram_banner(["msg"], ctx)
        # Formatted as 'Mon 05 May 2026'
        assert "05 May 2026" in result[0]

    def test_banner_contains_generation_time(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner(["msg"], ctx)
        assert "2026-05-06 01:15" in result[0]

    def test_banner_contains_timezone_name(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner(["msg"], ctx)
        assert "Europe/Madrid" in result[0]

    def test_empty_messages_returns_banner_only(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner([], ctx)
        assert len(result) == 1
        assert "HISTORICAL BACKFILL" in result[0]

    def test_not_live_phrase_present(self):
        ctx = _make_ctx()
        result = _apply_backfill_telegram_banner(["msg"], ctx)
        assert "NOT LIVE" in result[0]


# ---------------------------------------------------------------------------
# Email banner tests
# ---------------------------------------------------------------------------

class TestApplyBackfillEmailBanner:
    def test_subject_prefixed_with_backfill(self):
        ctx = _make_ctx()
        result = _apply_backfill_email_banner(_make_email("Morning Briefing"), ctx)
        assert result.subject.startswith("[BACKFILL]")
        assert "Morning Briefing" in result.subject

    def test_plain_text_banner_prepended(self):
        ctx = _make_ctx()
        result = _apply_backfill_email_banner(_make_email(), ctx)
        assert result.plain_text.startswith("HISTORICAL BACKFILL - NOT LIVE")
        assert "Body text." in result.plain_text

    def test_html_body_contains_amber_banner(self):
        ctx = _make_ctx()
        result = _apply_backfill_email_banner(_make_email(), ctx)
        assert "#fff3cd" in result.html_body
        assert "HISTORICAL BACKFILL" in result.html_body
        assert "<p>Body text.</p>" in result.html_body

    def test_session_date_in_plain_text(self):
        ctx = _make_ctx(session_date=date(2026, 5, 5))
        result = _apply_backfill_email_banner(_make_email(), ctx)
        assert "05 May 2026" in result.plain_text

    def test_session_date_in_html(self):
        ctx = _make_ctx(session_date=date(2026, 5, 5))
        result = _apply_backfill_email_banner(_make_email(), ctx)
        assert "05 May 2026" in result.html_body

    def test_inline_assets_preserved(self):
        ctx = _make_ctx()
        original = _make_email()
        result = _apply_backfill_email_banner(original, ctx)
        assert result.inline_assets == original.inline_assets

    def test_original_email_not_mutated(self):
        ctx = _make_ctx()
        original = _make_email("Original subject")
        _apply_backfill_email_banner(original, ctx)
        assert original.subject == "Original subject"


# ---------------------------------------------------------------------------
# _parse_catch_up_date future-date rejection
# ---------------------------------------------------------------------------

class TestParseCatchUpDate:
    def _local_now(self) -> datetime:
        return datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)

    def test_today_returns_today(self):
        local_now = self._local_now()
        result = _parse_catch_up_date("today", local_now=local_now)
        assert result == local_now.date()

    def test_yesterday_returns_yesterday(self):
        local_now = self._local_now()
        result = _parse_catch_up_date("yesterday", local_now=local_now)
        assert result == (local_now - timedelta(days=1)).date()

    def test_iso_past_date_accepted(self):
        local_now = self._local_now()
        result = _parse_catch_up_date("2026-05-05", local_now=local_now)
        assert result == date(2026, 5, 5)

    def test_future_date_raises_value_error(self):
        local_now = self._local_now()
        with pytest.raises(ValueError, match="future"):
            _parse_catch_up_date("2026-05-07", local_now=local_now)

    def test_future_date_message_contains_date(self):
        local_now = self._local_now()
        with pytest.raises(ValueError, match="2026-05-07"):
            _parse_catch_up_date("2026-05-07", local_now=local_now)

    def test_today_iso_format_accepted(self):
        local_now = self._local_now()
        result = _parse_catch_up_date("2026-05-06", local_now=local_now)
        assert result == date(2026, 5, 6)
