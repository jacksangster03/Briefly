"""Tests for Phase 9.8: exact snapshot replay."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.briefing.snapshot_replay import (
    ReplayResult,
    _email_banner_html,
    _email_banner_plain,
    _telegram_banner,
    replay_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(dry_run: bool = True):
    from app.settings import Settings
    s = Settings()
    s.dry_run = dry_run
    s.telegram_bot_token = "tok"
    s.telegram_chat_id = "123"
    s.email_host = "smtp.example.com"
    s.email_port = 587
    s.email_user = "user@example.com"
    s.email_password = "secret"
    s.email_to = "dest@example.com"
    return s


def _make_snap(**overrides):
    base = {
        "profile_name": "default_user",
        "session_key": "morning",
        "session_title": "Morning Briefing",
        "local_date": "2026-05-06",
        "generated_at_local": "2026-05-06 09:30",
        "generated_at_utc": "2026-05-06T07:30:00",
        "telegram_text": "Good morning. Markets are steady.",
        "email_subject": "Morning Briefing | Tue 6 May",
        "email_plain_text": "Markets are steady today.",
        "email_html": "<p>Markets are steady today.</p>",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Banner content
# ---------------------------------------------------------------------------

class TestBanners:
    def test_telegram_banner_contains_replay_label(self):
        snap = _make_snap()
        banner = _telegram_banner(snap)
        assert "SNAPSHOT REPLAY" in banner
        assert "NOT LIVE" in banner
        assert "2026-05-06" in banner
        assert "Morning Briefing" in banner

    def test_email_plain_banner_contains_replay_label(self):
        snap = _make_snap()
        banner = _email_banner_plain(snap)
        assert "SNAPSHOT REPLAY" in banner
        assert "NOT LIVE" in banner
        assert "2026-05-06" in banner

    def test_email_html_banner_uses_blue_border(self):
        snap = _make_snap()
        banner = _email_banner_html(snap)
        assert "0d6efd" in banner  # blue, not amber (amber is backfill)
        assert "SNAPSHOT REPLAY" in banner


# ---------------------------------------------------------------------------
# Missing snapshot
# ---------------------------------------------------------------------------

class TestMissingSnapshot:
    def test_missing_snapshot_populates_errors(self):
        settings = _make_settings()
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=None), \
             patch("app.briefing.snapshot_replay.init_db"):
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                settings=settings,
            )
        assert result.errors
        assert not result.telegram_attempted
        assert not result.email_attempted

    def test_missing_snapshot_no_send_attempted(self):
        settings = _make_settings()
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=None), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger") as mock_tg, \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                settings=settings,
            )
        mock_tg.assert_not_called()
        mock_em.assert_not_called()


# ---------------------------------------------------------------------------
# Dry-run behaviour
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_calls_send_messages(self):
        settings = _make_settings(dry_run=True)
        snap = _make_snap()
        mock_tg = MagicMock()
        mock_tg.return_value.is_configured.return_value = True
        mock_tg.return_value.send_messages.return_value = True

        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger", mock_tg), \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            mock_em.return_value.is_configured.return_value = True
            mock_em.return_value.send_rich.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                settings=settings,
            )

        assert result.telegram_attempted
        mock_tg.return_value.send_messages.assert_called_once()

    def test_dry_run_result_has_dry_run_true(self):
        settings = _make_settings(dry_run=True)
        snap = _make_snap()
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger") as mock_tg, \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            mock_tg.return_value.is_configured.return_value = True
            mock_tg.return_value.send_messages.return_value = True
            mock_em.return_value.is_configured.return_value = True
            mock_em.return_value.send_rich.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                settings=settings,
            )
        assert result.dry_run is True


# ---------------------------------------------------------------------------
# Channel routing
# ---------------------------------------------------------------------------

class TestChannelRouting:
    def _run(self, channel: str, snap=None):
        settings = _make_settings()
        if snap is None:
            snap = _make_snap()
        mock_tg = MagicMock()
        mock_tg.return_value.is_configured.return_value = True
        mock_tg.return_value.send_messages.return_value = True
        mock_em = MagicMock()
        mock_em.return_value.is_configured.return_value = True
        mock_em.return_value.send_rich.return_value = True
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger", mock_tg), \
             patch("app.briefing.snapshot_replay.EmailMessenger", mock_em):
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel=channel,
                settings=settings,
            )
        return result, mock_tg, mock_em

    def test_channel_all_attempts_both(self):
        result, mock_tg, mock_em = self._run("all")
        assert result.telegram_attempted
        assert result.email_attempted
        mock_tg.return_value.send_messages.assert_called_once()
        mock_em.return_value.send_rich.assert_called_once()

    def test_channel_telegram_skips_email(self):
        result, mock_tg, mock_em = self._run("telegram")
        assert result.telegram_attempted
        assert not result.email_attempted
        mock_em.return_value.send_rich.assert_not_called()

    def test_channel_email_skips_telegram(self):
        result, mock_tg, mock_em = self._run("email")
        assert not result.telegram_attempted
        assert result.email_attempted
        mock_tg.return_value.send_messages.assert_not_called()


# ---------------------------------------------------------------------------
# Banner application
# ---------------------------------------------------------------------------

class TestBannerApplication:
    def _call(self, no_banner: bool, snap=None):
        settings = _make_settings()
        if snap is None:
            snap = _make_snap()
        mock_tg = MagicMock()
        mock_tg.return_value.is_configured.return_value = True
        mock_tg.return_value.send_messages.return_value = True
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger", mock_tg), \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            mock_em.return_value.is_configured.return_value = True
            mock_em.return_value.send_rich.return_value = True
            replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="telegram",
                no_banner=no_banner,
                settings=settings,
            )
        return mock_tg.return_value.send_messages.call_args[0][0][0]

    def test_banner_prepended_by_default(self):
        text = self._call(no_banner=False)
        assert "SNAPSHOT REPLAY" in text
        assert text.index("SNAPSHOT REPLAY") < text.index("Good morning")

    def test_no_banner_flag_omits_prefix(self):
        text = self._call(no_banner=True)
        assert "SNAPSHOT REPLAY" not in text
        assert text.startswith("Good morning")

    def test_email_subject_prefixed_with_replay(self):
        settings = _make_settings()
        snap = _make_snap()
        mock_em = MagicMock()
        mock_em.return_value.is_configured.return_value = True
        mock_em.return_value.send_rich.return_value = True
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger") as mock_tg, \
             patch("app.briefing.snapshot_replay.EmailMessenger", mock_em):
            mock_tg.return_value.is_configured.return_value = True
            mock_tg.return_value.send_messages.return_value = True
            replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="email",
                no_banner=False,
                settings=settings,
            )
        _, kwargs = mock_em.return_value.send_rich.call_args
        assert kwargs["subject"].startswith("[REPLAY]")


# ---------------------------------------------------------------------------
# Missing content in snapshot
# ---------------------------------------------------------------------------

class TestMissingContent:
    def test_missing_telegram_text_skips_telegram_without_error(self):
        settings = _make_settings()
        snap = _make_snap(telegram_text="")
        mock_tg = MagicMock()
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger", mock_tg), \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            mock_em.return_value.is_configured.return_value = True
            mock_em.return_value.send_rich.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="telegram",
                settings=settings,
            )
        assert result.telegram_attempted
        assert not result.telegram_ok
        assert "no Telegram text" in result.telegram_reason
        mock_tg.return_value.send_messages.assert_not_called()

    def test_missing_email_content_skips_email_without_error(self):
        settings = _make_settings()
        snap = _make_snap(email_plain_text="", email_html="")
        mock_em = MagicMock()
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.EmailMessenger", mock_em), \
             patch("app.briefing.snapshot_replay.TelegramMessenger") as mock_tg:
            mock_tg.return_value.is_configured.return_value = True
            mock_tg.return_value.send_messages.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="email",
                settings=settings,
            )
        assert result.email_attempted
        assert not result.email_ok
        assert "no email content" in result.email_reason
        mock_em.return_value.send_rich.assert_not_called()


# ---------------------------------------------------------------------------
# Delivery failure captured in result
# ---------------------------------------------------------------------------

class TestDeliveryFailure:
    def test_telegram_failure_sets_error(self):
        settings = _make_settings()
        snap = _make_snap()
        mock_tg = MagicMock()
        mock_tg.return_value.is_configured.return_value = True
        mock_tg.return_value.send_messages.return_value = False
        mock_tg.return_value.last_error = "connection refused"
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.TelegramMessenger", mock_tg), \
             patch("app.briefing.snapshot_replay.EmailMessenger") as mock_em:
            mock_em.return_value.is_configured.return_value = True
            mock_em.return_value.send_rich.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="telegram",
                settings=settings,
            )
        assert not result.telegram_ok
        assert "connection refused" in result.telegram_reason
        assert result.errors

    def test_email_failure_sets_error(self):
        settings = _make_settings()
        snap = _make_snap()
        mock_em = MagicMock()
        mock_em.return_value.is_configured.return_value = True
        mock_em.return_value.send_rich.return_value = False
        mock_em.return_value.last_error = "auth failed"
        with patch("app.briefing.snapshot_replay.get_session_snapshot", return_value=snap), \
             patch("app.briefing.snapshot_replay.init_db"), \
             patch("app.briefing.snapshot_replay.EmailMessenger", mock_em), \
             patch("app.briefing.snapshot_replay.TelegramMessenger") as mock_tg:
            mock_tg.return_value.is_configured.return_value = True
            mock_tg.return_value.send_messages.return_value = True
            result = replay_snapshot(
                profile_name="default_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                channel="email",
                settings=settings,
            )
        assert not result.email_ok
        assert "auth failed" in result.email_reason
        assert result.errors
