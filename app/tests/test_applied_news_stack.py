from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MarketSetup, MorningBriefing


def test_applied_news_stack_renders_why_and_assets():
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 19, 6, 0, tzinfo=timezone.utc),
        session_key="morning",
        market_setup=MarketSetup(),
        applied_news_stack=[
            {
                "bucket": "macro_rates",
                "headline": "US 10Y reprices higher on inflation surprise",
                "why_it_matters": "Rates context can reset valuation-sensitive growth and duration risk.",
                "affected_assets": ["QQQ", "RUT"],
                "source_count": 3,
                "price_confirmation": "confirmed",
            }
        ],
    )
    rendered = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "APPLIED NEWS STACK" in rendered
    assert "Why it matters:" in rendered
    assert "Affected: QQQ, RUT" in rendered
    assert "Signal: confirmed" in rendered


def test_applied_news_stack_absent_when_empty():
    briefing = MorningBriefing(generated_at=datetime(2026, 5, 19, 6, 0, tzinfo=timezone.utc), session_key="morning")
    rendered = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "APPLIED NEWS STACK" not in rendered

