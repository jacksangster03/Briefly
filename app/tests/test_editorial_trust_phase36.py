"""Phase 3.6 tests: editorial trust and cross-section de-duplication."""

from __future__ import annotations

from datetime import datetime

from app.briefing.morning_generator import MorningBriefingGenerator
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorDef, SectorUniverse


class _StubMarketData:
    def get_quotes(self, symbols):
        return []


class _StubNewsData:
    finnhub = None


class _StubMacroData:
    pass


def _build_generator() -> MorningBriefingGenerator:
    universe = SectorUniverse(
        sectors=[
            SectorDef(
                key="real_estate",
                etf="XLRE",
                display_name="Real Estate",
                key_names=["PSA"],
            ),
            SectorDef(
                key="technology",
                etf="XLK",
                display_name="Technology",
                key_names=["NVDA", "AAPL"],
            ),
        ],
        indices=[],
        macro_instruments=[],
    )
    profile = UserProfile(
        watchlist_primary=["NVDA", "AAPL", "TSLA"],
        sector_weights={"technology": 1.0, "real_estate": 0.8},
    )
    return MorningBriefingGenerator(
        settings=Settings(),
        profile=profile,
        universe=universe,
        market_data=_StubMarketData(),
        news_data=_StubNewsData(),
        macro_data=_StubMacroData(),
    )


def test_top_themes_weekend_filters_side_angle_headline():
    generator = _build_generator()
    weak = NormalisedEvent(
        title="Nvidia CEO Says 'Move to California' Despite High Taxes",
        summary="Commentary angle not tied to operating catalyst.",
        source="newsapi",
        event_type="company_news",
        tickers=["NVDA"],
        sectors=["technology"],
        personal_relevance_score=0.82,
        factual_confidence_score=0.55,
        cluster_size=1,
        final_score=0.78,
        raw_data={"source_name": "TheStreet"},
    )
    strong = NormalisedEvent(
        title="Nvidia raises AI chip output guidance for H2 2026 demand",
        summary="Management increased production outlook after hyperscaler orders.",
        source="finnhub",
        event_type="guidance",
        tickers=["NVDA"],
        sectors=["technology"],
        personal_relevance_score=0.88,
        factual_confidence_score=0.82,
        cluster_size=5,
        final_score=0.80,
        raw_data={"source_name": "Reuters"},
    )

    themes = generator._build_top_themes([weak, strong], session_mode="saturday")
    assert [event.title for event in themes] == [strong.title]


def test_sector_scan_filters_known_low_value_pattern():
    generator = _build_generator()
    weak = NormalisedEvent(
        title="PSA: If you use the Meta AI app, your friends will find out and it will be embarrassing",
        summary="Side-angle social framing.",
        source="newsapi",
        event_type="company_news",
        tickers=["PSA"],
        sectors=["real_estate"],
        personal_relevance_score=0.74,
        factual_confidence_score=0.55,
        cluster_size=1,
        final_score=0.70,
        raw_data={"source_name": "Benzinga"},
    )
    strong = NormalisedEvent(
        title="Public Storage raises 2026 occupancy guidance after strong leasing trends",
        summary="Operating guidance update tied to demand.",
        source="finnhub",
        event_type="guidance",
        tickers=["PSA"],
        sectors=["real_estate"],
        personal_relevance_score=0.76,
        factual_confidence_score=0.81,
        cluster_size=3,
        final_score=0.75,
        raw_data={"source_name": "Reuters"},
    )

    snapshots = generator._build_sector_scan([weak, strong], session_mode="saturday")
    real_estate = next(snapshot for snapshot in snapshots if snapshot.sector_key == "real_estate")
    assert [event.title for event in real_estate.top_events] == [strong.title]


def test_cross_section_dedupe_keeps_story_once():
    generator = _build_generator()
    shared = NormalisedEvent(
        title="EV bloodbath: US sales plunge as Tesla tightens its grip",
        summary="Direct operating signal.",
        source="finnhub",
        event_type="company_news",
        tickers=["TSLA"],
        sectors=["consumer_discretionary"],
        cluster_id="cluster_tesla_1",
    )
    briefing = MorningBriefing(generated_at=datetime(2026, 4, 11, 8, 45))
    briefing.portfolio_focus = [shared]
    briefing.top_themes = [shared.model_copy(deep=True)]
    briefing.sector_scan = [
        SectorSnapshot(
            sector_key="consumer_discretionary",
            display_name="Consumer Discretionary",
            etf_symbol="XLY",
            top_events=[shared.model_copy(deep=True)],
        )
    ]
    briefing.watchlist_events = [shared.model_copy(deep=True)]

    generator._dedupe_cross_section_events(briefing)

    assert len(briefing.portfolio_focus) == 1
    assert briefing.top_themes == []
    assert briefing.sector_scan[0].top_events == []
    assert briefing.watchlist_events == []


def test_top_themes_blocks_personal_finance_seo_headline():
    generator = _build_generator()
    weak = NormalisedEvent(
        title="Best CD rates today, May 3, 2026 (lock in up to 4.05% APY)",
        summary="Low-signal personal finance recap with no market catalyst.",
        source="newsapi",
        source_type="news",
        event_type="news_search",
        tickers=[],
        sectors=[],
        personal_relevance_score=0.82,
        factual_confidence_score=0.62,
        cluster_size=1,
        final_score=0.84,
        raw_data={"source_name": "Some Blog"},
    )
    strong = NormalisedEvent(
        title="US naval blockade squeezes Iran's oil exports",
        summary="Shipping routes disrupted and crude risk premium rises.",
        source="finnhub",
        event_type="geopolitical",
        tickers=["XLE"],
        sectors=["energy"],
        personal_relevance_score=0.78,
        factual_confidence_score=0.82,
        cluster_size=7,
        final_score=0.79,
        raw_data={"source_name": "Reuters"},
    )
    themes = generator._build_top_themes([weak, strong], session_mode="saturday")
    assert themes
    assert all("best cd rates" not in evt.title.lower() for evt in themes)
