from __future__ import annotations

from app.personalization.user_profile import UserProfile
from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MorningBriefing
from app.verticals.shadow_summary import build_vertical_shadow_lines


def _profile(enabled: bool, sessions=None) -> UserProfile:
    overrides = {}
    if enabled:
        overrides["verticals.include_in_briefing"] = True
        overrides["verticals.briefing_sessions"] = sessions or ["morning"]
        overrides["verticals.geopolitics.mode"] = "watch"
        overrides["verticals.ai_tech.mode"] = "watch"
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        healthcare={"enabled": True},
        preference_overrides=overrides,
    )


def test_vertical_shadow_disabled_by_default():
    lines = build_vertical_shadow_lines(profile=_profile(enabled=False), session_key="morning")
    assert lines == []


def test_vertical_shadow_enabled_for_morning():
    lines = build_vertical_shadow_lines(profile=_profile(enabled=True), session_key="morning")
    assert isinstance(lines, list)


def test_vertical_shadow_intraday_not_rendered_without_session_opt_in():
    lines = build_vertical_shadow_lines(profile=_profile(enabled=True, sessions=["morning"]), session_key="us_intraday_risk")
    assert lines == []


def test_default_off_briefing_output_unchanged_for_vertical_shadow():
    formatter = TelegramFormatter(timezone_name="Europe/Madrid")
    briefing = MorningBriefing(session_key="morning", session_title="Morning Briefing")
    parts = formatter.format_morning_briefing(briefing)
    text = "\n".join(parts)
    assert "VERTICAL INTELLIGENCE (SHADOW)" not in text
