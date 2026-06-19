"""Tests for ticker/news relevance guard (Part 8)."""
from __future__ import annotations
from datetime import datetime, timezone

from app.briefing.trust_contract import run_pre_send_lints
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import NormalisedEvent, QuoteData


def _event(title: str, tickers: list[str], summary: str = "") -> NormalisedEvent:
    return NormalisedEvent(
        event_id=f"test_{title[:10]}",
        title=title,
        summary=summary,
        source="test",
        tickers=tickers,
        cluster_id=f"c_{title[:10]}",
        content_hash=f"h_{title[:10]}",
    )


def test_custard_apples_not_mapped_to_aapl():
    """'Custard apples' must not be confirmed as an AAPL item."""
    evt = _event("Custard apples see record harvest season", ["AAPL"])
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        top_themes=[evt],
    )
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    warning_text = " ".join(str(w) for w in warnings)
    assert "AAPL" in warning_text or "Irrelevant" in warning_text or "custard" in warning_text.lower(), (
        f"Expected a warning for custard apples mapped to AAPL. Warnings: {warnings}"
    )


def test_warby_parker_glasses_not_mapped_to_googl_without_google_mention():
    """'Warby Parker AI glasses' without Google mention must not map to GOOGL."""
    evt = _event(
        "Warby Parker launches new AI glasses line",
        ["GOOGL"],
        summary="Warby Parker has released a new product line of smart glasses.",
    )
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        top_themes=[evt],
    )
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    warning_text = " ".join(str(w) for w in warnings)
    assert "GOOGL" in warning_text or "glasses" in warning_text.lower(), (
        f"Expected GOOGL glasses warning. Warnings: {warnings}"
    )


def test_google_glasses_valid_when_google_mentioned():
    """'Google AI glasses' must be allowed for GOOGL."""
    evt = _event(
        "Google launches new AI glasses at I/O",
        ["GOOGL"],
        summary="Alphabet's Google division unveiled smart glasses at Google I/O.",
    )
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        top_themes=[evt],
    )
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    # Should NOT warn when Google is explicitly mentioned
    glasses_googl_warning = any(
        "GOOGL" in str(w) and ("glasses" in str(w).lower() or "eyewear" in str(w).lower())
        for w in warnings
    )
    assert not glasses_googl_warning, (
        f"Should not warn when Google is mentioned in context. Warnings: {warnings}"
    )


def test_apple_earnings_not_flagged():
    """Legitimate Apple earnings news must not trigger a false positive."""
    evt = _event(
        "Apple Q2 earnings beat estimates; iPhone sales strong",
        ["AAPL"],
        summary="Apple reported Q2 2026 results, beating analyst estimates on EPS and revenue.",
    )
    briefing = MorningBriefing(
        generated_at=datetime.now(timezone.utc),
        top_themes=[evt],
    )
    warnings = run_pre_send_lints(briefing, timezone_name="Europe/Madrid")
    # custard/irrelevant check should NOT fire for legitimate Apple news
    irrelevant_warnings = [
        w for w in warnings
        if "AAPL" in str(w) and ("Irrelevant" in str(w) or "custard" in str(w).lower())
    ]
    assert not irrelevant_warnings, f"False positive warning for Apple earnings: {warnings}"
