from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from app.briefing import day_replay as day_replay_mod
from app.cli import cli
from app.db.models import SentMessage
from app.db.session import get_session
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.delivery import EmailRenderResult
from app.schemas.events import MacroDataPoint, QuoteData
from app.settings import Settings


class _StubGenerator:
    def __init__(self, **kwargs):
        pass

    def generate(self, *, session_key: str = "morning", session_title: str = "Morning Briefing") -> MorningBriefing:
        return MorningBriefing(
            generated_at=datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
            session_key=session_key,
            session_title=session_title,
            market_setup=MarketSetup(
                index_quotes=[
                    QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7200, change=5, change_percent=0.07),
                    QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=25000, change=25, change_percent=0.10),
                    QuoteData(symbol="^VIX", display_name="VIX", current_price=17.4, change=0.4, change_percent=2.4),
                ],
                macro_quotes=[
                    QuoteData(symbol="CL=F", display_name="WTI Crude Oil", current_price=103.0, change=0.2, change_percent=0.2),
                    QuoteData(symbol="BZ=F", display_name="Brent Crude", current_price=106.0, change=0.8, change_percent=0.8),
                    QuoteData(symbol="GC=F", display_name="Gold", current_price=4600.0, change=-5.0, change_percent=-0.1),
                    QuoteData(symbol="^TNX", display_name="10Y US Treasury Yield", current_price=4.40, change=0.04, change_percent=0.9),
                ],
            ),
            macro_context=[
                MacroDataPoint(series_id="DGS2", name="US 2Y Treasury Yield", value=3.88, change=0.01),
                MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.40, change=0.04),
                MacroDataPoint(series_id="DGS30", name="US 30Y Treasury Yield", value=4.98, change=0.02),
            ],
            watchlist_quotes=[
                QuoteData(symbol="NVDA", display_name="NVDA", current_price=900.0, change=-5, change_percent=-0.55),
                QuoteData(symbol="MSFT", display_name="MSFT", current_price=420.0, change=3, change_percent=0.72),
            ],
            portfolio_quotes=[
                QuoteData(symbol="QQQ", display_name="QQQ", current_price=500.0, change=-0.6, change_percent=-0.12),
            ],
            morning_chart_bundle={
                "meta": {
                    "selected_charts": ["global_relative_performance", "cross_asset_impulse_strip"],
                    "suppressed_charts": ["event_linked_annotated_trend"],
                    "required_charts_missing": [],
                }
            },
        )


class _StubTelegramFormatter:
    def __init__(self, timezone_name: str = "UTC"):
        self.timezone_name = timezone_name

    def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
        return [f"<b>{briefing.session_title}</b>\nREAD: replay test"]


class _StubEmailFormatter:
    def __init__(self, timezone_name: str = "UTC"):
        self.timezone_name = timezone_name

    def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
        return EmailRenderResult(
            subject=f"{briefing.session_title} | Mon 04 May",
            plain_text=f"{briefing.session_title}\nREAD: replay test",
            html_body=f"<html><body><b>{briefing.session_title}</b></body></html>",
            inline_assets=[],
        )


def _stub_profile() -> UserProfile:
    return UserProfile(name="default_user", timezone="Europe/Madrid", delivery={"session_mode": "active"})


def _patch_replay_dependencies(monkeypatch):
    monkeypatch.setattr(day_replay_mod, "MorningBriefingGenerator", _StubGenerator)
    monkeypatch.setattr(day_replay_mod, "TelegramFormatter", _StubTelegramFormatter)
    monkeypatch.setattr(day_replay_mod, "EmailFormatter", _StubEmailFormatter)
    monkeypatch.setattr(day_replay_mod, "load_user_profile", lambda settings: _stub_profile())
    monkeypatch.setattr(day_replay_mod, "load_sector_universe", lambda settings: object())
    monkeypatch.setattr(day_replay_mod, "_utcnow", lambda: datetime(2026, 5, 4, 19, 27, tzinfo=timezone.utc))


def test_day_replay_2127_marks_closing_wrap_future(monkeypatch):
    _patch_replay_dependencies(monkeypatch)
    settings = Settings(dry_run=True, show_output=False)
    result = day_replay_mod.run_day_replay(settings, replay_date="today", until="now")
    keys = [row.session_key for row in result.sessions if row.eligible]
    assert keys == [
        "morning",
        "europe_midday",
        "us_pre_open",
        "us_intraday_risk",
        "into_close",
    ]
    closing = next(row for row in result.sessions if row.session_key == "closing_wrap")
    assert closing.eligible is False
    assert "not eligible until 22:00" in closing.future_reason


def test_day_replay_show_output_prints_preview(monkeypatch, capsys):
    _patch_replay_dependencies(monkeypatch)
    settings = Settings(dry_run=True, show_output=True)
    day_replay_mod.run_day_replay(
        settings,
        replay_date="today",
        until="now",
        respect_materiality=False,
        show_output=True,
    )
    captured = capsys.readouterr().out
    assert "[OUTPUT] day_replay:morning" in captured
    assert "REPLAY MODE: session slot simulated at" in captured


def test_day_replay_send_test_labels_without_sentmessage_writes(monkeypatch, validation_isolated_db):
    _patch_replay_dependencies(monkeypatch)
    sent_telegram: list[list[str]] = []
    sent_email: list[dict[str, str]] = []

    class _FakeTelegramMessenger:
        def __init__(self, settings):
            self.settings = settings

        def is_configured(self) -> bool:
            return True

        def send_messages(self, messages: list[str], parse_mode: str = "HTML") -> bool:
            sent_telegram.append(list(messages))
            return True

    class _FakeEmailMessenger:
        def __init__(self, settings):
            self.settings = settings

        def is_configured(self) -> bool:
            return True

        def send_rich(self, *, subject: str, plain_text: str, html_body: str, inline_assets=None) -> bool:
            sent_email.append({"subject": subject, "plain_text": plain_text, "html_body": html_body})
            return True

    monkeypatch.setattr(day_replay_mod, "TelegramMessenger", _FakeTelegramMessenger)
    monkeypatch.setattr(day_replay_mod, "EmailMessenger", _FakeEmailMessenger)

    settings = Settings(
        dry_run=True,
        show_output=False,
        database_url=f"sqlite:///unused_{datetime.now().timestamp()}.db",
    )
    day_replay_mod.run_day_replay(
        settings,
        replay_date="today",
        until="now",
        send_test="telegram,email",
        respect_materiality=False,
    )
    assert sent_telegram
    assert sent_email
    assert "[TEST DAY REPLAY - NOT LIVE]" in sent_telegram[0][0]
    assert "REPLAY MODE: session slot simulated at" in sent_telegram[0][0]
    assert sent_email[0]["subject"].startswith("[TEST Replay]")

    with get_session() as session:
        assert session.query(SentMessage).count() == 0


def test_day_replay_full_day_includes_closing_wrap(monkeypatch):
    _patch_replay_dependencies(monkeypatch)
    settings = Settings(dry_run=True, show_output=False)
    result = day_replay_mod.run_day_replay(
        settings,
        replay_date="today",
        until="close",
        respect_materiality=False,
        force_all=True,
    )
    closing = next(row for row in result.sessions if row.session_key == "closing_wrap")
    assert closing.eligible is True


def test_cli_day_replay_dispatches(monkeypatch):
    called: dict = {}

    def _fake_run(settings, **kwargs):
        called["kwargs"] = kwargs
        return None

    monkeypatch.setattr("app.briefing.day_replay.run_day_replay", _fake_run)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--dry-run",
            "day-replay",
            "--date",
            "2026-05-04",
            "--until",
            "now",
            "--send-test",
            "telegram,email",
        ],
    )
    assert result.exit_code == 0, result.output
    assert called["kwargs"]["replay_date"] == "2026-05-04"
    assert called["kwargs"]["send_test"] == "telegram,email"
