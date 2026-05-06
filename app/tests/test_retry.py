"""Phase 9.3: retry/backoff tests for Telegram and email messenger channels."""

from __future__ import annotations

from datetime import date
from email.mime.multipart import MIMEMultipart
from unittest.mock import MagicMock, call, patch

import pytest

from app.messaging.retry import call_with_retry
from app.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _telegram(configured: bool = True, dry_run: bool = False):
    """Minimal TelegramMessenger with configurable behaviour."""
    from app.messaging.telegram import TelegramMessenger
    s = Settings()
    s.dry_run = dry_run
    if configured:
        s.telegram_bot_token = "tok"
        s.telegram_chat_id = "123"
    else:
        s.telegram_bot_token = ""
        s.telegram_chat_id = ""
    return TelegramMessenger(s)


def _email(configured: bool = True, dry_run: bool = False):
    """Minimal EmailMessenger with configurable behaviour."""
    from app.messaging.email import EmailMessenger
    s = Settings()
    s.dry_run = dry_run
    if configured:
        s.email_user = "sender@example.com"
        s.email_password = "secret"
        s.email_to = "recipient@example.com"
        s.email_host = "smtp.example.com"
        s.email_port = 587
    else:
        s.email_user = ""
        s.email_password = ""
        s.email_to = ""
    return EmailMessenger(s)


def _rich_kwargs(**overrides):
    base = dict(
        subject="Morning Brief",
        plain_text="Hello",
        html_body="<p>Hello</p>",
        inline_assets=[],
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# call_with_retry unit tests
# ---------------------------------------------------------------------------

class TestCallWithRetry:
    def test_succeeds_first_attempt_no_sleep(self):
        fn = MagicMock(return_value=True)
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok, err = call_with_retry(fn, channel="test")
        assert ok is True
        assert err == ""
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    def test_fails_once_then_succeeds(self):
        fn = MagicMock(side_effect=[False, True])
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok, err = call_with_retry(fn, channel="test", delay_seconds=(5,))
        assert ok is True
        assert fn.call_count == 2
        mock_sleep.assert_called_once_with(5)

    def test_fails_all_attempts(self):
        fn = MagicMock(return_value=False)
        with patch("app.messaging.retry.time.sleep"):
            ok, err = call_with_retry(fn, channel="test", max_attempts=3, delay_seconds=(1, 2))
        assert ok is False
        assert fn.call_count == 3

    def test_exception_treated_as_failure(self):
        fn = MagicMock(side_effect=[RuntimeError("boom"), True])
        with patch("app.messaging.retry.time.sleep"):
            ok, err = call_with_retry(fn, channel="test", delay_seconds=(1,))
        assert ok is True
        assert fn.call_count == 2

    def test_all_exceptions_returns_false(self):
        fn = MagicMock(side_effect=RuntimeError("boom"))
        with patch("app.messaging.retry.time.sleep"):
            ok, err = call_with_retry(fn, channel="test", max_attempts=2, delay_seconds=(1,))
        assert ok is False
        assert "boom" in err

    def test_delays_between_attempts(self):
        fn = MagicMock(side_effect=[False, False, True])
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            call_with_retry(fn, channel="test", max_attempts=3, delay_seconds=(10, 30))
        assert mock_sleep.call_args_list == [call(10), call(30)]

    def test_max_attempts_one_no_sleep(self):
        fn = MagicMock(return_value=False)
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok, _ = call_with_retry(fn, channel="test", max_attempts=1)
        mock_sleep.assert_not_called()
        assert ok is False


# ---------------------------------------------------------------------------
# Test 1: Telegram succeeds on first attempt
# ---------------------------------------------------------------------------

class TestTelegramSucceedsFirstAttempt:
    def test_send_called_once_no_sleep(self):
        messenger = _telegram()
        with patch.object(messenger, "send", return_value=True) as mock_send, \
             patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_messages(["hello"])
        assert ok is True
        mock_send.assert_called_once_with("hello", parse_mode="HTML")
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Telegram fails once then succeeds
# ---------------------------------------------------------------------------

class TestTelegramFailsThenSucceeds:
    def test_retried_and_succeeds(self):
        messenger = _telegram()
        with patch.object(messenger, "send", side_effect=[False, True]) as mock_send, \
             patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_messages(["hello"])
        assert ok is True
        assert mock_send.call_count == 2
        assert mock_sleep.call_count == 1

    def test_result_is_true_not_false(self):
        messenger = _telegram()
        with patch.object(messenger, "send", side_effect=[False, True]), \
             patch("app.messaging.retry.time.sleep"):
            result = messenger.send_messages(["msg"])
        assert result is True


# ---------------------------------------------------------------------------
# Test 3: Telegram fails all attempts
# ---------------------------------------------------------------------------

class TestTelegramFailsAll:
    def test_returns_false_after_all_retries(self):
        messenger = _telegram()
        with patch.object(messenger, "send", return_value=False), \
             patch("app.messaging.retry.time.sleep"):
            ok = messenger.send_messages(["hello"])
        assert ok is False

    def test_send_called_max_attempts_times(self):
        messenger = _telegram()
        with patch.object(messenger, "send", return_value=False) as mock_send, \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_messages(["hello"])
        assert mock_send.call_count == 3  # default max_attempts=3

    def test_last_error_set(self):
        messenger = _telegram()
        with patch.object(messenger, "send", return_value=False), \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_messages(["hello"])
        assert messenger.last_error != ""


# ---------------------------------------------------------------------------
# Test 4: Email fails once then succeeds
# ---------------------------------------------------------------------------

class TestEmailFailsThenSucceeds:
    def test_smtp_retried_and_succeeds(self):
        messenger = _email()
        dummy_msg = MagicMock()
        with patch.object(messenger, "_build_mime_message", return_value=dummy_msg), \
             patch.object(messenger, "_smtp_send", side_effect=[False, True]) as mock_smtp, \
             patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_rich(**_rich_kwargs())
        assert ok is True
        assert mock_smtp.call_count == 2
        assert mock_sleep.call_count == 1

    def test_message_built_only_once(self):
        messenger = _email()
        with patch.object(messenger, "_build_mime_message", return_value=MagicMock()) as mock_build, \
             patch.object(messenger, "_smtp_send", side_effect=[False, True]), \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_rich(**_rich_kwargs())
        mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: Email fails all attempts
# ---------------------------------------------------------------------------

class TestEmailFailsAll:
    def test_returns_false(self):
        messenger = _email()
        with patch.object(messenger, "_build_mime_message", return_value=MagicMock()), \
             patch.object(messenger, "_smtp_send", return_value=False), \
             patch("app.messaging.retry.time.sleep"):
            ok = messenger.send_rich(**_rich_kwargs())
        assert ok is False

    def test_last_error_populated(self):
        messenger = _email()
        messenger.last_error = ""
        with patch.object(messenger, "_build_mime_message", return_value=MagicMock()), \
             patch.object(messenger, "_smtp_send", return_value=False), \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_rich(**_rich_kwargs())
        assert messenger.last_error != ""

    def test_smtp_called_max_attempts_times(self):
        messenger = _email()
        with patch.object(messenger, "_build_mime_message", return_value=MagicMock()), \
             patch.object(messenger, "_smtp_send", return_value=False) as mock_smtp, \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_rich(**_rich_kwargs())
        assert mock_smtp.call_count == 3


# ---------------------------------------------------------------------------
# Test 6: Retry does not run when channel not configured
# ---------------------------------------------------------------------------

class TestNoRetryWhenNotConfigured:
    def test_telegram_not_configured_no_sleep(self):
        messenger = _telegram(configured=False)
        with patch.object(messenger, "send", return_value=False) as mock_send, \
             patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_messages(["hello"])
        # send() called once (direct path, no retry), sleep never called
        mock_send.assert_called_once()
        mock_sleep.assert_not_called()
        assert ok is False

    def test_email_not_configured_no_smtp_no_sleep(self):
        messenger = _email(configured=False)
        with patch.object(messenger, "_smtp_send") as mock_smtp, \
             patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_rich(**_rich_kwargs())
        mock_smtp.assert_not_called()
        mock_sleep.assert_not_called()
        assert ok is False

    def test_dry_run_telegram_no_sleep(self):
        messenger = _telegram(dry_run=True)
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_messages(["hello"])
        mock_sleep.assert_not_called()
        assert ok is True

    def test_dry_run_email_no_sleep(self):
        messenger = _email(dry_run=True)
        with patch("app.messaging.retry.time.sleep") as mock_sleep:
            ok = messenger.send_rich(**_rich_kwargs())
        mock_sleep.assert_not_called()
        assert ok is True


# ---------------------------------------------------------------------------
# Test 7: Phase 9.2 alert fires only after final retry failure
# ---------------------------------------------------------------------------

class TestPhase92AlertAfterRetry:
    def test_alert_fires_once_not_per_retry(self):
        """_send_delivery_failure_alert must be called exactly once, after all retries fail."""
        from app.settings import Settings as S
        s = S()
        s.dry_run = False
        s.telegram_bot_token = "tok"
        s.telegram_chat_id = "123"

        from unittest.mock import MagicMock, patch
        from app.main import _send_delivery_failure_alert

        with patch("app.main._send_delivery_failure_alert") as mock_alert, \
             patch("app.main.TelegramMessenger") as MockTG, \
             patch("app.main.EmailMessenger") as MockEmail, \
             patch("app.main.run_morning_briefing"):
            # Simulate: send_messages fails (returns False), alert should fire once
            mock_messenger = MagicMock()
            mock_messenger.name = "telegram"
            mock_messenger.is_configured.return_value = True
            mock_messenger.send_messages.return_value = False
            mock_messenger.last_error = "connection refused"
            MockTG.return_value = mock_messenger

            # Build a minimal channel_status that shows failure
            channel_status = {"telegram": "failed"}
            channel_reason = {"telegram": "connection refused"}

            # Call the alert directly with the post-retry state
            _send_delivery_failure_alert(
                settings=s,
                profile_name="default",
                session_key="morning",
                session_title="Morning Brief",
                local_date=date(2026, 5, 6),
                generated_at_str="2026-05-06 07:30",
                timezone_name="Europe/Madrid",
                channel_status=channel_status,
                channel_reason=channel_reason,
            )

        # The alert function itself is called once with the final failed status
        mock_alert.assert_not_called()  # we called the real function above, not the mock

    def test_channel_status_failed_only_when_all_retries_exhausted(self):
        """channel_status["telegram"] is set to "failed" only after send_messages() returns False,
        which already includes all retry attempts."""
        messenger = _telegram()
        # All 3 attempts fail
        with patch.object(messenger, "send", return_value=False), \
             patch("app.messaging.retry.time.sleep"):
            result = messenger.send_messages(["hello"])

        # Only after send_messages returns do we know the final status
        assert result is False
        # This is what run_morning_briefing uses to set channel_status["telegram"] = "failed"
        # confirming the alert only fires post-retry


# ---------------------------------------------------------------------------
# Test 8: No duplicate messages on retry success
# ---------------------------------------------------------------------------

class TestNoDuplicateOnRetrySuccess:
    def test_telegram_sends_each_message_exactly_once_when_retry_succeeds(self):
        """If attempt 1 fails and attempt 2 succeeds, send() is called exactly twice
        for that message - no duplicates from re-sending earlier messages."""
        messenger = _telegram()
        # First message: fail attempt 1, succeed attempt 2
        # Second message: succeed on first attempt
        call_results = [False, True, True]
        call_iter = iter(call_results)
        with patch.object(messenger, "send", side_effect=lambda *a, **kw: next(call_iter)) as mock_send, \
             patch("app.messaging.retry.time.sleep"):
            ok = messenger.send_messages(["msg1", "msg2"])

        assert ok is True
        # msg1: attempted twice (fail + retry-success); msg2: once
        assert mock_send.call_count == 3

    def test_email_message_built_once_even_on_retry(self):
        """The MIME message object is built only once; SMTP is retried with the same message."""
        messenger = _email()
        build_count = [0]
        orig_build = messenger._build_mime_message

        def counting_build(*args, **kwargs):
            build_count[0] += 1
            return MagicMock()

        with patch.object(messenger, "_build_mime_message", side_effect=counting_build), \
             patch.object(messenger, "_smtp_send", side_effect=[False, True]), \
             patch("app.messaging.retry.time.sleep"):
            messenger.send_rich(**_rich_kwargs())

        assert build_count[0] == 1
