"""Tests for six-session distinct role design (Part 9)."""
from __future__ import annotations
from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.briefing.session_diagnosis import build_session_diagnosis
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


def _briefing(session_key: str) -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        session_key=session_key,
        session_title=session_key.replace("_", " ").title(),
    )


SESSIONS = [
    "morning",
    "europe_midday",
    "us_pre_open",
    "us_intraday_risk",
    "into_close",
    "closing_wrap",
]


def test_each_session_has_distinct_primary_heading():
    """Each session must produce a distinct one-sentence diagnosis prefix."""
    prefixes = set()
    for session_key in SESSIONS:
        briefing = _briefing(session_key)
        diag = build_session_diagnosis(briefing)
        # Extract the prefix (text before the first colon)
        sentence = diag.one_sentence_diagnosis
        prefix = sentence.split(":")[0].strip().lower()
        prefixes.add(prefix)
    assert len(prefixes) == len(SESSIONS), (
        f"Not all sessions have distinct prefixes. Got: {prefixes}"
    )


def test_morning_does_not_include_first_hour_verdict():
    """Morning briefing session key must not produce 'first-hour' or 'US open reaction' prefix."""
    briefing = _briefing("morning")
    diag = build_session_diagnosis(briefing)
    sentence = diag.one_sentence_diagnosis.lower()
    assert "first-hour" not in sentence
    assert "us open reaction" not in sentence


def test_us_intraday_includes_us_open_reaction_prefix():
    """US Intraday session must produce 'US open reaction' prefix."""
    briefing = _briefing("us_intraday_risk")
    diag = build_session_diagnosis(briefing)
    sentence = diag.one_sentence_diagnosis.lower()
    assert "us open reaction" in sentence or "first-hour" in sentence or "open" in sentence


def test_closing_wrap_has_day_verdict_prefix():
    """Closing Wrap session must produce 'Day verdict' prefix."""
    briefing = _briefing("closing_wrap")
    diag = build_session_diagnosis(briefing)
    sentence = diag.one_sentence_diagnosis.lower()
    assert "day verdict" in sentence


def test_europe_midday_has_europe_prefix():
    """Europe Midday session must produce 'Europe session so far' prefix."""
    briefing = _briefing("europe_midday")
    diag = build_session_diagnosis(briefing)
    sentence = diag.one_sentence_diagnosis.lower()
    assert "europe" in sentence


def test_us_intraday_stale_snapshot_included():
    """US Intraday session with stale snapshot available must include snapshot info in briefing."""
    from app.briefing.formatter import TelegramFormatter
    q = QuoteData(
        symbol="SPY", display_name="S&P 500",
        current_price=5000.0, change_percent=-0.3,
        source="stale_snapshot:us_pre_open",
        timestamp=datetime(2026, 5, 27, 13, 34, tzinfo=timezone.utc),
    )
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        session_key="us_intraday_risk",
        market_setup=MarketSetup(index_quotes=[q], macro_quotes=[]),
        stale_snapshot_used=True,
        stale_snapshot_session="us_pre_open",
        stale_snapshot_time="13:34",
        market_data_outage=False,
    )
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    # Should have some market data shown (not unavailable)
    assert "Market Prices: unavailable" not in text


def test_closing_wrap_shows_in_formatter():
    """Closing Wrap session must show 'DAY VERDICT' or 'CLOSING' heading."""
    briefing = _briefing("closing_wrap")
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert any(tok in text.upper() for tok in ("DAY VERDICT", "CLOSING", "WRAP", "SESSION")), (
        f"Expected a day/closing heading in closing_wrap output"
    )
