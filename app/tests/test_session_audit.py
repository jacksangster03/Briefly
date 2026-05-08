from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock

from app.main import run_session_audit
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.db.models import NewsClassifierLabel
from app.db.session import get_session


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


def _classifier_event(
    title: str,
    *,
    story_type: str = "breaking_market_moving",
    freshness: str = "new",
    suppress_reason: str = "",
    update_status: str = "new",
    final_score: float = 0.8,
) -> NormalisedEvent:
    evt = NormalisedEvent(
        title=title,
        summary="summary",
        event_type="headline",
        final_score=final_score,
        factual_confidence_score=0.82,
        update_status=update_status,
    )
    evt.raw_data = {
        "news_story_type": story_type,
        "news_freshness_state": freshness,
        "news_suppress_reason": suppress_reason,
        "news_classifier_confidence": 0.82,
        "breaking_label": "LATE DISCOVERY" if freshness == "late_discovery" else "CONTEXT",
    }
    return evt


def test_session_audit_live_check_prints_classifier_summary_counts(monkeypatch):
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
    e1 = _classifier_event("Catalyst A", story_type="guidance_change", freshness="new")
    e2 = _classifier_event("Commentary B", story_type="commentary_valuation", freshness="stale", suppress_reason="valuation_commentary_without_catalyst")
    e3 = _classifier_event("Polling C", story_type="ignore", freshness="new", suppress_reason="generic_polling_with_etf_proxy_only")
    fake_briefing.events_pool = [e1, e2, e3]
    fake_briefing.global_news = [e1]
    fake_briefing.top_themes = []
    fake_briefing.watchlist_events = []
    fake_briefing.portfolio_focus = []
    fake_briefing.quote_freshness = {}
    fake_gen.generate.return_value = fake_briefing
    monkeypatch.setattr("app.main.MorningBriefingGenerator", lambda **_k: fake_gen)
    monkeypatch.setattr(
        "app.main.split_news_since_previous",
        lambda **_k: MagicMock(new_news_items=[], repeated_news_items=[], carried_forward_items=[]),
    )
    monkeypatch.setattr("app.main.split_events_against_previous_snapshot", lambda **_k: ([], [], [], ""))

    out = run_session_audit(Settings(), target_date_str="today", profile_name="default_user", live_check=True)
    assert "story_type_counts=" in out
    assert "freshness_state_counts=" in out
    assert "suppression_reason_counts=" in out
    assert "guidance_change:" in out
    assert "weak_etf_proxy:" in out
    assert "dataset_rows_persisted=" in out
    assert "ml_shadow_classifier=" in out


def test_session_audit_classifier_details_flag_controls_examples(monkeypatch):
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
    e1 = _classifier_event("Included A", story_type="breaking_market_moving", freshness="new")
    e2 = _classifier_event("Suppressed B", story_type="low_signal", freshness="stale", suppress_reason="low_signal_format")
    fake_briefing.events_pool = [e1, e2]
    fake_briefing.global_news = [e1]
    fake_briefing.top_themes = []
    fake_briefing.watchlist_events = []
    fake_briefing.portfolio_focus = []
    fake_briefing.quote_freshness = {}
    fake_gen.generate.return_value = fake_briefing
    monkeypatch.setattr("app.main.MorningBriefingGenerator", lambda **_k: fake_gen)
    monkeypatch.setattr(
        "app.main.split_news_since_previous",
        lambda **_k: MagicMock(new_news_items=[], repeated_news_items=[], carried_forward_items=[]),
    )
    monkeypatch.setattr("app.main.split_events_against_previous_snapshot", lambda **_k: ([], [], [], ""))

    out_default = run_session_audit(Settings(), target_date_str="today", profile_name="default_user", live_check=True)
    assert "included_examples:" not in out_default
    out_details = run_session_audit(
        Settings(),
        target_date_str="today",
        profile_name="default_user",
        live_check=True,
        classifier_details=True,
    )
    assert "included_examples:" in out_details
    assert "suppressed_examples:" in out_details
    assert "break_eligible=" in out_details


def test_session_audit_live_check_persists_dataset_rows(validation_isolated_db, monkeypatch):
    monkeypatch.setattr("app.main.load_user_profile", lambda *_args, **_kwargs: _profile())
    monkeypatch.setattr("app.main.load_sector_universe", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr("app.main._build_services", lambda *_a, **_k: (MagicMock(), MagicMock(), MagicMock()))
    fake_gen = MagicMock()
    fake_briefing = MagicMock()
    e = _classifier_event("Persisted row example", story_type="guidance_change", freshness="new")
    fake_briefing.events_pool = [e]
    fake_briefing.global_news = [e]
    fake_briefing.top_themes = []
    fake_briefing.watchlist_events = []
    fake_briefing.portfolio_focus = []
    fake_briefing.quote_freshness = {}
    fake_gen.generate.return_value = fake_briefing
    monkeypatch.setattr("app.main.MorningBriefingGenerator", lambda **_k: fake_gen)
    monkeypatch.setattr(
        "app.main.split_news_since_previous",
        lambda **_k: MagicMock(new_news_items=[], repeated_news_items=[], carried_forward_items=[]),
    )
    monkeypatch.setattr("app.main.split_events_against_previous_snapshot", lambda **_k: ([], [], [], ""))

    out = run_session_audit(Settings(), target_date_str="today", profile_name="default_user", live_check=True)
    assert "dataset_rows_persisted=" in out
    with get_session() as db:
        rows = db.query(NewsClassifierLabel).all()
        assert rows
