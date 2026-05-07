from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.briefing.market_clock import build_market_clock_context
from app.briefing.session_templates import get_template_items
from app.personalization.user_profile import UserProfile


def test_emea_template_keeps_existing_keys_and_windows() -> None:
    items = get_template_items("emea_global")
    assert [i.key for i in items] == [
        "morning",
        "europe_midday",
        "us_pre_open",
        "us_intraday_risk",
        "into_close",
        "closing_wrap",
    ]
    assert items[0].window_str == "06:00-10:30"
    assert items[1].window_str == "10:30-13:30"
    assert items[2].window_str == "13:30-15:30"
    assert items[3].window_str == "15:30-17:30"
    assert items[4].window_str == "17:30-22:00"
    assert items[5].window_start.hour == 22


def test_market_clock_madrid_morning_has_europe_open_and_focus() -> None:
    profile = UserProfile(timezone="Europe/Madrid")
    morning_item = get_template_items("emea_global")[0]
    now_utc = datetime(2026, 5, 7, 7, 10, tzinfo=timezone.utc)
    ctx = build_market_clock_context(profile, now_utc.astimezone(ZoneInfo("Europe/Madrid")), morning_item)
    assert "focus" in ctx and ctx["focus"]
    assert any("Europe cash" in x for x in ctx["open_now"])
