"""Session templates by region with non-breaking EMEA defaults."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.personalization.user_profile import UserProfile


@dataclass(frozen=True)
class SessionTemplateItem:
    key: str
    label: str
    window_start: time
    window_end: time
    focus: str
    open_now_hint: str
    recently_closed_hint: str
    opening_next_hint: str
    intensity_level: str = "standard"

    @property
    def window_str(self) -> str:
        return f"{self.window_start.strftime('%H:%M')}-{self.window_end.strftime('%H:%M')}"


SESSION_TEMPLATES: dict[str, tuple[SessionTemplateItem, ...]] = {
    "emea_global": (
        SessionTemplateItem("morning", "Morning Briefing", time(6, 0), time(10, 30), "Asia overnight + Europe open + US prior close", "Europe cash and US futures context", "US prior session", "US cash later today", "standard"),
        SessionTemplateItem("europe_midday", "Europe Midday Check", time(10, 30), time(13, 30), "Europe session + US pre-market build", "Europe cash + UK cash", "Asia cash", "US pre-market", "standard"),
        SessionTemplateItem("us_pre_open", "US Pre-Open Setup", time(13, 30), time(15, 30), "US setup + Europe handoff", "Europe cash + US futures", "Asia cash", "US cash", "standard"),
        SessionTemplateItem("us_intraday_risk", "US Intraday Risk Check", time(15, 30), time(17, 30), "US open reaction + Europe/US overlap", "US cash + Europe cash", "Asia cash", "Europe close", "active"),
        SessionTemplateItem("into_close", "Into Close Update", time(17, 30), time(22, 0), "Late US session + Europe close", "US cash", "Europe cash", "US close", "active"),
        SessionTemplateItem("closing_wrap", "Closing Wrap / Next-Day Setup", time(22, 0), time(23, 59), "US close + next-day setup", "After-hours / futures context", "US cash prior session", "APAC cash next session", "light"),
    ),
    "americas_global": (
        SessionTemplateItem("americas_morning", "Morning Global Setup", time(6, 0), time(8, 30), "Asia close + Europe live + US pre-market", "Europe cash + US futures", "Asia cash", "US pre-market", "standard"),
        SessionTemplateItem("us_pre_open", "US Pre-Open Setup", time(8, 30), time(9, 30), "US futures + opening triggers", "US pre-market", "Asia cash", "US cash", "active"),
        SessionTemplateItem("us_intraday_risk", "US Intraday Risk Check", time(9, 30), time(12, 0), "US open reaction + breadth confirmation", "US cash", "Asia cash", "US midday", "active"),
        SessionTemplateItem("americas_midday", "Midday / Europe Close Check", time(12, 0), time(14, 30), "US midday + Europe close", "US cash", "Europe cash", "Power hour", "standard"),
        SessionTemplateItem("into_close", "Power Hour / Into Close", time(14, 30), time(16, 0), "Late US positioning", "US cash", "Europe cash", "US close", "active"),
        SessionTemplateItem("closing_wrap", "Closing Wrap / Next-Day Setup", time(16, 0), time(18, 0), "US close + next global setup", "After-hours futures", "US cash", "APAC open", "light"),
    ),
    "apac_global": (
        SessionTemplateItem("apac_morning", "APAC Morning Briefing", time(7, 0), time(9, 30), "US close + Europe close + APAC open", "APAC cash", "US cash + Europe cash", "APAC midday", "standard"),
        SessionTemplateItem("apac_midday", "APAC Midday Check", time(11, 30), time(13, 30), "Asia session breadth + regional leadership", "APAC cash", "US cash + Europe cash", "Europe open", "standard"),
        SessionTemplateItem("europe_open_handoff", "Europe Open Handoff", time(15, 30), time(17, 30), "APAC close + Europe open", "Europe cash", "APAC cash", "US pre-market", "standard"),
        SessionTemplateItem("us_pre_open_digest", "US Pre-Open Digest", time(20, 30), time(22, 30), "Europe handoff + US futures", "US pre-market", "Europe cash", "US cash", "light"),
        SessionTemplateItem("optional_us_open_alert", "Optional US Open Alert", time(22, 30), time(0, 30), "US cash open reaction", "US cash", "Europe cash", "APAC next open", "light"),
    ),
    "apac_australia": (
        SessionTemplateItem("australia_morning", "Australia/APAC Morning Briefing", time(7, 0), time(9, 30), "US close + Australia open", "ASX + APAC cash", "US cash + Europe cash", "APAC midday", "standard"),
        SessionTemplateItem("apac_midday", "APAC Midday Check", time(11, 30), time(13, 30), "Asia session breadth + regional leadership", "APAC cash", "US cash + Europe cash", "Europe open", "standard"),
        SessionTemplateItem("europe_open_handoff", "Europe Open Handoff", time(17, 0), time(19, 0), "APAC close + Europe open", "Europe cash", "APAC cash", "US pre-market", "standard"),
        SessionTemplateItem("us_pre_open_digest", "US Pre-Open Digest", time(21, 30), time(23, 30), "Europe handoff + US futures", "US pre-market", "Europe cash", "US cash", "light"),
    ),
}


def get_template_items(template_name: str) -> tuple[SessionTemplateItem, ...]:
    return SESSION_TEMPLATES.get(template_name, SESSION_TEMPLATES["emea_global"])


def get_session_template_for_profile(profile: "UserProfile") -> tuple[str, tuple[SessionTemplateItem, ...]]:
    override = (getattr(profile, "session_template_override", "") or "").strip().lower()
    if override:
        name = override
    else:
        focus_region = (getattr(profile, "market_focus_region", "") or "").strip().lower()
        if focus_region in {"americas", "us", "north_america"}:
            name = "americas_global"
        elif focus_region in {"apac", "asia"}:
            au_nz = (getattr(profile, "sub_region", "") or "").strip().lower() in {"australia/nz", "australia", "new zealand"}
            name = "apac_australia" if au_nz else "apac_global"
        elif focus_region in {"emea", "europe", "middle_east", "africa"}:
            name = "emea_global"
        else:
            name = (getattr(profile, "session_template", "") or "emea_global").strip().lower()
    if name not in SESSION_TEMPLATES:
        name = "emea_global"
    return name, SESSION_TEMPLATES[name]
