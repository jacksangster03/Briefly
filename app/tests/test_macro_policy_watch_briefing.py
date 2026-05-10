from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.briefing.macro_policy_service import build_macro_policy_watch_summary
from app.schemas.briefings import MorningBriefing


def test_macro_policy_watch_summary_unavailable():
    summary = build_macro_policy_watch_summary({})
    assert "unavailable" in summary.lower()


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
    assert "Regional: UK partial" in summary
    assert "not a forecast" in summary.lower()


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
