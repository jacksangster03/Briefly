from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.briefing.news_classifier import (
    annotate_news_events,
    classify_news_event,
    should_suppress_low_signal,
)
from app.briefing.llm_news_classifier import run_llm_news_classifier_shadow
from app.schemas.events import NormalisedEvent
from app.settings import Settings


def _evt(**kwargs) -> NormalisedEvent:
    base = dict(
        title="Company reports earnings beat and raises guidance",
        summary="Revenue beat with stronger outlook.",
        event_type="earnings",
        final_score=0.82,
        factual_confidence_score=0.84,
        personal_relevance_score=0.5,
        update_status="new",
        tickers=["LLY"],
        published_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        raw_data={},
    )
    base.update(kwargs)
    return NormalisedEvent(**base)


def test_old_published_story_is_not_breaking():
    evt = _evt(
        title="Central bank comments from last week continue to circulate",
        summary="No new policy action disclosed.",
        event_type="macro_release",
        published_at=datetime.now(timezone.utc) - timedelta(hours=20),
        raw_data={"first_seen_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()},
    )
    cls = classify_news_event(evt, breaking_max_age_hours=6)
    assert cls.freshness_state in {"old_context", "stale"}
    assert cls.breaking_label in {"LATE DISCOVERY", "CONTEXT"}


def test_material_update_labelled_update():
    evt = _evt(update_status="material_update")
    cls = classify_news_event(evt)
    assert cls.freshness_state == "updated"
    assert cls.breaking_label == "UPDATE"


def test_valuation_commentary_demoted_without_catalyst():
    evt = _evt(
        title="Is this tech stock cheap after a pullback?",
        summary="Valuation commentary with no catalyst.",
        event_type="news_search",
        tickers=["QQQ"],
    )
    annotate_news_events([evt])
    assert should_suppress_low_signal(evt)
    assert evt.raw_data.get("news_suppress_reason") in {
        "low_signal_format",
        "valuation_commentary_without_catalyst",
    }


def test_generic_qqq_spy_polling_story_not_portfolio_focus():
    evt = _evt(
        title="Approval polling shifts as investors watch election narrative",
        summary="SPY and QQQ mentioned as market proxies.",
        event_type="news_search",
        tickers=["SPY", "QQQ"],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_suppress_reason") in {
        "generic_polling_with_etf_proxy_only",
        "no_hard_catalyst",
    }


def test_low_ticker_confidence_story_can_be_suppressed():
    evt = _evt(
        title="Macro wrap: mixed session with little catalyst",
        summary="Daily wrap article.",
        event_type="news_search",
        tickers=[],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") in {"generic_market_wrap", "ignore", "low_signal"}


def test_alphabet_thesis_article_not_breaking_market_moving():
    evt = _evt(
        title="Alphabet's Earnings Reaffirm Our Thesis Despite Scale",
        summary="Post-earnings interpretation and valuation framing.",
        event_type="news_search",
        tickers=["GOOGL"],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") in {"earnings_analysis", "commentary_valuation"}
    assert evt.raw_data.get("news_story_type") != "breaking_market_moving"
    assert evt.raw_data.get("news_breaking_eligible") is False


def test_toyota_tesla_commentary_not_breaking_market_moving():
    evt = _evt(
        title="What Toyota’s Earnings Mean for Tesla",
        summary="Cross-name interpretation without fresh catalyst.",
        event_type="news_search",
        tickers=["TM", "TSLA"],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") in {"single_name_context", "commentary_valuation", "earnings_analysis"}
    assert evt.raw_data.get("news_story_type") != "breaking_market_moving"
    assert evt.raw_data.get("news_breaking_eligible") is False


def test_meta_excited_after_q1_not_breaking_market_moving():
    evt = _evt(
        title="Why Are Some Investors Excited About Meta Platforms After Its Q1 Earnings?",
        summary="Interpretive follow-up commentary.",
        event_type="news_search",
        tickers=["META"],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") in {"earnings_analysis", "commentary_valuation"}
    assert evt.raw_data.get("news_breaking_eligible") is False


def test_wedbush_target_raise_maps_to_analyst_action():
    evt = _evt(
        title="Apple shares gain after Wedbush raises target to $240",
        summary="Analyst target change following earnings.",
        event_type="news_search",
        tickers=["AAPL"],
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") == "analyst_action"
    assert evt.raw_data.get("news_breaking_eligible") is False


def test_partner_award_not_breaking_market_moving():
    evt = _evt(
        title="GuidePoint Security Wins Partner Award",
        summary="Routine partner award announcement.",
        event_type="company_news",
    )
    annotate_news_events([evt])
    assert evt.raw_data.get("news_story_type") in {"product_partnership", "low_signal", "ignore"}
    assert evt.raw_data.get("news_story_type") != "breaking_market_moving"


def test_fresh_sanctions_shock_can_still_be_breaking():
    evt = _evt(
        title="US sanctions trigger oil chokepoint fears near Hormuz",
        summary="Crude spikes as shipping risk escalates.",
        event_type="geopolitical",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    annotate_news_events([evt], breaking_max_age_hours=6)
    assert evt.raw_data.get("news_story_type") in {"geopolitical_energy", "breaking_market_moving"}
    assert evt.raw_data.get("news_breaking_eligible") is True


def test_llm_shadow_failure_falls_back_deterministically(monkeypatch):
    settings = Settings(enable_llm_news_classifier=True, openai_api_key="test-key")
    event = _evt()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.briefing.llm_news_classifier._call_llm_news_classifier", _boom)
    out = run_llm_news_classifier_shadow(
        settings=settings,
        profile_name="default_user",
        session_key="morning",
        local_date=datetime.now(timezone.utc).date(),
        events=[event],
    )
    assert out["enabled"] is True
    assert out["executed"] is False
    assert str(out["reason"]).startswith("error:")


def test_llm_shadow_mode_does_not_mutate_deterministic_event_fields():
    settings = Settings(enable_llm_news_classifier=True, llm_news_classifier_shadow_mode=True)
    event = _evt()
    before = event.model_dump()
    out = run_llm_news_classifier_shadow(
        settings=settings,
        profile_name="default_user",
        session_key="morning",
        local_date=datetime.now(timezone.utc).date(),
        events=[event],
    )
    assert out["enabled"] is True
    assert event.model_dump() == before
