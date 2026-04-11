"""Phase 3 tests: holdings import, persistence, and portfolio-aware relevance."""

from __future__ import annotations

from datetime import date, datetime
import pytest
from sqlalchemy.orm import sessionmaker

from app.briefing.formatter import TelegramFormatter
from app.briefing.intraday_generator import IntradayGenerator
from app.db import session as db_session
from app.db.base import Base, create_app_engine
from app.db.models import PortfolioHolding as PortfolioHoldingRow
from app.db.session import get_session
from app.personalization.user_profile import UserProfile, load_user_profile
from app.portfolio.importer import load_holdings_file
from app.portfolio.service import load_active_holdings, replace_holdings_snapshot
from app.processing.relevance_scoring import score_event
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent
from app.schemas.portfolio import PortfolioHolding
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse


@pytest.fixture
def isolated_db(tmp_path):
    """Swap DB session globals to an isolated SQLite file for this test."""
    old_engine = db_session._engine
    old_factory = db_session._SessionLocal
    db_path = tmp_path / "portfolio_test.db"
    engine = create_app_engine(f"sqlite:///{db_path}")
    db_session._engine = engine
    db_session._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        db_session._engine = old_engine
        db_session._SessionLocal = old_factory
        engine.dispose()


def test_holdings_yaml_import(tmp_path):
    path = tmp_path / "holdings.yaml"
    path.write_text(
        "\n".join(
            [
                "profile: default_user",
                "as_of_date: 2026-04-11",
                "holdings:",
                "  - symbol: nvda",
                "    weight_pct: 8.5",
                "    bucket: core",
                "  - symbol: msft",
                "    weight_pct: 7.0",
            ]
        )
    )

    snapshot = load_holdings_file(path)
    assert snapshot.profile_name == "default_user"
    assert snapshot.as_of_date == date(2026, 4, 11)
    assert [holding.symbol for holding in snapshot.holdings] == ["NVDA", "MSFT"]
    assert snapshot.holdings[0].bucket == "core"


def test_holdings_csv_import(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text(
        "\n".join(
            [
                "symbol,weight_pct,shares,avg_cost,account,bucket",
                "nvda,8.5,,,IBKR,core",
                "msft,7.0,,,IBKR,core",
            ]
        )
    )

    snapshot = load_holdings_file(path, default_profile="default_user")
    assert len(snapshot.holdings) == 2
    assert snapshot.holdings[0].symbol == "NVDA"
    assert snapshot.holdings[1].account == "IBKR"


def test_holdings_csv_rejects_missing_symbol(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text(
        "\n".join(
            [
                "symbol,weight_pct,shares",
                ",8.5,10",
            ]
        )
    )
    with pytest.raises(ValueError, match="Missing symbol"):
        load_holdings_file(path)


def test_holdings_persistence_replace_and_load(isolated_db):
    first_snapshot = [
        PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.5, bucket="core"),
        PortfolioHolding(profile_name="default_user", symbol="MSFT", weight_pct=7.0, bucket="core"),
    ]
    imported_first = replace_holdings_snapshot("default_user", first_snapshot, as_of_date=date(2026, 4, 11))
    assert imported_first == 2

    loaded_first = load_active_holdings("default_user")
    assert [h.symbol for h in loaded_first] == ["NVDA", "MSFT"]

    second_snapshot = [
        PortfolioHolding(profile_name="default_user", symbol="AAPL", weight_pct=6.0, bucket="core"),
    ]
    imported_second = replace_holdings_snapshot("default_user", second_snapshot, as_of_date=date(2026, 4, 12))
    assert imported_second == 1

    loaded_second = load_active_holdings("default_user")
    assert [h.symbol for h in loaded_second] == ["AAPL"]

    with get_session() as session:
        total_rows = session.query(PortfolioHoldingRow).filter(
            PortfolioHoldingRow.profile_name == "default_user"
        ).count()
        active_rows = session.query(PortfolioHoldingRow).filter(
            PortfolioHoldingRow.profile_name == "default_user",
            PortfolioHoldingRow.active.is_(True),
        ).count()
    assert total_rows == 3
    assert active_rows == 1


def test_load_user_profile_includes_persisted_holdings(isolated_db, tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "user_profile.example.yaml").write_text(
        "\n".join(
            [
                "user:",
                "  name: default_user",
                "  timezone: Europe/Madrid",
                "coverage_weights:",
                "  us: 1.0",
                "sector_weights:",
                "  semiconductors: 1.0",
            ]
        )
    )
    (config_dir / "watchlists.example.yaml").write_text("primary: [NVDA]\nsecondary: []\nmonitor: []\n")
    (config_dir / "sectors.yaml").write_text(
        "\n".join(
            [
                "sectors:",
                "  semiconductors:",
                "    etf: SMH",
                "    display_name: Semiconductors",
                "    key_names: [NVDA, AMD]",
                "indices: []",
                "macro_instruments: []",
            ]
        )
    )

    replace_holdings_snapshot(
        "default_user",
        [
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="AMD", weight_pct=3.0, bucket="satellite"),
        ],
        as_of_date=date(2026, 4, 11),
    )

    settings = Settings(configs_dir=str(config_dir))
    profile = load_user_profile(settings)
    assert profile.has_portfolio
    assert profile.portfolio_symbols[0] == "NVDA"
    assert profile.portfolio_sector_weights.get("semiconductors", 0) > 0


def test_portfolio_relevance_boosts_direct_holding():
    profile = UserProfile(
        watchlist_primary=[],
        sector_weights={"semiconductors": 0.9},
        coverage_weights={"us": 1.0},
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core")
        ],
        portfolio_sector_weights={"semiconductors": 0.65},
    )
    event = NormalisedEvent(
        title="Nvidia raises AI accelerator guidance",
        source="finnhub",
        tickers=["NVDA"],
        sectors=["semiconductors"],
        event_type="company_news",
        factual_confidence_score=0.75,
    )
    scored = score_event(event, profile)
    assert scored.personal_relevance_score >= 0.95
    assert scored.raw_data.get("portfolio_relevance", 0) >= 0.95
    assert "portfolio_holdings=[NVDA]" in scored.score_explanation


def test_portfolio_sector_readthrough_boost():
    profile = UserProfile(
        watchlist_primary=[],
        sector_weights={"semiconductors": 0.4},
        coverage_weights={"us": 1.0},
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core")
        ],
        portfolio_sector_weights={"semiconductors": 0.60},
    )
    event = NormalisedEvent(
        title="Chip supply bottlenecks tighten again",
        source="finnhub",
        tickers=[],
        sectors=["semiconductors"],
        event_type="market_news",
        factual_confidence_score=0.75,
    )
    scored = score_event(event, profile)
    assert scored.raw_data.get("portfolio_relevance", 0) >= 0.65
    assert scored.personal_relevance_score >= 0.65


def test_formatter_renders_portfolio_focus_section():
    formatter = TelegramFormatter("Europe/Madrid")
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 14, 8, 45),
        portfolio_focus=[
            NormalisedEvent(
                title="Nvidia supplier warns on CoWoS bottlenecks",
                summary="Packaging constraints may cap near-term AI server deliveries.",
                tickers=["NVDA"],
                event_type="company_news",
            )
        ],
        events_fetched=10,
        events_after_dedup=5,
        events_sent=1,
    )
    messages = formatter.format_morning_briefing(briefing)
    full = "\n".join(messages)
    assert "PORTFOLIO FOCUS" in full
    assert "Nvidia supplier warns on CoWoS bottlenecks" in full


def test_intraday_generator_prioritizes_held_name_tie_break():
    generator = IntradayGenerator(
        settings=Settings(),
        profile=UserProfile(
            watchlist_primary=[],
            portfolio_holdings=[
                PortfolioHolding(profile_name="default_user", symbol="NVDA", weight_pct=8.0, bucket="core")
            ],
            portfolio_sector_weights={"semiconductors": 0.6},
        ),
        universe=SectorUniverse([], [], []),
        market_data=object(),  # not used by _prioritize_for_portfolio
        news_data=object(),    # not used by _prioritize_for_portfolio
    )
    events = [
        NormalisedEvent(
            title="Generic macro headline",
            tickers=[],
            sectors=["energy"],
            final_score=0.80,
            event_type="market_news",
        ),
        NormalisedEvent(
            title="Nvidia wins major hyperscaler order",
            tickers=["NVDA"],
            sectors=["semiconductors"],
            final_score=0.78,
            event_type="company_news",
        ),
    ]
    prioritized = generator._prioritize_for_portfolio(events)
    assert prioritized[0].title == "Nvidia wins major hyperscaler order"
