from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.session_freshness import (
    FRESHNESS_NEAR_REAL_TIME,
    FRESHNESS_PRIOR_CLOSE,
    FRESHNESS_STALE,
    build_data_basis_lines,
    build_freshness_map,
    classify_quote_freshness,
)
from app.schemas.events import QuoteData


def _q(symbol: str, name: str, ts: datetime, pct: float = 0.0) -> QuoteData:
    return QuoteData(
        symbol=symbol,
        display_name=name,
        current_price=100.0,
        change_percent=pct,
        timestamp=ts,
        source="yfinance",
    )


def test_us_equity_before_open_prior_close_labelled():
    generated = datetime(2026, 5, 7, 11, 30, tzinfo=timezone.utc)  # 13:30 CEST pre-open
    quote_ts = datetime(2026, 5, 6, 20, 0, tzinfo=timezone.utc)    # prior close-ish
    meta = classify_quote_freshness(
        quote=_q("AMD", "AMD", quote_ts, 18.61),
        generated_at=generated,
        session_key="us_pre_open",
        timezone_name="Europe/Madrid",
    )
    assert meta.freshness_state == FRESHNESS_PRIOR_CLOSE
    assert "prior close" in meta.freshness_label.lower()


def test_us_equity_intraday_fresh_labelled_near_real_time():
    generated = datetime(2026, 5, 7, 14, 24, tzinfo=timezone.utc)
    quote_ts = datetime(2026, 5, 7, 14, 20, tzinfo=timezone.utc)
    meta = classify_quote_freshness(
        quote=_q("AMD", "AMD", quote_ts, 1.12),
        generated_at=generated,
        session_key="us_intraday_risk",
        timezone_name="Europe/Madrid",
    )
    assert meta.freshness_state == FRESHNESS_NEAR_REAL_TIME
    assert meta.should_show_as_live is True


def test_old_timestamp_during_open_marked_stale():
    generated = datetime(2026, 5, 7, 15, 30, tzinfo=timezone.utc)
    quote_ts = datetime(2026, 5, 7, 13, 0, tzinfo=timezone.utc)
    meta = classify_quote_freshness(
        quote=_q("SPX", "S&P 500", quote_ts, 0.2),
        generated_at=generated,
        session_key="us_intraday_risk",
        timezone_name="Europe/Madrid",
    )
    assert meta.freshness_state == FRESHNESS_STALE


def test_build_freshness_map_contains_symbol_keys():
    generated = datetime(2026, 5, 7, 14, 24, tzinfo=timezone.utc)
    quote_ts = datetime(2026, 5, 7, 14, 20, tzinfo=timezone.utc)
    quotes = [_q("AMD", "AMD", quote_ts, 1.12)]
    fmap = build_freshness_map(
        quotes=quotes,
        generated_at=generated,
        session_key="us_intraday_risk",
        timezone_name="Europe/Madrid",
    )
    assert "AMD" in fmap


def test_preopen_data_basis_labels_us_equities_prior_close():
    generated = datetime(2026, 5, 7, 11, 30, tzinfo=timezone.utc)
    quote_ts = datetime(2026, 5, 6, 20, 0, tzinfo=timezone.utc)
    lines = build_data_basis_lines(
        session_key="us_pre_open",
        generated_at=generated,
        timezone_name="Europe/Madrid",
        index_quotes=[],
        macro_quotes=[],
        watchlist_quotes=[_q("AMD", "AMD", quote_ts, 18.61)],
    )
    assert any("us cash indices" in line.lower() for line in lines)
    assert any("prior close" in line.lower() for line in lines)


def test_europe_open_prior_close_conflict_is_explicitly_labeled():
    generated = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)  # 11:00 CEST Europe Midday
    stale_eu = _q("^STOXX50E", "Euro Stoxx 50", datetime(2026, 5, 6, 20, 0, tzinfo=timezone.utc), -0.2)
    lines = build_data_basis_lines(
        session_key="europe_midday",
        generated_at=generated,
        timezone_name="Europe/Madrid",
        index_quotes=[stale_eu],
        macro_quotes=[],
        watchlist_quotes=[],
    )
    assert any("europe cash is open but provider quotes are prior close/delayed" in line.lower() for line in lines)


def test_us_preopen_does_not_claim_cash_indices_live_without_cash_updates():
    generated = datetime(2026, 5, 7, 11, 30, tzinfo=timezone.utc)  # 13:30 CEST
    spx_prior = _q("^GSPC", "S&P 500", datetime(2026, 5, 6, 20, 0, tzinfo=timezone.utc), 1.4)
    amd_live = _q("AMD", "AMD", datetime(2026, 5, 7, 11, 25, tzinfo=timezone.utc), -1.2)
    lines = build_data_basis_lines(
        session_key="us_pre_open",
        generated_at=generated,
        timezone_name="Europe/Madrid",
        index_quotes=[spx_prior],
        macro_quotes=[],
        watchlist_quotes=[amd_live],
    )
    joined = " | ".join(lines).lower()
    assert "us cash indices: prior close" in joined
    assert "watchlist/pre-market proxies: near-real-time" in joined
