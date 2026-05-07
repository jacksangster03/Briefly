from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.briefing.watchlist_chart_service import build_watchlist_chart_spec
from app.personalization.user_profile import UserProfile
from app.schemas.events import PricePoint


class _StubMarketData:
    def __init__(self):
        self.now = datetime(2026, 5, 7, 14, 0, tzinfo=timezone.utc)

    def get_price_history(self, symbol: str, period: str = "1mo", interval: str = "1d"):
        base = {
            "NVDA": [100.0, 103.0, 107.0],
            "MSFT": [100.0, 101.0, 102.0],
            "AAPL": [100.0, 99.0, 98.0],
            "SPY": [100.0, 100.5, 101.0],
        }.get(symbol, [])
        points: list[PricePoint] = []
        for idx, value in enumerate(base):
            points.append(
                PricePoint(
                    symbol=symbol,
                    timestamp=self.now - timedelta(days=3 - idx),
                    close=value,
                    source="stub",
                )
            )
        return points


def _profile() -> UserProfile:
    return UserProfile(
        name="default_user",
        watchlist_primary=["NVDA", "MSFT"],
        watchlist_secondary=["AAPL"],
        watchlist_monitor=["AMD"],
    )


def test_rebased_starts_at_100_and_return_math():
    payload = build_watchlist_chart_spec(
        profile=_profile(),
        period="1M",
        mode="rebased",
        benchmark="none",
        symbols=["NVDA"],
        market_data_service=_StubMarketData(),
    )
    points = payload["series"][0]["points"]
    assert points[0]["rebased_100"] == 100.0
    assert round(points[-1]["return_pct"], 2) == 7.0


def test_relative_vs_benchmark_math():
    payload = build_watchlist_chart_spec(
        profile=_profile(),
        period="1M",
        mode="relative",
        benchmark="SPY",
        symbols=["NVDA"],
        market_data_service=_StubMarketData(),
    )
    points = payload["series"][0]["points"]
    # NVDA +7.0%, SPY +1.0% => relative +6.0%
    assert round(points[-1]["relative_vs_benchmark"], 2) == 6.0
    assert round(points[-1]["value"], 2) == 6.0


def test_missing_symbol_history_returns_warning_without_crash():
    payload = build_watchlist_chart_spec(
        profile=_profile(),
        period="1M",
        mode="rebased",
        benchmark="none",
        symbols=["ZZZZ"],
        market_data_service=_StubMarketData(),
    )
    assert payload["series"][0]["available"] is False
    assert payload["warnings"]


def test_default_symbol_resolution_is_deterministic_top_10():
    profile = UserProfile(
        name="default_user",
        watchlist_primary=["A", "B", "C", "D", "E"],
        watchlist_secondary=["F", "G", "H"],
        watchlist_monitor=["I", "J", "K"],
    )
    payload = build_watchlist_chart_spec(
        profile=profile,
        period="1M",
        mode="rebased",
        benchmark="none",
        symbols=None,
        market_data_service=_StubMarketData(),
    )
    assert payload["symbols"] == ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]


def test_summary_metrics_and_basis_shape_present():
    payload = build_watchlist_chart_spec(
        profile=_profile(),
        period="1M",
        mode="return",
        benchmark="SPY",
        symbols=["NVDA", "AAPL"],
        market_data_service=_StubMarketData(),
    )
    summary = payload["summary"]
    assert summary["best_performer"]["symbol"] == "NVDA"
    assert summary["worst_performer"]["symbol"] == "AAPL"
    assert "line" in payload["data_basis"]
    assert isinstance(payload["warnings"], list)

