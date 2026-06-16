from __future__ import annotations

from app.processing.price_confirmation import (
    DEFAULT_MOVE_THRESHOLD_PCT,
    apply_price_confirmation,
    confirm_price_action,
    expected_direction_from_sentiment,
)
from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import ResearchEvent


def _quote(symbol: str, change_percent: float) -> QuoteData:
    return QuoteData(symbol=symbol, current_price=100.0, change_percent=change_percent)


def test_expected_direction_from_sentiment_up():
    assert expected_direction_from_sentiment(0.5) == "up"


def test_expected_direction_from_sentiment_down():
    assert expected_direction_from_sentiment(-0.5) == "down"


def test_expected_direction_from_sentiment_neutral_band():
    assert expected_direction_from_sentiment(0.05) == "neutral"
    assert expected_direction_from_sentiment(-0.1) == "neutral"


def test_confirm_price_action_confirmed_when_move_matches_direction():
    status, detail = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={"NVDA": _quote("NVDA", 2.5)},
    )
    assert status == "confirmed"
    assert "NVDA" in detail


def test_confirm_price_action_contradicted_when_move_opposes_direction():
    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={"NVDA": _quote("NVDA", -2.5)},
    )
    assert status == "contradicted"


def test_confirm_price_action_pending_when_move_below_threshold():
    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={"NVDA": _quote("NVDA", 0.2)},
    )
    assert status == "pending"


def test_confirm_price_action_unavailable_when_quote_missing():
    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={},
    )
    assert status == "unavailable"


def test_confirm_price_action_unavailable_for_neutral_direction():
    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="neutral",
        quotes_by_symbol={"NVDA": _quote("NVDA", 5.0)},
    )
    assert status == "unavailable"


def test_confirm_price_action_no_tickers():
    status, _ = confirm_price_action(tickers=[], direction="up", quotes_by_symbol={})
    assert status == "unavailable"


def test_confirm_price_action_multi_ticker_confirmed_wins_over_contradicted():
    """If one ticker confirms and another contradicts, confirmed wins
    (the story is about at least one real, moving asset).
    """
    status, _ = confirm_price_action(
        tickers=["NVDA", "AMD"],
        direction="up",
        quotes_by_symbol={
            "NVDA": _quote("NVDA", 3.0),   # confirms
            "AMD": _quote("AMD", -3.0),    # contradicts
        },
    )
    assert status == "confirmed"


def test_confirm_price_action_multi_ticker_contradicted_when_no_confirmation():
    status, _ = confirm_price_action(
        tickers=["NVDA", "AMD"],
        direction="up",
        quotes_by_symbol={
            "NVDA": _quote("NVDA", -3.0),
            "AMD": _quote("AMD", -2.0),
        },
    )
    assert status == "contradicted"


def test_confirm_price_action_respects_custom_threshold():
    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={"NVDA": _quote("NVDA", 1.5)},
        move_threshold_pct=2.0,
    )
    assert status == "pending"

    status, _ = confirm_price_action(
        tickers=["NVDA"],
        direction="up",
        quotes_by_symbol={"NVDA": _quote("NVDA", 1.5)},
        move_threshold_pct=1.0,
    )
    assert status == "confirmed"


def test_apply_price_confirmation_uses_research_event_tickers_first():
    source_event = NormalisedEvent(
        title="Nvidia raises guidance",
        sentiment=0.6,
        tickers=["NVDA"],
    )
    research_event = ResearchEvent(title="Nvidia raises guidance", tickers=["NVDA"])

    result = apply_price_confirmation(
        research_event,
        source_event,
        quotes_by_symbol={"NVDA": _quote("NVDA", 4.0)},
    )

    assert result is research_event
    assert research_event.price_confirmation_status == "confirmed"
    assert "NVDA" in research_event.price_confirmation_detail


def test_apply_price_confirmation_falls_back_to_source_event_tickers():
    source_event = NormalisedEvent(
        title="Broadcom AI demand story",
        sentiment=0.5,
        tickers=["AVGO"],
    )
    research_event = ResearchEvent(title="Broadcom AI demand story")  # no tickers set

    apply_price_confirmation(
        research_event,
        source_event,
        quotes_by_symbol={"AVGO": _quote("AVGO", 3.2)},
    )

    assert research_event.price_confirmation_status == "confirmed"


def test_apply_price_confirmation_unavailable_for_neutral_sentiment_event():
    source_event = NormalisedEvent(title="Generic market wrap", sentiment=0.0, tickers=["SPY"])
    research_event = ResearchEvent(title="Generic market wrap", tickers=["SPY"])

    apply_price_confirmation(
        research_event,
        source_event,
        quotes_by_symbol={"SPY": _quote("SPY", 5.0)},
    )

    assert research_event.price_confirmation_status == "unavailable"


def test_default_move_threshold_matches_documented_value():
    assert DEFAULT_MOVE_THRESHOLD_PCT == 0.9
