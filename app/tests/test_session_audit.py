from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

from app.main import run_session_audit
from app.personalization.user_profile import UserProfile
from app.settings import Settings


def _profile() -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        market_region="EMEA",
        sub_region="Eurozone",
        session_template="emea_global",
    )


def test_session_audit_local_only_does_not_call_providers(monkeypatch):
    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr("app.main.load_user_profile", lambda *_args, **_kwargs: _profile())

    @contextmanager
    def _fake_db():
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        yield db

    monkeypatch.setattr("app.main.get_session", _fake_db)
    monkeypatch.setattr("app.main.get_session_snapshot", lambda *_a, **_k: None)

    called = {"build_services": 0}

    def _boom(*_a, **_k):
        called["build_services"] += 1
        raise AssertionError("provider services should not be created in local-only session-audit")

    monkeypatch.setattr("app.main._build_services", _boom)
    out = run_session_audit(Settings(), target_date_str="today", profile_name="default_user", live_check=False)
    assert "SESSION AUDIT | local-only | no provider calls" in out
    assert called["build_services"] == 0


def test_session_audit_live_check_builds_services(monkeypatch):
    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr("app.main.load_user_profile", lambda *_args, **_kwargs: _profile())

    @contextmanager
    def _fake_db():
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        yield db

    monkeypatch.setattr("app.main.get_session", _fake_db)
    monkeypatch.setattr("app.main.get_session_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.load_sector_universe", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr("app.main._build_services", lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()))
    fake_gen = MagicMock()
    fake_briefing = MagicMock()
    fake_briefing.global_news = []
    fake_briefing.top_themes = []
    fake_briefing.quote_freshness = {}
    fake_gen.generate.return_value = fake_briefing
    monkeypatch.setattr("app.main.MorningBriefingGenerator", lambda **_k: fake_gen)
    monkeypatch.setattr(
        "app.main.split_news_since_previous",
        lambda **_k: MagicMock(new_news_items=[], repeated_news_items=[], carried_forward_items=[]),
    )
    monkeypatch.setattr("app.main.split_events_against_previous_snapshot", lambda **_k: ([], [], [], ""))

    out = run_session_audit(Settings(), target_date_str="today", profile_name="default_user", live_check=True)
    assert "provider-backed live-check" in out

