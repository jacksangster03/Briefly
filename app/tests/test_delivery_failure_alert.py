"""Tests for Phase 9.2: delivery failure alert on scheduler-triggered sends."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import DeliveryFailureAlertState, SentMessage
from app.db.session import get_session
from app.main import (
    _ALERT_COOLDOWN_MINUTES,
    _ALERT_MESSAGE_TYPE,
    _delivery_alert_hash,
    _delivery_failed_channels_hash,
    _should_emit_delivery_failure_alert,
    _send_delivery_failure_alert,
)
from app.settings import Settings


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _settings(dry_run: bool = False) -> Settings:
    s = Settings()
    s.dry_run = dry_run
    return s


def _alert_kwargs(**overrides):
    base = dict(
        settings=_settings(),
        profile_name="default",
        session_key="morning",
        session_title="Morning Brief",
        local_date=date(2026, 5, 6),
        generated_at_str="2026-05-06 07:30",
        timezone_name="Europe/Madrid",
        channel_status={"telegram": "failed", "email": "skipped"},
        channel_reason={"telegram": "ConnectionError", "email": "not configured"},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

class TestDeliveryAlertHash:
    def test_deterministic(self):
        h1 = _delivery_alert_hash("default", "morning", date(2026, 5, 6))
        h2 = _delivery_alert_hash("default", "morning", date(2026, 5, 6))
        assert h1 == h2

    def test_differs_by_session(self):
        h1 = _delivery_alert_hash("default", "morning", date(2026, 5, 6))
        h2 = _delivery_alert_hash("default", "closing_wrap", date(2026, 5, 6))
        assert h1 != h2

    def test_differs_by_date(self):
        h1 = _delivery_alert_hash("default", "morning", date(2026, 5, 6))
        h2 = _delivery_alert_hash("default", "morning", date(2026, 5, 7))
        assert h1 != h2

    def test_differs_by_profile(self):
        h1 = _delivery_alert_hash("alice", "morning", date(2026, 5, 6))
        h2 = _delivery_alert_hash("bob", "morning", date(2026, 5, 6))
        assert h1 != h2

    def test_length_16(self):
        h = _delivery_alert_hash("default", "morning", date(2026, 5, 6))
        assert len(h) == 16


# ---------------------------------------------------------------------------
# _send_delivery_failure_alert
# ---------------------------------------------------------------------------

class TestSendDeliveryFailureAlert:
    def _mock_db_session(self):
        mock_sess = MagicMock()

        def _make_cm():
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=mock_sess)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        return _make_cm, mock_sess

    def test_no_failed_channels_returns_immediately(self):
        kwargs = _alert_kwargs(
            channel_status={"telegram": "sent", "email": "sent"},
            channel_reason={},
        )
        with patch("app.main.get_session") as mock_gs:
            _send_delivery_failure_alert(**kwargs)
        mock_gs.assert_not_called()

    def test_all_skipped_returns_immediately(self):
        kwargs = _alert_kwargs(
            channel_status={"telegram": "skipped", "email": "skipped"},
        )
        with patch("app.main.get_session") as mock_gs:
            _send_delivery_failure_alert(**kwargs)
        mock_gs.assert_not_called()

    def test_cooldown_suppresses_duplicate_alert(self):
        make_cm, mock_sess = self._mock_db_session()
        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(False, "cooldown")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger") as mock_tg:
            _send_delivery_failure_alert(**kwargs)
        mock_tg.assert_not_called()

    def test_no_cooldown_match_sends_alert(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        mock_messenger.send_messages.assert_called_once()
        sent_text = mock_messenger.send_messages.call_args[0][0][0]
        assert "DELIVERY FAILURE" in sent_text
        assert "morning" in sent_text
        assert "telegram" in sent_text
        assert "ConnectionError" in sent_text

    def test_telegram_not_configured_skips_send(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = False

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        mock_messenger.send_messages.assert_not_called()

    def test_alert_records_sentmessage_on_success(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        # Inspect the SentMessage instance passed to db_sess.add()
        added = mock_sess.add.call_args[0][0]
        assert added.message_type == _ALERT_MESSAGE_TYPE
        assert added.success is True
        assert added.channel == "telegram"

    def test_alert_records_sentmessage_on_failure(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = False

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        added = mock_sess.add.call_args[0][0]
        assert added.success is False

    def test_exception_does_not_propagate(self):
        """Alert must be fully non-blocking."""
        with patch("app.main.get_session", side_effect=RuntimeError("db gone")):
            # Must not raise
            _send_delivery_failure_alert(**_alert_kwargs())

    def test_alert_text_includes_sent_channels(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs(
            channel_status={"telegram": "failed", "email": "sent"},
            channel_reason={"telegram": "timeout"},
        )
        with patch("app.main._authoritative_success_channels", return_value={"email"}), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        sent_text = mock_messenger.send_messages.call_args[0][0][0]
        assert "email: delivered" in sent_text
        assert "telegram" in sent_text
        assert "timeout" in sent_text

    def test_cooldown_uses_correct_message_type(self):
        """Alert sends when no recent alert is found in the cooldown window."""
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        assert mock_messenger.send_messages.called

    def test_alert_hash_in_sentmessage_content_hash(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs()
        expected_hash = "hashx"

        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        added = mock_sess.add.call_args[0][0]
        assert added.content_hash == expected_hash

    def test_partial_failure_only_lists_failed_in_failed_section(self):
        """A partial send (telegram ok, email failed) should only list email in Failed."""
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs(
            channel_status={"telegram": "sent", "email": "failed"},
            channel_reason={"email": "SMTP auth error"},
        )
        with patch("app.main._authoritative_success_channels", return_value={"telegram"}), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        sent_text = mock_messenger.send_messages.call_args[0][0][0]
        assert "email: SMTP auth error" in sent_text
        assert "telegram: delivered" in sent_text

    def test_alert_includes_resend_cli_hint(self):
        make_cm, mock_sess = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True

        kwargs = _alert_kwargs()
        with patch("app.main._authoritative_success_channels", return_value=set()), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)

        sent_text = mock_messenger.send_messages.call_args[0][0][0]
        assert "session-send" in sent_text or "cli" in sent_text.lower()

    def test_stale_failure_suppressed_if_all_channels_already_successful(self):
        make_cm, _ = self._mock_db_session()
        kwargs = _alert_kwargs(channel_status={"telegram": "failed", "email": "failed"})
        with patch("app.main._authoritative_success_channels", return_value={"telegram", "email"}), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger") as mock_tg:
            _send_delivery_failure_alert(**kwargs)
        mock_tg.assert_not_called()

    def test_partial_success_alerts_only_unresolved_channel(self):
        make_cm, _ = self._mock_db_session()
        mock_messenger = MagicMock()
        mock_messenger.is_configured.return_value = True
        mock_messenger.send_messages.return_value = True
        kwargs = _alert_kwargs(
            channel_status={"telegram": "failed", "email": "failed"},
            channel_reason={"telegram": "timeout", "email": "smtp fail"},
        )
        with patch("app.main._authoritative_success_channels", return_value={"telegram"}), \
             patch("app.main._should_emit_delivery_failure_alert", return_value=(True, "hashx")), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger", return_value=mock_messenger):
            _send_delivery_failure_alert(**kwargs)
        txt = mock_messenger.send_messages.call_args[0][0][0]
        assert "email: smtp fail" in txt
        assert "telegram: timeout" not in txt
        assert "telegram: delivered" in txt

    def test_no_retry_instruction_when_no_unresolved_failure(self):
        make_cm, _ = self._mock_db_session()
        kwargs = _alert_kwargs(channel_status={"telegram": "failed"})
        with patch("app.main._authoritative_success_channels", return_value={"telegram"}), \
             patch("app.main.get_session", side_effect=make_cm), \
             patch("app.main.TelegramMessenger") as mock_tg:
            _send_delivery_failure_alert(**kwargs)
        mock_tg.assert_not_called()


def test_should_emit_suppresses_on_recent_failed_alert_attempt(validation_isolated_db):
    now = datetime.now(timezone.utc)
    local_day = date(2026, 5, 9)
    failed_hash = _delivery_failed_channels_hash(
        profile_name="default",
        session_key="morning",
        local_date=local_day,
        failed_channels=["telegram", "email"],
    )
    with get_session() as s:
        s.add(
            SentMessage(
                message_type=_ALERT_MESSAGE_TYPE,
                channel="telegram",
                event_ids=["alert:test"],
                content_preview="delivery fail",
                content_hash=failed_hash,
                sent_at=now - timedelta(minutes=15),
                success=False,
                error_message="transport failed",
            )
        )
    should, reason = _should_emit_delivery_failure_alert(
        profile_name="default",
        session_key="morning",
        local_date=local_day,
        unresolved_failed_channels=["telegram", "email"],
        now_utc=now,
    )
    assert should is False
    assert reason == "cooldown"


def test_send_failure_alert_records_state_even_when_alert_send_fails(validation_isolated_db):
    class _FailingTelegram:
        def __init__(self, _settings):
            self.last_error = "simulated alert transport fail"

        def is_configured(self):
            return True

        def send_messages(self, _messages):
            return False

    kwargs = _alert_kwargs(
        channel_status={"telegram": "failed", "email": "failed"},
        channel_reason={"telegram": "tg fail", "email": "smtp fail"},
    )
    with patch("app.main.TelegramMessenger", _FailingTelegram), \
         patch("app.main._authoritative_success_channels", return_value=set()):
        _send_delivery_failure_alert(**kwargs)

    with get_session() as s:
        state = s.query(DeliveryFailureAlertState).filter(
            DeliveryFailureAlertState.profile_name == "default",
            DeliveryFailureAlertState.session_key == "morning",
            DeliveryFailureAlertState.local_date == date(2026, 5, 6),
        ).first()
        assert state is not None
        assert state.alert_count >= 1


def test_send_failure_alert_dedupes_within_cooldown(validation_isolated_db):
    calls: list[str] = []

    class _OkTelegram:
        def __init__(self, _settings):
            self.last_error = ""

        def is_configured(self):
            return True

        def send_messages(self, messages):
            calls.append(messages[0])
            return True

    kwargs = _alert_kwargs(
        channel_status={"telegram": "failed", "email": "failed"},
        channel_reason={"telegram": "tg fail", "email": "smtp fail"},
    )
    with patch("app.main.TelegramMessenger", _OkTelegram), \
         patch("app.main._authoritative_success_channels", return_value=set()):
        _send_delivery_failure_alert(**kwargs)
        _send_delivery_failure_alert(**kwargs)
    assert len(calls) == 1
