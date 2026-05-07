from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MorningBriefing


def test_non_morning_empty_global_news_mentions_no_material_new_headlines() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc),
        session_key="us_intraday_risk",
        session_title="US Intraday Risk Check",
        what_changed_header="WHAT CHANGED SINCE US PRE-OPEN SETUP",
        global_news=[],
    )
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "No material new headlines since Us Pre-Open Setup" in text
