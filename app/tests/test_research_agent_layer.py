from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.formatter import TelegramFormatter
from app.research.confidence import calculate_research_confidence
from app.research.events import (
    build_research_events,
    build_research_freshness_summary,
    diff_research_events,
    rank_research_events,
)
from app.research.rendering import render_analyst_read
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent, QuoteData
from app.schemas.research_event import ResearchEvent


def _event(**kwargs) -> NormalisedEvent:
    base = {
        "event_id": "evt-1",
        "source": "sec_edgar",
        "source_type": "filing",
        "published_at": datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
        "title": "Nvidia files 8-K with updated guidance",
        "summary": "Company filing points to higher revenue guidance.",
        "url": "https://www.sec.gov/example",
        "tickers": ["NVDA"],
        "event_type": "guidance",
        "sentiment": 0.8,
        "novelty_score": 1.0,
        "personal_relevance_score": 0.85,
        "factual_confidence_score": 0.95,
        "cluster_size": 3,
        "update_status": "new",
        "raw_data": {"news_freshness_state": "new"},
    }
    base.update(kwargs)
    return NormalisedEvent(**base)


def test_research_builder_populates_freshness_evidence_confidence_and_price_confirmation():
    quote = QuoteData(symbol="NVDA", current_price=100, change_percent=2.4)
    result = build_research_events(
        [_event()],
        quotes_by_symbol={"NVDA": quote},
        preferred_symbols={"NVDA"},
        now=datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc),
    )
    assert len(result) == 1
    research_event = result[0]
    assert research_event.freshness_state == "new"
    assert research_event.evidence_items
    assert research_event.price_confirmation_status == "confirmed"
    assert research_event.confidence_label == "high"
    assert research_event.why_now
    assert research_event.what_to_watch_next


def test_confidence_low_when_price_action_contradicts_story():
    event = ResearchEvent(
        source_tier="highest",
        source_quality_score=0.95,
        freshness_state="new",
        corroboration_score=0.9,
        portfolio_relevance_score=0.9,
        confidence=0.95,
        price_confirmation_status="contradicted",
        primary_sources=["https://www.sec.gov/example"],
    )
    confidence = calculate_research_confidence(event)
    assert confidence.label == "low"
    assert "contradicts" in confidence.reason


def test_rank_research_events_filters_none_confidence_and_orders_by_score():
    weak = ResearchEvent(title="Weak", confidence_label="none", final_score=99)
    strong = ResearchEvent(title="Strong", confidence_label="medium", final_score=2)
    ranked = rank_research_events([weak, strong], session_key="morning", max_items=5)
    assert [event.title for event in ranked] == ["Strong"]


def test_diff_research_events_marks_price_confirmation_change_as_material_update():
    previous = ResearchEvent(source_event_id="same", price_confirmation_status="pending")
    current = ResearchEvent(source_event_id="same", price_confirmation_status="confirmed")
    deltas = diff_research_events([current], [previous])
    assert deltas[0].status == "material_update"
    assert "price_confirmation" in deltas[0].changed_fields


def test_freshness_summary_reports_active_and_empty_providers():
    event = ResearchEvent(
        published_at=datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
        freshness_state="new",
        source_tier="highest",
    )
    summary = build_research_freshness_summary(
        [event],
        provider_contributions={"finnhub": 3, "gdelt": 0},
        timezone_label="Europe/Paris",
    )
    assert "Finnhub" in summary.source_basis_label
    assert "Gdelt" in summary.source_basis_label


def test_render_analyst_read_empty_state_is_explicit_not_filler():
    lines = render_analyst_read([], session_key="europe_midday")
    assert lines == ["No material fresh portfolio-relevant news cleared the research bar since the prior session."]


def test_formatter_renders_analyst_read_and_source_basis():
    briefing = MorningBriefing(
        generated_at=datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc),
        analyst_read_lines=["- <b>NVDA</b>: Fresh guidance update. <i>Confidence: HIGH</i>"],
        research_source_basis="Source basis: provider evidence through 08:00 UTC; active providers: finnhub.",
    )
    rendered = "\n".join(TelegramFormatter("Europe/Paris").format_morning_briefing(briefing))
    assert "ANALYST READ" in rendered
    assert "Source basis:" in rendered
