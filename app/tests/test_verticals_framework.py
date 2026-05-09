from __future__ import annotations

from click.testing import CliRunner

from app.cli import cli
from app.healthcare.section_builder import build_healthcare_section
from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent
from app.verticals.engine import build_vertical_section, verticals_status_for_profile
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
