from __future__ import annotations

from datetime import UTC

from app.schemas.events import NormalisedEvent
from app.schemas.research_event import EvidenceItem, ResearchEvent


def test_research_event_minimal_defaults():
    event = ResearchEvent(title="Fed holds rates steady")
    assert event.title == "Fed holds rates steady"
    assert event.causal_channel == "other"
    assert event.price_confirmation_status == "unavailable"
    assert event.tickers == []
    assert event.evidence_items == []
    assert event.event_id  # auto-generated uuid


def test_research_event_ticker_normalisation():
    event = ResearchEvent(title="x", tickers="nvda, amd")
    assert event.tickers == ["NVDA", "AMD"]


def test_research_event_compute_hash_is_deterministic():
    a = ResearchEvent(source_event_id="evt-1", title="Same Headline")
    b = ResearchEvent(source_event_id="evt-1", title="Same Headline")
    assert a.compute_hash() == b.compute_hash()


def test_research_event_compute_hash_differs_on_title():
    a = ResearchEvent(source_event_id="evt-1", title="Headline A")
    b = ResearchEvent(source_event_id="evt-1", title="Headline B")
    assert a.compute_hash() != b.compute_hash()


def test_research_event_evidence_item_defaults():
    item = EvidenceItem(url="https://example.com", source_name="sec_edgar")
    assert item.source_tier == ""
    assert item.published_at is None


def test_research_event_from_normalised_event_copies_known_fields():
    normalised = NormalisedEvent(
        event_id="evt-123",
        title="NVDA beats on AI demand",
        summary="Strong datacenter revenue",
        url="https://example.com/nvda",
        tickers=["nvda"],
        sectors=["semiconductors"],
        novelty_score=0.8,
        personal_relevance_score=0.6,
        factual_confidence_score=0.9,
        final_score=0.75,
        reason_code="",
    )

    research_event = ResearchEvent.from_normalised_event(normalised)

    assert research_event.source_event_id == "evt-123"
    assert research_event.title == "NVDA beats on AI demand"
    assert research_event.tickers == ["NVDA"]
    assert research_event.sectors == ["semiconductors"]
    assert research_event.novelty_score == 0.8
    assert research_event.portfolio_relevance_score == 0.6
    assert research_event.confidence == 0.9
    assert research_event.final_score == 0.75
    assert research_event.primary_sources == ["https://example.com/nvda"]
    # Fields that require logic not built yet stay at their defaults.
    assert research_event.causal_channel == "other"
    assert research_event.price_confirmation_status == "unavailable"
    assert research_event.evidence_items == []


def test_research_event_from_normalised_event_handles_missing_url():
    normalised = NormalisedEvent(event_id="evt-456", title="No URL story")
    research_event = ResearchEvent.from_normalised_event(normalised)
    assert research_event.primary_sources == []


def test_research_event_generated_at_is_timezone_aware():
    event = ResearchEvent(title="x")
    assert event.generated_at.tzinfo is not None
    assert event.generated_at.tzinfo == UTC
