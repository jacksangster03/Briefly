from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.macro_policy_service import build_macro_policy_watch_summary
from app.schemas.briefings import MorningBriefing


def test_macro_policy_watch_summary_unavailable():
    summary = build_macro_policy_watch_summary({})
    assert "unavailable" in summary.lower()


def test_macro_policy_watch_helper_with_partial_payload():
    summary = build_macro_policy_watch_summary(
        {
            "rates_yield_curve_panel": {"curve_shape": "normal curve", "rate_impulse": "higher-rate pressure"},
            "inflation_tracker": {"series": {"us_cpi": {"value": 318.0, "change": 0.2}}},
            "labour_tracker": {"series": {"us_unemployment_rate": {"value": 4.1, "change": 0.0}}},
            "macro_catalyst_calendar": {"events": [{"title": "US CPI", "date": "2026-06-12"}]},
        }
    )
    assert "Macro Policy Watch" in summary
    assert "US CPI" in summary


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
