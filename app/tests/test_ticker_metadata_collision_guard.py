from __future__ import annotations

from app.universe.ticker_metadata import (
    extract_tickers_from_text,
    is_collision_prone_symbol,
)


def test_is_collision_prone_symbol_true_for_known_collisions():
    assert is_collision_prone_symbol("ALL")
    assert is_collision_prone_symbol("low")  # case-insensitive
    assert is_collision_prone_symbol("ON")


def test_is_collision_prone_symbol_false_for_unambiguous_tickers():
    assert not is_collision_prone_symbol("NVDA")
    assert not is_collision_prone_symbol("AAPL")


def test_extract_tickers_from_text_does_not_match_custard_apple_to_aapl():
    """Regression guard for the false-positive collision named in
    docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md Part 17.2: a phrase like
    "custard apple" should never resolve to the AAPL ticker.
    """
    tickers = extract_tickers_from_text("Custard apple season has started in the region.")
    assert "AAPL" not in tickers


def test_extract_tickers_from_text_still_matches_real_company_mentions():
    tickers = extract_tickers_from_text("Apple unveiled a new chip today.")
    assert "AAPL" in tickers


def test_extract_tickers_from_text_matches_all_caps_headline():
    tickers = extract_tickers_from_text("APPLE RAISES GUIDANCE ON STRONG DEMAND")
    assert "AAPL" in tickers


def test_extract_tickers_from_text_ignores_apple_mid_sentence_lowercase():
    tickers = extract_tickers_from_text("She picked an apple from the orchard.")
    assert "AAPL" not in tickers


def test_cleaners_no_longer_exports_unsafe_duplicate_extractor():
    """The unsafe, registry-free extract_tickers_from_text in cleaners.py was
    dead code (no production callers) and shared a name with the safe,
    registry-validated version in app.universe.ticker_metadata. Removed in
    Phase 2 of the analyst-first redesign to eliminate the footgun.
    """
    import app.processing.cleaners as cleaners

    assert not hasattr(cleaners, "extract_tickers_from_text")
