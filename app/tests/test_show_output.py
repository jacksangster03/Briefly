"""Tests for the --show-output terminal inspection mode.

These tests pin the behavior that makes manual runs inspectable:
- `_deliver` prints rendered Telegram payloads when `settings.show_output`
  is set, regardless of dry-run state.
- HTML tags are stripped so the terminal view matches what a reader
  would see in Telegram.
- The Telegram messenger's own dry-run echo is suppressed when
  show_output is on, so the payload never prints twice.
- The CLI `--show-output` option wires the flag through to Settings.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from app.cli import cli
from app.main import _deliver, _delivery_channel_plan, _get_messengers, _print_terminal_output
from app.messaging.telegram import TelegramMessenger
from app.personalization.user_profile import UserProfile
from app.settings import Settings


def _make_messenger() -> MagicMock:
    """Stub messenger that records sends and reports success."""
    messenger = MagicMock()
    messenger.name = "stub"
    messenger.dry_run = False
    messenger.send_messages.return_value = True
    return messenger


def test_print_terminal_output_strips_html(capsys):
    _print_terminal_output(["<b>HEADLINE</b>\n<i>detail</i>"], "morning_brief")
    captured = capsys.readouterr().out
    assert "HEADLINE" in captured
    assert "detail" in captured
    assert "<b>" not in captured
    assert "<i>" not in captured
    assert "morning_brief" in captured


def test_print_terminal_output_labels_multi_part(capsys):
    _print_terminal_output(["part one", "part two"], "intraday")
    captured = capsys.readouterr().out
    assert "part 1/2" in captured
    assert "part 2/2" in captured
    assert "intraday" in captured


def test_deliver_prints_when_show_output_enabled_live(capsys):
    settings = Settings(show_output=True, dry_run=False)
    messenger = _make_messenger()
    _deliver([messenger], ["hello <b>world</b>"], "breaking", events=None, settings=settings)
    captured = capsys.readouterr().out
    assert "hello world" in captured
    assert "breaking" in captured
    # Delivery still runs once.
    messenger.send_messages.assert_called_once_with(["hello <b>world</b>"])


def test_deliver_silent_when_show_output_disabled(capsys):
    settings = Settings(show_output=False, dry_run=False)
    messenger = _make_messenger()
    _deliver([messenger], ["hello"], "breaking", events=None, settings=settings)
    captured = capsys.readouterr().out
    # No banner should appear.
    assert "[OUTPUT]" not in captured
    assert "hello" not in captured
    messenger.send_messages.assert_called_once()


def test_deliver_does_not_duplicate_sends(capsys):
    settings = Settings(show_output=True, dry_run=False)
    messenger = _make_messenger()
    _deliver([messenger], ["m1", "m2"], "morning_brief", events=None, settings=settings)
    # Exactly one send call with the full message list, even when printing.
    messenger.send_messages.assert_called_once_with(["m1", "m2"])


def test_telegram_dry_run_suppresses_echo_when_show_output_on(capsys):
    settings = Settings(
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        dry_run=True,
        show_output=True,
    )
    tg = TelegramMessenger(settings)
    result = tg.send("<b>Dry</b> content")
    assert result is True
    captured = capsys.readouterr().out
    # Messenger's own echo must stay silent; _deliver owns the print.
    assert "[DRY RUN] Telegram message" not in captured


def test_telegram_dry_run_echo_still_fires_without_show_output(capsys):
    settings = Settings(
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        dry_run=True,
        show_output=False,
    )
    tg = TelegramMessenger(settings)
    tg.send("<b>Dry</b> content")
    captured = capsys.readouterr().out
    assert "[DRY RUN] Telegram message" in captured
    assert "Dry content" in captured  # HTML-stripped


def test_deliver_prints_once_under_dry_run_with_show_output(capsys):
    """Dry-run + show-output must not print the payload twice."""
    settings = Settings(
        telegram_bot_token="test-token",
        telegram_chat_id="12345",
        dry_run=True,
        show_output=True,
    )
    tg = TelegramMessenger(settings)
    _deliver([tg], ["<b>hi</b>"], "morning_brief", events=None, settings=settings)
    captured = capsys.readouterr().out
    # Only the _deliver banner should print the payload.
    assert captured.count("hi") == 1
    assert "[OUTPUT]" in captured
    assert "[DRY RUN] Telegram message" not in captured


def test_cli_show_output_option_sets_settings(monkeypatch):
    """CLI --show-output must flip settings.show_output on."""
    captured: dict = {}

    def _fake_run(settings):
        captured["show_output"] = settings.show_output
        captured["dry_run"] = settings.dry_run

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "--show-output", "morning"])
    assert result.exit_code == 0, result.output
    assert captured["show_output"] is True
    assert captured["dry_run"] is True


def test_cli_no_show_output_keeps_flag_off(monkeypatch):
    captured: dict = {}

    def _fake_run(settings):
        captured["show_output"] = settings.show_output

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "morning"])
    assert result.exit_code == 0, result.output
    assert captured["show_output"] is False


def test_cli_email_only_sets_delivery_channel(monkeypatch):
    captured: dict = {}

    def _fake_run(settings):
        captured["delivery_channel"] = settings.delivery_channel

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "--email-only", "morning"])
    assert result.exit_code == 0, result.output
    assert captured["delivery_channel"] == "email"


def test_cli_telegram_only_sets_delivery_channel(monkeypatch):
    captured: dict = {}

    def _fake_run(settings):
        captured["delivery_channel"] = settings.delivery_channel

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "--telegram-only", "morning"])
    assert result.exit_code == 0, result.output
    assert captured["delivery_channel"] == "telegram"


def test_cli_channel_overrides_are_mutually_exclusive():
    runner = CliRunner()
    result = runner.invoke(cli, ["--email-only", "--telegram-only", "morning"])
    assert result.exit_code != 0
    assert "Use only one channel override" in result.output


def test_get_messengers_respects_email_only_setting():
    settings = Settings(
        delivery_channel="email",
        dry_run=False,
        telegram_bot_token="tg-token",
        telegram_chat_id="12345",
        email_user="user@example.com",
        email_password="secret",
        email_to="to@example.com",
    )
    profile = UserProfile()
    messengers = _get_messengers(settings, profile)
    names = [messenger.name for messenger in messengers]
    assert names == ["email"]


def test_delivery_channel_plan_marks_email_skipped_when_unconfigured():
    settings = Settings(
        delivery_channel="all",
        dry_run=False,
        telegram_bot_token="tg-token",
        telegram_chat_id="12345",
        email_user="",
        email_password="",
        email_to="",
    )
    profile = UserProfile()
    plan = _delivery_channel_plan(settings=settings, profile=profile, message_type="morning")
    email_row = next(item for item in plan if item["channel"] == "email")
    assert email_row["attempted"] is False
    assert email_row["status"] == "skipped"
    assert "not configured" in str(email_row["reason"])


def test_get_messengers_respects_telegram_only_setting():
    settings = Settings(
        delivery_channel="telegram",
        dry_run=False,
        telegram_bot_token="tg-token",
        telegram_chat_id="12345",
        email_user="user@example.com",
        email_password="secret",
        email_to="to@example.com",
    )
    profile = UserProfile()
    messengers = _get_messengers(settings, profile)
    names = [messenger.name for messenger in messengers]
    assert names == ["telegram"]


def test_preflight_reports_pass_for_dry_run_defaults():
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "preflight"])
    assert result.exit_code == 0, result.output
    assert "Briefly preflight" in result.output
    assert "result: PASS" in result.output


def test_preflight_flags_missing_openai_key_when_llm_enabled(monkeypatch):
    from app import cli as cli_module

    real_get_settings = cli_module.get_settings

    def _fake_get_settings():
        settings = real_get_settings()
        settings.enable_llm_email_render = True
        settings.openai_api_key = ""
        settings.dry_run = True
        return settings

    monkeypatch.setattr(cli_module, "get_settings", _fake_get_settings)
    runner = CliRunner()
    result = runner.invoke(cli, ["preflight"])
    assert result.exit_code == 0, result.output
    assert "[FAIL] OpenAI key" in result.output
    assert "result: FAIL" in result.output


def test_preflight_warns_when_sender_equals_recipient(monkeypatch):
    from app import cli as cli_module

    real_get_settings = cli_module.get_settings

    def _fake_get_settings():
        settings = real_get_settings()
        settings.email_user = "same@gmail.com"
        settings.email_to = "same@gmail.com"
        settings.email_password = "x"
        settings.dry_run = False
        return settings

    monkeypatch.setattr(cli_module, "get_settings", _fake_get_settings)
    runner = CliRunner()
    result = runner.invoke(cli, ["preflight"])
    assert result.exit_code == 0, result.output
    assert "Gmail may show Sent + Inbox copies in one thread" in result.output
