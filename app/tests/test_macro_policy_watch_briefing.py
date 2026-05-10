from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.briefing.macro_policy_service import (
    build_macro_policy_watch_summary,
    select_macro_watch_regions,
    should_include_macro_policy_watch,
)
from app.personalization.preferences_service import normalize_preference_value
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing


def test_macro_policy_watch_summary_unavailable():
    summary = build_macro_policy_watch_summary({})
    assert "unavailable" in summary.lower() or "partial data" in summary.lower()


def test_macro_policy_watch_helper_with_partial_payload():
    summary = build_macro_policy_watch_summary(
        {
            "policy_signals": {
                "fed_bias": {"label": "hold"},
                "ecb_bias": {"label": "cut_leaning", "confidence": "low"},
                "inflation_pressure": {"label": "reaccelerating"},
                "labour_pressure": {"label": "balanced"},
                "rates_pressure": {"label": "tightening"},
                "regions": {
                    "uk": {"status": "partial"},
                    "spain": {"status": "partial"},
                    "japan": {"status": "unavailable"},
                    "china": {"status": "unavailable"},
                },
                "portfolio_implications": ["watch duration and valuation-sensitive growth"],
            }
        }
    )
    assert "MACRO POLICY WATCH" in summary
    assert "Fed: hold" in summary
    assert "Regional:" in summary
    assert "not a forecast" in summary.lower()
    assert len(summary.splitlines()) <= 6


def test_macro_policy_watch_summary_us_pre_open_compact():
    summary = build_macro_policy_watch_summary(
        {
            "policy_signals": {
                "fed_bias": {"label": "hold"},
                "ecb_bias": {"label": "cut_leaning", "confidence": "low"},
                "inflation_pressure": {"label": "reaccelerating"},
                "labour_pressure": {"label": "balanced"},
                "rates_pressure": {"label": "tightening"},
                "regions": {},
                "portfolio_implications": ["watch duration/growth sensitivity"],
            }
        },
        profile=UserProfile(timezone="Europe/Madrid"),
        session_key="us_pre_open",
    )
    lines = summary.splitlines()
    assert lines[0] == "MACRO POLICY WATCH"
    assert len(lines) <= 4
    assert "Fed:" in lines[1]


def test_default_briefing_output_unchanged_without_macro_watch():
    briefing = MorningBriefing(generated_at=datetime.now(timezone.utc))
    formatter = TelegramFormatter("Europe/Madrid")
    output = "\n".join(formatter.format_morning_briefing(briefing))
    assert "MACRO POLICY WATCH" not in output


def test_macro_policy_watch_only_when_present():
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        macro_policy_watch="Macro Policy Watch: mixed curve; neutral impulse.",
    )
    formatter = TelegramFormatter("Europe/Madrid")
    output = "\n".join(formatter.format_morning_briefing(briefing))
    assert "MACRO POLICY WATCH" in output


def test_macro_policy_watch_renders_in_email_when_present():
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        macro_policy_watch="MACRO POLICY WATCH\nFed: hold · ECB: hold",
    )
    html = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing).html_body
    assert "MACRO POLICY WATCH" in html
    assert "Fed: hold" in html


def test_macro_watch_sessions_normalization():
    normalized = normalize_preference_value("briefing.macro_policy_watch_sessions", "morning,us_pre_open")
    assert normalized == ["morning", "us_pre_open"]


def test_should_include_macro_policy_watch_defaults_morning_only():
    profile = UserProfile()
    profile.delivery["include_macro_policy_watch"] = True
    assert should_include_macro_policy_watch(profile=profile, session_key="morning") is True
    assert should_include_macro_policy_watch(profile=profile, session_key="us_pre_open") is False
    assert should_include_macro_policy_watch(profile=profile, session_key="europe_midday") is False
    assert should_include_macro_policy_watch(profile=profile, session_key="saturday_weekend_briefing") is False


def test_should_include_macro_policy_watch_us_pre_open_opt_in():
    profile = UserProfile()
    profile.delivery["include_macro_policy_watch"] = True
    profile.delivery["macro_policy_watch_sessions"] = ["morning", "us_pre_open"]
    assert should_include_macro_policy_watch(profile=profile, session_key="us_pre_open") is True


def test_region_selection_includes_spain_for_spain_profile():
    profile = UserProfile(country="Spain", home_region="spain", timezone="Europe/Madrid")
    regions = select_macro_watch_regions(
        policy_signals={
            "regions": {
                "us": {"status": "ok"},
                "eurozone": {"status": "partial"},
                "spain": {"status": "partial"},
                "uk": {"status": "unavailable"},
                "japan": {"status": "unavailable"},
                "china": {"status": "unavailable"},
            }
        },
        profile=profile,
        session_key="morning",
    )
    assert "spain" in regions


def test_region_selection_avoids_noisy_unavailable_placeholders():
    profile = UserProfile(country="Spain", home_region="spain", timezone="Europe/Madrid")
    summary = build_macro_policy_watch_summary(
        {
            "policy_signals": {
                "fed_bias": {"label": "hold"},
                "ecb_bias": {"label": "hold", "confidence": "low"},
                "inflation_pressure": {"label": "sticky"},
                "labour_pressure": {"label": "balanced"},
                "rates_pressure": {"label": "neutral"},
                "regions": {
                    "us": {"status": "ok"},
                    "eurozone": {"status": "ok"},
                    "spain": {"status": "partial"},
                    "uk": {"status": "unavailable"},
                    "japan": {"status": "unavailable"},
                    "china": {"status": "unavailable"},
                },
                "portfolio_implications": ["keep sizing disciplined"],
            }
        },
        profile=profile,
        session_key="morning",
    )
    assert "Japan unavailable" not in summary
    assert "China unavailable" not in summary
