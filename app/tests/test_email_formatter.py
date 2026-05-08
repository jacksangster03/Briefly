from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


def test_morning_email_uses_overnight_header_not_generic_what_changed() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 8, 6, 30, tzinfo=timezone.utc),
        session_key="morning",
        session_title="Morning Briefing",
        what_changed_lines=["WTI: -4.13% -> -5.61% (sharply lower (-1.48%))"],
    )
    result = EmailFormatter("Europe/Madrid").format_morning_briefing(briefing)
    assert "OVERNIGHT / PRIOR SESSION CHANGE" in result.html_body
    assert "WHAT CHANGED</div>" not in result.html_body


def test_market_snapshot_treasury_yield_uses_bp_not_pct_change() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 8, 14, 0, tzinfo=timezone.utc),
        session_key="us_intraday_risk",
        session_title="US Intraday Risk Check",
        market_setup=MarketSetup(
            macro_quotes=[
                QuoteData(
                    symbol="^TNX",
                    display_name="10Y US Treasury Yield",
                    current_price=4.39,
                    change=0.036,
                    change_percent=0.83,
                )
            ]
        ),
    )
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "10Y US Treasury Yield 4.39% (+4 bp)" in text
    assert "(+0.83%)" not in text

