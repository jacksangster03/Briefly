from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.db.models import SentMessage, SessionSendState
from app.db.session import get_session
from app.main import run_morning_briefing
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import EmailRenderResult
from app.settings import Settings


class _StubGenerator:
    def __init__(self, *args, **kwargs):
        pass

    def generate(self, *, session_key: str = "morning", session_title: str = "Morning Briefing") -> MorningBriefing:
        return MorningBriefing(
            generated_at=datetime(2026, 5, 5, 8, 46, tzinfo=timezone.utc),
            session_key=session_key,
            session_title=session_title,
            data_freshness={"Market Prices": "2026-05-05 08:46 CEST"},
        )


class _StubTelegramFormatter:
    def __init__(self, *args, **kwargs):
        pass

    def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
        return [f"{briefing.session_title}: telegram"]


class _StubEmailFormatter:
    def __init__(self, *args, **kwargs):
        pass

    def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
        return EmailRenderResult(
            subject=briefing.session_title,
            plain_text=f"{briefing.session_title}: email",
            html_body=f"<p>{briefing.session_title}: email</p>",
            inline_assets=[],
        )


class _StubLLMRenderer:
    def __init__(self, *args, **kwargs):
        pass

    def render_morning(self, *, deterministic_email, **kwargs):
        return SimpleNamespace(active_email=deterministic_email, shadow_preview=None)


def test_duplicate_morning_send_creates_single_live_sent_message(monkeypatch, validation_isolated_db):
    send_calls: list[str] = []

    class _FakeEmailMessenger:
        name = "email"

        def __init__(self, settings):
            self.settings = settings
            self.dry_run = False
            self.last_error = ""

        def is_configured(self) -> bool:
            return True

        def send_rich(self, *, subject: str, plain_text: str, html_body: str, inline_assets=None) -> bool:
            send_calls.append(subject)
            return True

    monkeypatch.setattr("app.main.load_user_profile", lambda settings: UserProfile(name="default_user"))
    monkeypatch.setattr("app.main.load_sector_universe", lambda settings: object())
    monkeypatch.setattr("app.main._build_services", lambda settings: (object(), object(), object()))
    monkeypatch.setattr("app.main.MorningBriefingGenerator", _StubGenerator)
    monkeypatch.setattr("app.main.TelegramFormatter", _StubTelegramFormatter)
    monkeypatch.setattr("app.main.EmailFormatter", _StubEmailFormatter)
    monkeypatch.setattr("app.main.LLMEmailRenderer", _StubLLMRenderer)
    monkeypatch.setattr("app.main.EmailMessenger", _FakeEmailMessenger)
    monkeypatch.setattr("app.main.load_previous_snapshot", lambda **kwargs: (None, {}))
    monkeypatch.setattr("app.main.persist_snapshot", lambda **kwargs: None)
    monkeypatch.setattr("app.main.record_sent_events", lambda *args, **kwargs: None)

    settings = Settings(
        dry_run=False,
        delivery_channel="email",
        email_user="sender@example.com",
        email_password="secret",
        email_to="recipient@example.com",
    )

    run_morning_briefing(settings, auto_route_session=False, session_override="morning")
    run_morning_briefing(settings, auto_route_session=False, session_override="morning")

    assert send_calls == ["Morning Briefing"]

    with get_session() as session:
        sent_rows = session.query(SentMessage).all()
        assert len(sent_rows) == 1
        assert sent_rows[0].message_type == "session_brief:morning"
        state_rows = session.query(SessionSendState).all()
        assert len(state_rows) == 1
        assert state_rows[0].message_type == "session_brief:morning"
        assert state_rows[0].success is True
        assert state_rows[0].channel == "email"
        assert state_rows[0].profile_name == "default_user"


def test_scheduler_warns_when_lock_already_held(monkeypatch, tmp_path):
    from app import scheduler as scheduler_mod

    settings = Settings(data_dir=str(tmp_path))
    warnings: list[str] = []

    class _FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 8

        @staticmethod
        def flock(_fd, _flags):
            raise OSError("locked")

    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(scheduler_mod, "fcntl", _FakeFcntl)
    monkeypatch.setattr(scheduler_mod.logger, "warning", lambda message, *args: warnings.append(message % args))
    monkeypatch.setattr(scheduler_mod, "build_scheduler", lambda _settings: (_ for _ in ()).throw(AssertionError("should not build scheduler")))

    scheduler_mod.start_scheduler()

    assert warnings
    assert "scheduler may already be running" in warnings[0].lower()
