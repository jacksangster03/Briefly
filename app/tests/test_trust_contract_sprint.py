from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from app.briefing.email_formatter import EmailFormatter
from app.briefing.llm_email_renderer import LLMEmailRenderer
from app.briefing.market_setup_interpreter import interpret_market_setup
from app.briefing.morning_charts import build_morning_chart_bundle, validate_chart_contract
from app.briefing.trust_contract import (
    active_index_quotes,
    chart_copy_is_distinct,
    distinct_lines,
    resolve_canonical_prices,
    run_pre_send_lints,
)
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import MacroDataPoint, NormalisedEvent, PricePoint, QuoteData
from app.settings import Settings


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class _StubMarketData:
    def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d"):
        base = 100.0 + float(abs(hash(symbol)) % 20)
        return [
            PricePoint(symbol=symbol, timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc), close=base - 1.5),
            PricePoint(symbol=symbol, timestamp=datetime(2026, 5, 2, tzinfo=timezone.utc), close=base - 0.5),
            PricePoint(symbol=symbol, timestamp=datetime(2026, 5, 3, tzinfo=timezone.utc), close=base + 0.4),
            PricePoint(symbol=symbol, timestamp=datetime(2026, 5, 4, tzinfo=timezone.utc), close=base + 0.9),
            PricePoint(symbol=symbol, timestamp=datetime(2026, 5, 5, tzinfo=timezone.utc), close=base + 1.1),
        ]


def _load_fixture(name: str) -> MorningBriefing:
    data = json.loads((FIXTURE_DIR / name).read_text())
    briefing = MorningBriefing.model_validate(data)
    if not briefing.global_news:
        briefing.global_news = [
            NormalisedEvent(
                title="Fallback macro headline for fixture consistency",
                summary="Deterministic fixture signal.",
                source="fixture",
                source_type="news",
            )
        ]
    return briefing


def test_energy_stress_fixture_contract_and_snapshots():
    briefing = _load_fixture("trust_contract_energy_stress.json")
    canonical = resolve_canonical_prices(briefing)

    assert canonical["WTI"]["value"] == 102.88
    wti_strip = next(point for point in briefing.commodity_strip if "WTI" in (point.name or "").upper())
    assert wti_strip.value == 102.88

    active_quotes = active_index_quotes(briefing, timezone_name="Europe/Madrid")
    assert all("FTSE" not in (quote.display_name or "").upper() for quote in active_quotes)

    active_setup = MarketSetup(
        index_quotes=active_quotes,
        macro_quotes=briefing.market_setup.macro_quotes,
        market_breadth=briefing.market_setup.market_breadth,
    )
    interpretation = interpret_market_setup(active_setup, briefing.macro_context, global_news=briefing.global_news)
    briefing.market_setup_analysis = interpretation.narrative
    briefing.geo_risk_level = "ELEVATED"
    briefing.geo_risk_raw_level = "LOW"
    briefing.geo_risk_summary = (
        "Geo risk ELEVATED: oil 103 USD/bbl (+0.97%) with active geopolitical headlines. "
        "Inputs: VIX 17.5, oil +0.97%, haven neutral, density 0.11."
    )

    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    assert not any("Asset mismatch" in warning for warning in warnings)
    assert not any("Geo raw/final conflict" in warning for warning in warnings)

    profile = UserProfile(name="fixture", timezone="Europe/Madrid")
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    assert validate_chart_contract(bundle) == []
    assert selected
    assert any(row["chart_key"] == "cross_asset_impulse_strip" for row in selected)


def test_rates_repricing_fixture_contract_and_snapshots():
    briefing = _load_fixture("trust_contract_rates_repricing.json")
    canonical = resolve_canonical_prices(briefing)
    assert canonical["US10Y"]["value"] == 4.62
    assert canonical["US2Y"]["value"] == 4.24

    active_setup = MarketSetup(
        index_quotes=active_index_quotes(briefing, timezone_name="Europe/Madrid"),
        macro_quotes=briefing.market_setup.macro_quotes,
        market_breadth=briefing.market_setup.market_breadth,
    )
    interpretation = interpret_market_setup(active_setup, briefing.macro_context, global_news=briefing.global_news)
    assert "Rates impulse" in interpretation.narrative
    assert interpretation.dominant_driver

    briefing.geo_risk_level = "MODERATE"
    briefing.geo_risk_raw_level = "MODERATE"
    briefing.geo_risk_summary = "Geo risk MODERATE: rates-led repricing with contained commodity stress."
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    assert not any("Geo summary mentions multiple levels" in warning for warning in warnings)

    profile = UserProfile(name="fixture", timezone="Europe/Madrid")
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    assert validate_chart_contract(bundle) == []
    assert selected
    assert any(row["chart_key"] == "global_relative_performance" for row in selected)


def test_chart_copy_triplet_is_distinct_and_outlook_safe():
    formatter = EmailFormatter("Europe/Madrid")
    read, why, lens = formatter._chart_copy_triplet(
        "portfolio_concentration_risk",
        "Top-weight concentration and single-name risk posture.",
    )
    assert chart_copy_is_distinct(read, why, lens)
    assert read and why and lens


def test_distinct_lines_preserves_display_casing_units_and_punctuation():
    read, why, lens = distinct_lines(
        "Nasdaq Composite (COMP) leads CAC 40 by 1.9 rebased points over the 5D window.",
        "Why this matters: 6/11 tracked benchmarks are positive, so breadth is split.",
        "Portfolio lens: Portfolio contribution is +0.10% and 1D 95% VaR is 1.84%.",
    )
    assert "Nasdaq Composite (COMP)" in read
    assert "1.9" in read
    assert "5D" in read
    assert "6/11" in why
    assert "+0.10%" in lens
    assert "1.84%" in lens


def test_lints_flag_rates_conflict_and_proxy_scaled_index_level():
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="SPY", display_name="S&P 500 (SPX)", current_price=720.0, change=1.2, change_percent=0.17),
            ],
            treasury_10y=MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.40, change=-0.02),
        ),
        macro_context=[
            MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.39, change=0.02),
        ],
    )
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    assert any("Index level integrity" in warning for warning in warnings)
    assert any("Rates direction conflict" in warning for warning in warnings)


def test_canonical_price_resolver_does_not_overwrite_spx_with_spy_proxy():
    briefing = MorningBriefing(
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7230.12, change=21.11, change_percent=0.29),
            ],
        ),
        watchlist_quotes=[
            QuoteData(symbol="SPY", display_name="SPDR S&P 500 ETF", current_price=720.65, change=1.99, change_percent=0.28),
        ],
    )
    resolve_canonical_prices(briefing)
    assert briefing.market_setup.index_quotes[0].current_price == 7230.12


def test_llm_payload_is_prose_only_contract():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing = MorningBriefing(
        generated_at=datetime(2026, 5, 4, 8, 0, tzinfo=timezone.utc),
        market_setup=MarketSetup(),
        morning_chart_bundle={
            "charts": [{"chart_key": "global_relative_performance", "variant": "5d_rebased"}],
            "selected": [{"chart_key": "global_relative_performance", "role": "hero"}],
        },
    )
    event = NormalisedEvent(
        title="Sample macro headline",
        summary="Sample summary.",
        source="fixture",
        source_type="news",
        url="https://example.com/sample",
    )
    payload = renderer._build_payload(briefing, [event]).prompt
    assert payload["rules"]["llm_role"] == "prose_only"
    assert "deterministic_contract" in payload["rules"]
    assert "chart_summaries" in payload
    assert "charts" not in payload
