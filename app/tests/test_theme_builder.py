from app.briefing.theme_builder import build_top_themes
from app.schemas.events import NormalisedEvent


def test_theme_builder_prefers_holdings_linked_theme_over_filing_stub():
    filing = NormalisedEvent(
        title="Company files Form 8-K",
        summary="Routine filing update.",
        source="sec_edgar",
        event_type="filing",
        tickers=["ZZZZ"],
        personal_relevance_score=0.9,
        factual_confidence_score=0.9,
        final_score=0.9,
        cluster_size=1,
    )
    holding_theme = NormalisedEvent(
        title="Nvidia guidance lifts AI capex outlook",
        summary="Multiple reports point to stronger demand.",
        source="finnhub",
        event_type="guidance",
        tickers=["NVDA"],
        personal_relevance_score=0.95,
        factual_confidence_score=0.85,
        final_score=0.82,
        cluster_size=3,
    )

    themes = build_top_themes(
        [filing, holding_theme],
        max_themes=1,
        preferred_symbols={"NVDA"},
    )
    assert themes
    assert themes[0].title == "Nvidia guidance lifts AI capex outlook"
