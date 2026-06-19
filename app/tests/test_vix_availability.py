from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.morning_generator import MorningBriefingGenerator
from app.briefing.session_freshness import build_data_basis_lines
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import QuoteData


def test_trim_keeps_vix_even_when_index_cap_is_small():
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        session_key="us_intraday_risk",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500", current_price=7400, change_percent=-0.1),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite", current_price=18500, change_percent=-0.5),
                QuoteData(symbol="^DJI", display_name="Dow Jones", current_price=39000, change_percent=0.1),
                QuoteData(symbol="^RUT", display_name="Russell 2000", current_price=2000, change_percent=-0.6),
                QuoteData(symbol="^STOXX50E", display_name="Euro STOXX 50", current_price=5200, change_percent=0.7),
                QuoteData(symbol="^N225", display_name="Nikkei 225", current_price=39000, change_percent=0.2),
                QuoteData(symbol="^VIX", display_name="VIX", current_price=18.2, change_percent=-1.1),
            ],
            macro_quotes=[],
        ),
    )
    MorningBriefingGenerator._trim_market_snapshot_quotes(briefing, max_index=3, max_macro=0)
    joined = " | ".join(f"{q.display_name} {q.symbol}" for q in briefing.market_setup.index_quotes).upper()
    assert "VIX" in joined


def test_data_basis_reports_vix_unavailable_reason():
    lines = build_data_basis_lines(
        session_key="us_pre_open",
        generated_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        timezone_name="Europe/Madrid",
        index_quotes=[],
        macro_quotes=[],
        watchlist_quotes=[],
    )
    assert any("VIX: unavailable (provider returned no quote this cycle)." in line for line in lines)

