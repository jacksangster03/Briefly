from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_generator import MorningBriefingGenerator
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent


def test_non_morning_empty_global_news_mentions_no_material_new_headlines() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc),
        session_key="us_intraday_risk",
        session_title="US Intraday Risk Check",
        what_changed_header="WHAT CHANGED SINCE US PRE-OPEN SETUP",
        global_news=[],
    )
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "No material new headlines since US Pre-Open Setup" in text


def test_low_confidence_ticker_mismatch_suppressed_from_top_themes(monkeypatch) -> None:
    gen = MorningBriefingGenerator.__new__(MorningBriefingGenerator)
    gen.profile = SimpleNamespace(portfolio_symbols=[], all_watchlist_tickers=[])
    gen.rules = SimpleNamespace(max_themes=5)
    gen._is_editorially_trustworthy = lambda *args, **kwargs: True
    gen._apply_news_hygiene = lambda evt, section=None: evt

    low_conf = NormalisedEvent(
        source="newsapi",
        source_type="news",
        title="Apple valuation commentary",
        summary="Generic valuation view with weak mapping",
        url="https://example.com/story",
        tickers=["AAPL"],
        raw_data={"ticker_confidence": 0.52},
        event_type="company_news",
    )
    high_conf = NormalisedEvent(
        source="newsapi",
        source_type="news",
        title="AMD issues updated guidance",
        summary="Guidance revision tied to earnings call.",
        url="https://example.com/amd",
        tickers=["AMD"],
        raw_data={"ticker_confidence": 0.91},
        event_type="guidance",
    )

    monkeypatch.setattr(
        "app.briefing.morning_generator.build_top_themes",
        lambda *_a, **_k: [low_conf, high_conf],
    )

    selected = gen._build_top_themes([low_conf, high_conf], "us_pre_open")
    titles = [evt.title for evt in selected]
    assert "Apple valuation commentary" not in titles
    assert "AMD issues updated guidance" in titles


def test_watchlist_basis_change_note_for_intraday_live_transition() -> None:
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 8, 14, 15, tzinfo=timezone.utc),
        session_key="us_intraday_risk",
        session_title="US Intraday Risk Check",
        watchlist_quotes=[],
    )
    # Build real QuoteData objects with explicit live freshness metadata.
    from app.schemas.events import QuoteData
    q1 = QuoteData(symbol="AMD", display_name="AMD", current_price=420.0, change_percent=1.12)
    q2 = QuoteData(symbol="NVDA", display_name="NVDA", current_price=1100.0, change_percent=0.84)
    briefing.watchlist_quotes = [q1, q2]
    briefing.quote_freshness = {
        "AMD": {"freshness_state": "near_real_time"},
        "NVDA": {"freshness_state": "live"},
    }
    text = "\n".join(TelegramFormatter("Europe/Madrid").format_morning_briefing(briefing))
    assert "Watchlist basis changed from prior-close context to live intraday quotes." in text
