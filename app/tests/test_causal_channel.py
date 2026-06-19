from __future__ import annotations

from app.processing.causal_channel import classify_causal_channel
from app.schemas.events import NormalisedEvent


def _event(**kwargs) -> NormalisedEvent:
    defaults = {"title": "", "summary": "", "event_type": ""}
    defaults.update(kwargs)
    return NormalisedEvent(**defaults)


def test_explicit_event_type_mapping_earnings():
    assert classify_causal_channel(_event(event_type="earnings")) == "earnings"


def test_explicit_event_type_mapping_fed_decision():
    assert classify_causal_channel(_event(event_type="fed_decision")) == "rates"


def test_explicit_event_type_mapping_fda_decision():
    assert classify_causal_channel(_event(event_type="fda_decision")) == "regulatory"


def test_explicit_event_type_mapping_geopolitical():
    assert classify_causal_channel(_event(event_type="geopolitical")) == "geopolitical"


def test_explicit_event_type_mapping_analyst_action():
    assert classify_causal_channel(_event(event_type="analyst_action")) == "sentiment"


def test_keyword_fallback_rates():
    event = _event(event_type="market_news", title="Fed signals fewer rate cuts in 2026")
    assert classify_causal_channel(event) == "rates"


def test_keyword_fallback_supply_chain():
    event = _event(
        event_type="company_news",
        title="Chip shortage forces factory shutdown at supplier",
    )
    assert classify_causal_channel(event) == "supply_chain"


def test_keyword_fallback_sentiment():
    event = _event(event_type="headline", title="Analyst upgrade lifts price target on AI demand")
    assert classify_causal_channel(event) == "sentiment"


def test_falls_back_to_other_when_nothing_matches():
    event = _event(event_type="market_news", title="Local bakery opens new storefront downtown")
    assert classify_causal_channel(event) == "other"


def test_event_type_mapping_takes_priority_over_keywords():
    """Even if the text contains "earnings"-like keywords, an explicit
    event_type mapping to a different channel should win.
    """
    event = _event(event_type="fed_decision", title="Fed decision overshadows earnings season")
    assert classify_causal_channel(event) == "rates"
