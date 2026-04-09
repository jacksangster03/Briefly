"""Tests for Phase 2 pipeline behaviors."""

from __future__ import annotations

from app.briefing.theme_builder import build_top_themes
from app.personalization.delivery_rules import load_alert_rules
from app.processing.event_clustering import build_story_key, cluster_events
from app.processing.pipeline import select_intraday_events
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def test_story_key_is_stable_for_same_catalyst():
    a = NormalisedEvent(
        title="NVDA raises AI chip guidance after Blackwell demand jump",
        summary="Guidance raised on strong AI demand.",
        tickers=["NVDA"],
        event_type="guidance",
    )
    b = NormalisedEvent(
        title="NVDA raises AI chip guidance after Blackwell demand jump",
        summary="Guidance raised on strong AI demand.",
        tickers=["NVDA"],
        event_type="guidance",
    )
    assert build_story_key(a) == build_story_key(b)


def test_cluster_events_sets_cluster_metadata():
    events = [
        NormalisedEvent(
            title="AAPL launches new AI features at WWDC",
            tickers=["AAPL"],
            event_type="company_news",
            final_score=0.7,
        ),
        NormalisedEvent(
            title="Apple unveils AI features at WWDC",
            tickers=["AAPL"],
            event_type="headline",
            final_score=0.6,
        ),
    ]
    clustered = cluster_events(events)
    assert len(clustered) == 1
    assert clustered[0].cluster_size == 2
    assert clustered[0].cluster_id


def test_intraday_selector_prefers_actionable_events():
    rules = load_alert_rules(Settings()).intraday
    events = [
        NormalisedEvent(
            title="2 Monster EV Stocks Worth Owning While the Sector Is Still Out of Favor",
            event_type="headline",
            final_score=0.9,
            novelty_score=1.0,
        ),
        NormalisedEvent(
            title="Fed signals patience on rates as oil shock lifts inflation risk",
            event_type="macro_release",
            final_score=0.75,
            novelty_score=1.0,
        ),
    ]
    selected = select_intraday_events(events, rules)
    assert len(selected) == 1
    assert "Fed signals" in selected[0].title


def test_theme_builder_generates_clean_summary():
    event = NormalisedEvent(
        title="TSMC flags tighter AI chip capacity for second half",
        summary="Advanced packaging constraints limit supply for H2 orders.",
        tickers=["TSM"],
        sectors=["semiconductors"],
        event_type="company_news",
        cluster_id="story123",
        cluster_size=3,
        update_status="new",
        personal_relevance_score=0.9,
        final_score=0.8,
        raw_data={
            "summary_original": "Advanced packaging constraints limit supply for H2 orders.",
            "cluster_sources": ["finnhub", "newsapi"],
        },
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    summary = themes[0].summary
    # Should use original summary content, not a mechanical template
    assert "Advanced packaging" in summary or "3 reports" in summary
    # Should NOT contain mechanical patterns
    assert "Focus:" not in summary
    assert "Why it matters:" not in summary


def test_theme_builder_skips_duplicate_summary():
    """When the summary is just the title repeated, don't echo it."""
    event = NormalisedEvent(
        title="Fed signals patience on rate cuts",
        summary="Fed signals patience on rate cuts  Reuters",
        tickers=[],
        sectors=[],
        event_type="macro_release",
        cluster_id="fed1",
        cluster_size=1,
        update_status="new",
        personal_relevance_score=0.7,
        final_score=0.8,
        raw_data={"summary_original": "Fed signals patience on rate cuts  Reuters"},
    )
    themes = build_top_themes([event], max_themes=1)
    assert len(themes) == 1
    # Summary should NOT just re-echo the title
    assert "Fed signals patience on rate cuts" not in themes[0].summary


def test_alert_rules_load_phase2_thresholds():
    rules = load_alert_rules(Settings())
    assert rules.intraday.continuation_min_final_score >= rules.intraday.min_final_score
    assert rules.breaking.material_update_min_final_score >= rules.breaking.min_final_score


def test_low_signal_filtering_catches_listicles():
    """Content-farm listicle/opinion pieces should be filtered out."""
    from app.processing.pipeline import is_actionable_event

    blocked_titles = [
        "Is Qualcomm Stock A Cheap Cash Machine With Big AI Potential?",
        "Everyone's Buying NVIDIA - Here Are 2 Smarter AI Stocks for Q2 2026",
        "3 Reasons to Buy AAPL Before June",
        "Four Risks To Watch For Palantir Stock In The Next 6 Months",
        "Why TSLA Dropped 5% Today",
        "Jim Cramer Says Buy This Dip",
        "Is the Iran War Market Dip a Buying Opportunity? Here's What Warren Buffett Had To Say",
    ]
    for title in blocked_titles:
        evt = NormalisedEvent(
            title=title,
            source="newsapi",
            tickers=["AAPL"],
            event_type="headline",
        )
        assert not is_actionable_event(evt), f"Should have blocked: {title}"


def test_low_signal_filtering_allows_real_catalysts():
    """Real catalysts should pass the filter."""
    from app.processing.pipeline import is_actionable_event

    allowed_titles = [
        "NVDA raises AI chip guidance after Blackwell demand jump",
        "Fed holds rates steady, signals patience on cuts",
        "FDA approves Lilly obesity drug for expanded indication",
        "8-K: AMAZON COM INC  (AMZN): FORM 8-K",
        "Trump says US military to stay around Iran",
    ]
    for title in allowed_titles:
        evt = NormalisedEvent(
            title=title,
            source="finnhub",
            tickers=["NVDA"],
            event_type="company_news",
        )
        assert is_actionable_event(evt), f"Should have allowed: {title}"


def test_macro_thread_clustering():
    """Multiple articles about the same geopolitical thread should cluster."""
    events = [
        NormalisedEvent(
            title="Trump says US military to stay around Iran",
            event_type="market_news",
            final_score=0.77,
        ),
        NormalisedEvent(
            title="US-Iran ceasefire: what we know so far",
            event_type="market_news",
            final_score=0.75,
        ),
        NormalisedEvent(
            title="NATO chief says allies were tested in Iran war",
            event_type="market_news",
            final_score=0.73,
        ),
        NormalisedEvent(
            title="India grants waivers for ships to deliver Iran cargoes",
            event_type="market_news",
            final_score=0.70,
        ),
    ]
    clustered = cluster_events(events)
    # All Iran-thread events should collapse into one cluster
    assert len(clustered) == 1
    assert clustered[0].cluster_size == 4
