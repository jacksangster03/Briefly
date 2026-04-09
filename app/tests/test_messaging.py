"""Tests for messaging delivery channels."""

import pytest
from unittest.mock import patch, MagicMock

from app.messaging.telegram import TelegramMessenger
from app.settings import Settings


def _dry_settings() -> Settings:
    """Settings with dry_run enabled for testing."""
    return Settings(
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        dry_run=True,
    )


class TestTelegramMessenger:
    def test_is_configured_with_both_fields(self):
        settings = _dry_settings()
        tg = TelegramMessenger(settings)
        assert tg.is_configured()

    def test_not_configured_without_token(self):
        settings = Settings(telegram_bot_token="", telegram_chat_id="12345", dry_run=True)
        tg = TelegramMessenger(settings)
        assert not tg.is_configured()

    def test_dry_run_sends_successfully(self, capsys):
        settings = _dry_settings()
        tg = TelegramMessenger(settings)
        result = tg.send("Test message")
        assert result is True
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    def test_send_messages_multiple(self, capsys):
        settings = _dry_settings()
        tg = TelegramMessenger(settings)
        result = tg.send_messages(["Part 1", "Part 2"])
        assert result is True

    @patch("app.messaging.telegram.requests.post")
    def test_real_send_success(self, mock_post):
        settings = Settings(
            telegram_bot_token="test-token",
            telegram_chat_id="12345",
            dry_run=False,
        )
        mock_post.return_value = MagicMock(ok=True)
        tg = TelegramMessenger(settings)
        result = tg.send("Test message")
        assert result is True
        mock_post.assert_called_once()

    @patch("app.messaging.telegram.requests.post")
    def test_real_send_failure(self, mock_post):
        settings = Settings(
            telegram_bot_token="test-token",
            telegram_chat_id="12345",
            dry_run=False,
        )
        mock_resp = MagicMock(ok=False)
        mock_resp.json.return_value = {"description": "Bad Request"}
        mock_post.return_value = mock_resp
        tg = TelegramMessenger(settings)
        result = tg.send("Test message")
        assert result is False
