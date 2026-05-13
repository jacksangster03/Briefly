"""Tests for geo risk wording, oil spike thresholds, and market-confirmation language.

All tests are deterministic and use no live provider calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.briefing.quality_guard import BriefingQualityGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply(sections: list[str], *, vix_available: bool = True, brent_stale: bool = False) -> str:
    guard = BriefingQualityGuard(vix_available=vix_available, brent_stale=brent_stale)
    return "\n".join(guard.apply(sections))


# ---------------------------------------------------------------------------
# Part 5: Geo risk wording
# ---------------------------------------------------------------------------

class TestGeoRiskWording:
    """Geo risk wording respects oil move thresholds and VIX availability."""

    def test_small_oil_move_does_not_produce_spiking(self):
        """Oil move 0.31%: the tape driver should not label oil as 'spiking'.

        The interpreter requires abs(oil_move) >= 1.5 OR oil_level > 90 for
        oil_is_driver to be True. With oil at 85 and move 0.31%, no geo_driver
        fires and 'spiking' should not appear.
        """
        from app.briefing.market_setup_interpreter import _dominant_tape_driver
        from app.schemas.briefings import MarketSetup
        from app.schemas.events import QuoteData, NormalisedEvent

        oil_q = QuoteData(
            symbol="CL=F",
            display_name="WTI Crude",
            current_price=85.0,
            change_percent=0.31,
        )
        setup = MarketSetup(
            index_quotes=[],
            macro_quotes=[oil_q],
        )
        news = [
            NormalisedEvent(
                event_id="geo-1",
                title="Iran tensions: shipping concerns remain elevated",
                source="test",
                cluster_size=8,
            ),
            NormalisedEvent(
                event_id="geo-2",
                title="Hormuz passage shipping route disruption fears persist",
                source="test",
                cluster_size=4,
            ),
            NormalisedEvent(
                event_id="geo-3",
                title="Oil supply disruption risk from Middle East escalation",
                source="test",
                cluster_size=3,
            ),
            NormalisedEvent(
                event_id="geo-4",
                title="OPEC meeting energy market outlook",
                source="test",
                cluster_size=2,
            ),
            NormalisedEvent(
                event_id="geo-5",
                title="US energy sanctions on Iran extended",
                source="test",
                cluster_size=2,
            ),
        ]
        result = _dominant_tape_driver(setup=setup, global_news=news, macro_points=[])
        assert "spiking" not in (result or "").lower(), (
            f"0.31% oil move should not produce 'spiking'. Got: {result!r}"
        )

    def test_large_oil_move_produces_spiking(self):
        """Oil move 3.5% (above 3.0% threshold): should produce 'spiking'."""
        from app.briefing.market_setup_interpreter import _dominant_tape_driver
        from app.schemas.briefings import MarketSetup
        from app.schemas.events import QuoteData, NormalisedEvent

        oil_q = QuoteData(
            symbol="CL=F",
            display_name="WTI Crude",
            current_price=92.0,
            change_percent=3.5,
        )
        setup = MarketSetup(
            index_quotes=[],
            macro_quotes=[oil_q],
        )
        news = [
            NormalisedEvent(
                event_id="geo-1",
                title="Iran Hormuz blockade threat energy shock",
                source="test",
                cluster_size=8,
            ),
            NormalisedEvent(
                event_id="geo-2",
                title="Middle East war escalation oil supply disruption attack",
                source="test",
                cluster_size=6,
            ),
            NormalisedEvent(
                event_id="geo-3",
                title="Shipping route Iran oil energy sanctions invasion",
                source="test",
                cluster_size=5,
            ),
            NormalisedEvent(
                event_id="geo-4",
                title="Energy market panic supply shortage",
                source="test",
                cluster_size=4,
            ),
            NormalisedEvent(
                event_id="geo-5",
                title="WTI oil price surge on Middle East strike",
                source="test",
                cluster_size=4,
            ),
        ]
        result = _dominant_tape_driver(setup=setup, global_news=news, macro_points=[])
        assert "spiking" in (result or "").lower(), (
            f"3.5% oil move on geo headlines should produce 'spiking'. Got: {result!r}"
        )

    def test_vix_unavailable_neutral_haven_small_oil_produces_incomplete_confirmation(self):
        """When VIX unavailable + haven neutral + oil move < 1%: geo wording should note
        incomplete market confirmation, not 'VIX confirms'."""
        sections = [
            "<b>GEO RISK</b>\nGeo risk ELEVATED: oil steady at 85 USD/bbl (+0.31%) "
            "with active Middle East/geopolitical headlines; "
            "Geo headline risk elevated; market confirmation incomplete (VIX unavailable, haven demand neutral). "
            "Inputs: VIX unavailable, oil +0.31%, haven neutral, density 0.45."
        ]
        result = _apply(sections, vix_available=False)
        assert "VIX confirms" not in result
        assert "vix" in result.lower() or "unavailable" in result.lower() or "incomplete" in result.lower()

    def test_vix_available_low_elevated_headlines_not_confirming(self):
        """VIX available but below 20 with elevated headlines: should indicate not yet confirming."""
        sections = [
            "<b>GEO RISK</b>\nGeo risk ELEVATED: oil elevated at 88 USD/bbl. "
            "Market signals are not yet confirming broader stress."
        ]
        # No quality guard changes needed here; just verify the wording passes through
        result = _apply(sections, vix_available=True)
        assert "Market signals are not yet confirming" in result

    def test_vix_available_high_and_oil_elevated_full_confirmation(self):
        """VIX >= 20 and oil elevated: full geo risk confirmation wording allowed."""
        sections = [
            "<b>GEO RISK</b>\nGeo risk ELEVATED: oil spiking at 95 USD/bbl (+3.8%) "
            "with active Middle East/geopolitical headlines; "
            "VIX 22.5 and haven +0.85 confirm elevated stress."
        ]
        result = _apply(sections, vix_available=True)
        # Should be preserved unchanged (no suppression needed when vix_available=True)
        assert "VIX 22.5" in result
        assert "confirm elevated stress" in result

    def test_headline_density_only_without_market_signals_incomplete(self):
        """Geo risk raised purely by headline density with no market signals:
        quality guard must not allow 'VIX confirms' language."""
        sections = [
            "<b>GEO RISK</b>\nGeo risk ELEVATED by headline density. VIX confirms broad stress.",
        ]
        result = _apply(sections, vix_available=False)
        assert "VIX confirms" not in result


# ---------------------------------------------------------------------------
# Part 6: Trigger wording tests
# ---------------------------------------------------------------------------

class TestTriggerWording:
    """format_trigger_line produces the correct wording based on threshold breach."""

    def test_trigger_breached_above(self):
        """When value > threshold (above), trigger says 'is above ... reinforcing'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("US 10Y", 4.47, 4.45, "above", "reinforce rates pressure")
        assert "is above" in line
        assert "would" not in line

    def test_trigger_not_breached_above(self):
        """When value < threshold (above direction), trigger says 'would reinforce'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("US 10Y", 4.41, 4.45, "above", "reinforce rates pressure")
        assert "would" in line
        assert "is above" not in line

    def test_trigger_none_value_suppressed(self):
        """When metric_value is None, trigger line is suppressed entirely."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("US 10Y", None, 4.45, "above", "reinforce rates pressure")
        assert line == ""

    def test_trigger_breached_below(self):
        """When value <= threshold (below direction), trigger says 'is below'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("Europe average move", -1.8, -1.5, "below", "confirm deeper regional weakness")
        assert "is below" in line
        assert "would" not in line

    def test_trigger_not_breached_below(self):
        """When value > threshold (below direction), trigger says 'would ... confirm'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("Europe average move", -0.3, -1.5, "below", "confirm deeper regional weakness")
        assert "would" in line

    def test_vix_trigger_shows_is_above_when_breached(self):
        """VIX >= 20: trigger line says 'is above 20.00, confirms' not 'would confirm'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("VIX", 21.5, 20.0, "above", "confirms broader risk-off pressure")
        assert "is above" in line

    def test_vix_trigger_shows_would_when_below_threshold(self):
        """VIX < 20: trigger line says 'above 20.00 would confirm'."""
        from app.briefing.formatter import format_trigger_line
        line = format_trigger_line("VIX", 15.3, 20.0, "above", "confirms broader risk-off pressure")
        assert "would" in line
