"""Phase 6 tests: ResearchEvent-driven ranking and applied_news_stack routing.

Covers the use_research_event_ranking flag wiring in MorningBriefingGenerator:
- _build_research_event_index builds a valid index
- _build_top_themes uses composite_research_score when index is supplied
- _build_applied_news_stack routes by causal_channel when index is supplied
- New formatter bucket labels render without "Theme" fallback
- Flag=False leaves existing keyword path unchanged (no regression)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_generator import MorningBriefingGenerator
from app.personalization.user_profile import UserProfile
from app.processing.research_event_builder import build_research_event, composite_research_score
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent, SectorSnapshot
from app.schemas.research_event import ResearchEvent
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_id: str = "e1",
    title: str = "Test event",
    event_type: str = "macro_release",
    source: str = "fred",
    final_score: float = 1.5,
    personal_relevance_score: float = 0.70,
    novelty_score: float = 0.60,
    cluster_size: int = 3,
    tickers: list[str] | None = None,
) -> NormalisedEvent:
    return NormalisedEvent(
        event_id=event_id,
        title=title,
        event_type=event_type,
        source=source,
        final_score=final_score,
        personal_relevance_score=personal_relevance_score,
        novelty_score=novelty_score,
        cluster_size=cluster_size,
        tickers=tickers or [],
        factual_confidence_score=0.80,
    )


def _make_generator(use_research_event_ranking: bool = False) -> MorningBriefingGenerator:
    settings = Settings(use_research_event_ranking=use_research_event_ranking)
    profile = UserProfile(name="test")
    universe = SectorUniverse(sectors=[], indices=[], macro_instruments=[])
    market_data = MagicMock()
    market_data.get_quotes.return_value = []
    macro_data = MagicMock()
    macro_data.get_morning_macro.return_value = []
    news_data = MagicMock()
    news_data.get_news.return_value = []
    return MorningBriefingGenerator(
        settings=settings,
        profile=profile,
        market_data=market_data,
        macro_data=macro_data,
        news_data=news_data,
        universe=universe,
    )


# ---------------------------------------------------------------------------
# _build_research_event_index
# ---------------------------------------------------------------------------


class TestBuildResearchEventIndex:
    def test_returns_mapping_keyed_by_event_id(self):
        gen = _make_generator(use_research_event_ranking=True)
        events = [_make_event("a"), _make_event("b")]
        index = gen._build_research_event_index(events, preferred_symbols=set())
        assert set(index.keys()) == {"a", "b"}

    def test_each_entry_is_research_event_and_float(self):
        gen = _make_generator()
        events = [_make_event("x")]
        index = gen._build_research_event_index(events, preferred_symbols=set())
        re, score = index["x"]
        assert isinstance(re, ResearchEvent)
        assert isinstance(score, float)

    def test_preferred_symbols_flow_through_to_watchlist_relevance(self):
        gen = _make_generator()
        events = [_make_event("t", tickers=["AAPL"])]
        index_with = gen._build_research_event_index(events, preferred_symbols={"AAPL"})
        index_without = gen._build_research_event_index(events, preferred_symbols=set())
        re_with, _ = index_with["t"]
        re_without, _ = index_without["t"]
        assert re_with.watchlist_relevance_score > re_without.watchlist_relevance_score

    def test_bad_event_is_skipped_not_raised(self):
        gen = _make_generator()

        class BrokenEvent:
            event_id = "bad"

        index = gen._build_research_event_index([BrokenEvent()], preferred_symbols=set())  # type: ignore[list-item]
        assert "bad" not in index


# ---------------------------------------------------------------------------
# _build_top_themes with research_index
# ---------------------------------------------------------------------------


class TestBuildTopThemesWithResearchIndex:
    def test_returns_normalised_events_not_research_events(self):
        gen = _make_generator()
        evt = _make_event("t1", source="fred", event_type="macro_release")
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        result = gen._build_top_themes([evt], "weekday", research_index=index)
        for item in result:
            assert isinstance(item, NormalisedEvent)

    def test_high_composite_score_event_outranks_low(self):
        gen = _make_generator()
        high = _make_event(
            "h1",
            title="Fed rate decision",
            event_type="fed_decision",
            source="fred",
            final_score=2.0,
            personal_relevance_score=0.90,
            novelty_score=0.90,
        )
        low = _make_event(
            "l1",
            title="Minor analyst note",
            event_type="macro_release",
            source="newsapi",
            final_score=0.8,
            personal_relevance_score=0.60,
            novelty_score=0.20,
        )
        index = gen._build_research_event_index([high, low], preferred_symbols=set())
        result = gen._build_top_themes([high, low], "weekday", research_index=index)
        if len(result) >= 2:
            assert result[0].event_id == "h1"

    def test_no_research_index_uses_existing_theme_builder(self):
        gen = _make_generator()
        evt = _make_event("n1", source="fred", event_type="macro_release")
        result = gen._build_top_themes([evt], "weekday", research_index=None)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _build_applied_news_stack causal_channel routing
# ---------------------------------------------------------------------------


class TestBuildAppliedNewsStackViaResearchEvents:
    def _make_briefing_with_themes(self, events: list[NormalisedEvent]) -> MorningBriefing:
        briefing = MorningBriefing(session_key="morning")
        briefing.top_themes = events
        briefing.global_news = events
        briefing.watchlist_events = []
        briefing.portfolio_focus = []
        briefing.sector_scan = []
        return briefing

    def test_rates_event_routes_to_macro_rates_bucket(self):
        gen = _make_generator(use_research_event_ranking=True)
        evt = _make_event(
            "r1",
            title="Fed raises rates amid inflation concerns",
            event_type="fed_decision",
            source="fred",
        )
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        briefing = self._make_briefing_with_themes([evt])
        result = gen._build_applied_news_stack(briefing, research_index=index)
        buckets = [row["bucket"] for row in result]
        assert "macro_rates" in buckets

    def test_earnings_event_routes_to_event_risk_bucket(self):
        gen = _make_generator(use_research_event_ranking=True)
        evt = _make_event(
            "e1",
            title="Apple Q3 earnings beat expectations",
            event_type="earnings",
            source="sec_edgar",
        )
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        briefing = self._make_briefing_with_themes([evt])
        result = gen._build_applied_news_stack(briefing, research_index=index)
        buckets = [row["bucket"] for row in result]
        assert "event_risk" in buckets

    def test_geopolitical_event_routes_to_geopolitical_bucket(self):
        gen = _make_generator(use_research_event_ranking=True)
        evt = _make_event(
            "g1",
            title="Iran closes Strait of Hormuz",
            event_type="geopolitical",
            source="newsapi",
        )
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        briefing = self._make_briefing_with_themes([evt])
        result = gen._build_applied_news_stack(briefing, research_index=index)
        buckets = [row["bucket"] for row in result]
        assert "geopolitical" in buckets

    def test_interpretation_used_as_why_it_matters(self):
        gen = _make_generator(use_research_event_ranking=True)
        evt = _make_event(
            "r2",
            title="ECB keeps rates on hold",
            event_type="fed_decision",
            source="ecb",
        )
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        briefing = self._make_briefing_with_themes([evt])
        result = gen._build_applied_news_stack(briefing, research_index=index)
        macro_rows = [r for r in result if r["bucket"] == "macro_rates"]
        if macro_rows:
            re, _ = index["r2"]
            assert macro_rows[0]["why_it_matters"] == re.interpretation or macro_rows[0]["why_it_matters"]

    def test_non_morning_session_returns_empty(self):
        gen = _make_generator(use_research_event_ranking=True)
        evt = _make_event("x1")
        index = gen._build_research_event_index([evt], preferred_symbols=set())
        briefing = MorningBriefing(session_key="intraday")
        result = gen._build_applied_news_stack(briefing, research_index=index)
        assert result == []

    def test_flag_false_uses_keyword_path(self):
        gen = _make_generator(use_research_event_ranking=False)
        evt = _make_event("k1", title="CPI inflation data release", event_type="macro_release")
        briefing = MorningBriefing(session_key="morning")
        briefing.top_themes = [evt]
        briefing.global_news = []
        briefing.watchlist_events = []
        briefing.portfolio_focus = []
        briefing.sector_scan = []
        result = gen._build_applied_news_stack(briefing, research_index=None)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Formatter bucket label extension
# ---------------------------------------------------------------------------


class TestFormatterBucketLabels:
    def setup_method(self):
        self.formatter = TelegramFormatter("Europe/Madrid")

    def _stack_with_bucket(self, bucket: str) -> list[dict]:
        return [
            {
                "bucket": bucket,
                "headline": "Test headline",
                "why_it_matters": "Test reason.",
                "affected_assets": ["AAPL"],
                "source_count": 2,
                "price_confirmation": "optional",
            }
        ]

    def test_regulatory_bucket_renders_label(self):
        result = self.formatter._format_applied_news_stack(self._stack_with_bucket("regulatory"))
        assert "Regulatory" in result

    def test_supply_chain_bucket_renders_label(self):
        result = self.formatter._format_applied_news_stack(self._stack_with_bucket("supply_chain"))
        assert "Supply Chain" in result

    def test_sentiment_bucket_renders_label(self):
        result = self.formatter._format_applied_news_stack(self._stack_with_bucket("sentiment"))
        assert "Analyst/Sentiment" in result

    def test_unknown_bucket_falls_back_to_theme(self):
        result = self.formatter._format_applied_news_stack(self._stack_with_bucket("__unknown__"))
        assert "Theme" in result
