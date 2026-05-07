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
    assert ctx["open_now_source"] == "computed"


def test_market_clock_sources_and_examples_madrid_weekday() -> None:
    profile = UserProfile(timezone="Europe/Madrid", session_template="emea_global")
    items = {item.key: item for item in get_template_items("emea_global")}
    checks = [
        ("morning", datetime(2026, 5, 6, 6, 30, tzinfo=ZoneInfo("Europe/Madrid"))),
        ("europe_midday", datetime(2026, 5, 6, 11, 0, tzinfo=ZoneInfo("Europe/Madrid"))),
        ("us_intraday_risk", datetime(2026, 5, 6, 16, 0, tzinfo=ZoneInfo("Europe/Madrid"))),
        ("closing_wrap", datetime(2026, 5, 6, 22, 15, tzinfo=ZoneInfo("Europe/Madrid"))),
    ]
    for key, when in checks:
        ctx = build_market_clock_context(profile, when, items[key])
        assert ctx["open_now"]
        assert ctx["opening_next"]
        assert ctx["open_now_source"] in {"computed", "fallback"}
        assert ctx["recently_closed_source"] in {"computed", "fallback"}
        assert ctx["opening_next_source"] in {"computed", "fallback"}


def test_market_clock_weekend_is_context_fallback_friendly() -> None:
    profile = UserProfile(timezone="Europe/Madrid", session_template="emea_global")
    morning = get_template_items("emea_global")[0]
    saturday = datetime(2026, 5, 9, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    ctx = build_market_clock_context(profile, saturday, morning)
    assert ctx["open_now_source"] == "fallback"
    assert ctx["recently_closed_source"] == "fallback"
