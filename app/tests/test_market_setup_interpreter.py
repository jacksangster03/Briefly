from datetime import datetime, timezone

from app.briefing.market_setup_interpreter import interpret_market_setup
from app.schemas.briefings import MarketSetup
from app.schemas.events import MacroDataPoint, MarketBreadth, QuoteData


def _quote(symbol: str, name: str, change_pct: float) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=name,
        current_price=100.0,
        change=change_pct,
        change_percent=change_pct,
        timestamp=datetime.now(timezone.utc),
    )


def test_interpreter_risk_on_regime():
    setup = MarketSetup(
        index_quotes=[
            _quote("^GSPC", "S&P 500 (SPX)", 1.2),
            _quote("^IXIC", "Nasdaq Composite (COMP)", 1.5),
            _quote("^DJI", "Dow Jones (DJIA)", 1.1),
            _quote("^STOXX50E", "EURO STOXX 50", 1.8),
            _quote("^N225", "Nikkei 225", 0.4),
            _quote("^VIX", "VIX", -2.1),
        ],
        macro_quotes=[
            _quote("CL=F", "WTI Crude Oil (CL1:COM)", -4.4),
            _quote("GC=F", "Gold (GC1:COM)", -0.8),
        ],
    )
    macro = [
        MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.32, change=-0.04),
        MacroDataPoint(series_id="SPREAD", name="10Y-2Y Yield Spread", value=0.55, change=0.02),
    ]
    result = interpret_market_setup(setup, macro)
    assert "risk-on" in result.narrative.lower()
    assert result.confidence in {"high", "medium"}
    assert "risk_on" in result.tags


def test_interpreter_mixed_contradictory_signals_low_confidence():
    setup = MarketSetup(
        index_quotes=[
            _quote("^GSPC", "S&P 500 (SPX)", 0.3),
            _quote("^STOXX50E", "EURO STOXX 50", -0.4),
            _quote("^N225", "Nikkei 225", 0.1),
            _quote("^VIX", "VIX", 0.1),
        ],
        macro_quotes=[_quote("CL=F", "WTI Crude Oil (CL1:COM)", 0.0)],
    )
    macro = [
        MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.32, change=0.0),
        MacroDataPoint(series_id="SPREAD", name="10Y-2Y Yield Spread", value=0.55, change=0.0),
    ]
    result = interpret_market_setup(setup, macro)
    assert "mixed" in result.narrative.lower()
    assert result.confidence == "low"


def test_interpreter_risk_off_with_energy_pressure():
    setup = MarketSetup(
        index_quotes=[
            _quote("^GSPC", "S&P 500 (SPX)", -1.2),
            _quote("^IXIC", "Nasdaq Composite (COMP)", -1.8),
            _quote("^FTSE", "FTSE 100", -0.9),
            _quote("^HSI", "Hang Seng", -1.1),
            _quote("^VIX", "VIX", 4.5),
        ],
        macro_quotes=[
            _quote("CL=F", "WTI Crude Oil (CL1:COM)", 6.2),
            _quote("GC=F", "Gold (GC1:COM)", 2.0),
        ],
    )
    macro = [
        MacroDataPoint(series_id="UST10Y", name="US 10Y Treasury Yield", value=4.32, change=0.05),
        MacroDataPoint(series_id="SPREAD", name="10Y-2Y Yield Spread", value=0.55, change=-0.02),
    ]
    result = interpret_market_setup(setup, macro)
    assert "risk-off" in result.narrative.lower()
    assert "commodity_pressure" in result.tags


def test_interpreter_mentions_weak_sector_breadth_when_indices_are_split():
    setup = MarketSetup(
        index_quotes=[
            _quote("^GSPC", "S&P 500 (SPX)", 0.4),
            _quote("^IXIC", "Nasdaq Composite (COMP)", 0.8),
            _quote("^STOXX50E", "EURO STOXX 50", -0.8),
            _quote("^N225", "Nikkei 225", 0.7),
            _quote("^VIX", "VIX", 3.2),
        ],
        macro_quotes=[_quote("CL=F", "WTI Crude Oil (CL1:COM)", 0.2)],
        market_breadth=[
            MarketBreadth(symbol="XLK", display_name="Technology", change_percent=-0.4),
            MarketBreadth(symbol="XLF", display_name="Financials", change_percent=-0.2),
            MarketBreadth(symbol="XLE", display_name="Energy", change_percent=0.1),
            MarketBreadth(symbol="XLV", display_name="Health Care", change_percent=-0.3),
            MarketBreadth(symbol="XLI", display_name="Industrials", change_percent=-0.5),
            MarketBreadth(symbol="XLP", display_name="Consumer Staples", change_percent=-0.1),
        ],
    )
    result = interpret_market_setup(setup, [])
    assert "Sector breadth is weak" in result.narrative


def test_interpreter_mentions_mixed_index_breadth_but_supportive_sector_breadth():
    setup = MarketSetup(
        index_quotes=[
            _quote("^GSPC", "S&P 500 (SPX)", 0.1),
            _quote("^IXIC", "Nasdaq Composite (COMP)", 0.5),
            _quote("^STOXX50E", "EURO STOXX 50", -0.7),
            _quote("^DAX", "DAX", -0.6),
            _quote("^VIX", "VIX", 1.8),
        ],
        market_breadth=[
            MarketBreadth(symbol="XLK", display_name="Technology", change_percent=0.9),
            MarketBreadth(symbol="XLF", display_name="Financials", change_percent=0.5),
            MarketBreadth(symbol="XLE", display_name="Energy", change_percent=0.3),
            MarketBreadth(symbol="XLV", display_name="Health Care", change_percent=0.6),
            MarketBreadth(symbol="XLI", display_name="Industrials", change_percent=0.7),
            MarketBreadth(symbol="XLP", display_name="Consumer Staples", change_percent=0.1),
            MarketBreadth(symbol="XLU", display_name="Utilities", change_percent=0.2),
            MarketBreadth(symbol="XLY", display_name="Consumer Discretionary", change_percent=0.8),
            MarketBreadth(symbol="XLB", display_name="Materials", change_percent=-0.2),
            MarketBreadth(symbol="XLRE", display_name="Real Estate", change_percent=-0.1),
            MarketBreadth(symbol="XLC", display_name="Communication Services", change_percent=-0.4),
            MarketBreadth(symbol="SMH", display_name="Semiconductors", change_percent=0.4),
        ],
    )
    result = interpret_market_setup(setup, [])
    assert (
        "Equity breadth is balanced" in result.narrative
        or "Index breadth is mixed-to-weak" in result.narrative
    )
    assert "Sector breadth is supportive" in result.narrative or "sector breadth has improved" in result.narrative
