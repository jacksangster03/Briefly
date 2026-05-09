from __future__ import annotations

from click.testing import CliRunner
from fastapi.testclient import TestClient

from app.cli import cli
from app.db.models import VerticalRunDiagnostics
from app.db.session import get_session
from app.healthcare.section_builder import build_healthcare_section
from app.personalization.user_profile import UserProfile
from app.web.app import create_web_app
from app.web.control_plane_service import apply_preference_updates, build_profile_state
from app.schemas.events import NormalisedEvent
from app.verticals.engine import (
    build_vertical_section,
    vertical_diagnostics_history,
    verticals_status_for_profile,
)
from app.verticals.registry import load_vertical_plugins


def _profile(*, enabled: bool) -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        watchlist_primary=["LLY", "NVO"],
        healthcare={
            "enabled": enabled,
            "max_items_morning": 4,
            "max_items_intraday": 3,
            "breaking_alerts": True,
            "themes": ["GLP-1", "peptides"],
            "tickers": ["LLY", "NVO"],
            "assets": ["tirzepatide", "semaglutide"],
            "minimum_severity_morning": "medium",
            "minimum_severity_intraday": "high",
            "minimum_severity_breaking": "critical",
        },
    )


def _event() -> NormalisedEvent:
    return NormalisedEvent(
        title="FDA approves Eli Lilly obesity therapy",
        summary="The FDA approved tirzepatide for obesity treatment.",
        event_type="news",
        tickers=["LLY"],
        source="newsapi",
    )


def test_vertical_registry_loads_healthcare_plugin():
    plugins = load_vertical_plugins()
    assert "healthcare" in plugins
    assert plugins["healthcare"].display_name.lower().startswith("healthcare")


def test_vertical_engine_off_mode_returns_no_section():
    profile = _profile(enabled=False)
    section = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=[_event()],
    )
    assert section is None


def test_vertical_engine_healthcare_enabled_matches_existing_behavior():
    profile = _profile(enabled=True)
    events = [_event()]
    wrapped = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=events,
    )
    direct = build_healthcare_section(
        profile=profile,
        session_key="morning",
        events=events,
        verbose_when_empty=False,
    )
    assert (wrapped is None) == (direct is None)
    if wrapped and direct:
        assert wrapped.title == direct.title
        assert [item.title for item in wrapped.items] == [item.title for item in direct.items]


def test_vertical_plugin_failure_is_non_blocking(monkeypatch):
    profile = _profile(enabled=True)

    class _FailingPlugin:
        vertical_key = "healthcare"
        display_name = "Healthcare / Biotech"

        def resolve_mode(self, *, profile):
            return "active"

        def activation_state(self, *, profile, mode, session_key, candidate_events):
            return True, "active_mode"

        def build_section(self, *, profile, session_key, candidate_events):
            raise RuntimeError("boom")

        def audit_metrics(self, *, profile, session_key, candidate_events=None):
            return {"vertical_key": "healthcare", "candidate_count": len(candidate_events or [])}

        def breaking_candidates(self, *, profile, events):
            return []

    monkeypatch.setattr("app.verticals.engine.registered_verticals", lambda: {"healthcare": _FailingPlugin()})
    section = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=[_event()],
    )
    assert section is None
    rows = verticals_status_for_profile(profile=profile)
    assert rows
    assert "boom" in (rows[0]["plugin_error"] or "")


def test_verticals_status_cli_works_enabled_and_disabled(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr("app.cli.init_db", lambda: None)
    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *_a, **_k: _profile(enabled=True))
    out_enabled = runner.invoke(cli, ["verticals-status", "--profile", "default_user"])
    assert out_enabled.exit_code == 0
    assert "healthcare" in out_enabled.output.lower()

    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *_a, **_k: _profile(enabled=False))
    out_disabled = runner.invoke(cli, ["verticals-status", "--profile", "default_user"])
    assert out_disabled.exit_code == 0
    assert "mode=off" in out_disabled.output.lower()


def test_verticals_page_renders(validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)
    response = client.get("/ui/briefing/verticals?profile=default_user")
    assert response.status_code == 200
    html = response.text
    assert "Vertical Intelligence" in html
    assert "Healthcare / Biotech" in html
    assert "Mode" in html
    assert "Healthcare Diagnostics" in html
    assert "How Activation Works" in html
    assert "Planned Verticals" in html
    assert "AI / Semiconductors" in html
    assert "Energy / Geopolitics" in html
    assert "Defence / Aerospace" in html


def test_verticals_healthcare_mode_preference_save_read(validation_isolated_db, validation_test_settings):
    apply_preference_updates(
        "default_user",
        {
            "verticals.healthcare.mode": "watch",
            "verticals.healthcare.priority": "high",
            "verticals.healthcare.max_items.morning": 5,
            "verticals.healthcare.max_items.intraday": 2,
            "verticals.healthcare.min_severity": "high",
            "verticals.healthcare.portfolio_weight_threshold": 7.5,
            "verticals.healthcare.watchlist_count_threshold": 3,
        },
    )
    state = build_profile_state(validation_test_settings, "default_user")
    eff = state["effective"]["verticals"]["healthcare"]
    assert eff["mode"] == "watch"
    assert eff["priority"] == "high"
    assert eff["max_items_morning"] == 5
    assert eff["max_items_intraday"] == 2
    assert eff["min_severity"] == "high"
    assert abs(float(eff["portfolio_weight_threshold"]) - 7.5) < 1e-9
    assert eff["watchlist_count_threshold"] == 3


def test_verticals_invalid_mode_rejected(validation_isolated_db):
    try:
        apply_preference_updates("default_user", {"verticals.healthcare.mode": "ultra"})
    except ValueError as exc:
        assert "Unsupported vertical mode" in str(exc)
        return
    assert False, "Expected ValueError for invalid vertical mode"


def test_vertical_diagnostics_render_off_and_active(monkeypatch, validation_test_settings):
    app = create_web_app(validation_test_settings)
    client = TestClient(app)

    monkeypatch.setattr("app.verticals.engine.vertical_mode_for_profile", lambda **_k: "off")
    resp_off = client.get("/ui/briefing/verticals?profile=default_user")
    assert resp_off.status_code == 200
    assert "Status:" in resp_off.text

    monkeypatch.setattr("app.verticals.engine.vertical_mode_for_profile", lambda **_k: "active")
    resp_active = client.get("/ui/briefing/verticals?profile=default_user")
    assert resp_active.status_code == 200
    assert "Mode:" in resp_active.text


def test_vertical_diagnostics_persisted_best_effort(validation_isolated_db):
    profile = _profile(enabled=True)
    _ = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=[_event()],
    )
    with get_session() as db:
        row = (
            db.query(VerticalRunDiagnostics)
            .filter(VerticalRunDiagnostics.profile_name == profile.name)
            .first()
        )
        assert row is not None
        assert row.vertical_key == "healthcare"
        assert row.session_key == "morning"


def test_verticals_status_verbose_and_history_cli(validation_isolated_db, monkeypatch):
    profile = _profile(enabled=True)
    _ = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=[_event()],
    )

    runner = CliRunner()
    monkeypatch.setattr("app.cli.init_db", lambda: None)
    monkeypatch.setattr("app.personalization.user_profile.load_user_profile", lambda *_a, **_k: profile)
    out_verbose = runner.invoke(cli, ["verticals-status", "--verbose"])
    assert out_verbose.exit_code == 0
    assert "last_session=" in out_verbose.output
    assert "source_status=" in out_verbose.output

    out_hist = runner.invoke(cli, ["verticals-history", "--to", "today", "--limit", "10"])
    assert out_hist.exit_code == 0
    assert "VERTICALS HISTORY" in out_hist.output
    assert "healthcare" in out_hist.output.lower()


def test_vertical_diagnostics_history_function(validation_isolated_db):
    profile = _profile(enabled=True)
    _ = build_vertical_section(
        profile=profile,
        vertical_key="healthcare",
        session_key="morning",
        candidate_events=[_event()],
    )
    rows = vertical_diagnostics_history(profile_name=profile.name, limit=5)
    assert rows
    assert rows[0]["vertical_key"] == "healthcare"
