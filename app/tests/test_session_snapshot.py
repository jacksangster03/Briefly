from __future__ import annotations

from app.briefing.session_snapshot import build_what_changed_lines


def test_morning_what_changed_suppresses_noisy_flat_lines() -> None:
    previous = {
        "wti_pct": -4.81,
        "brent_pct": -4.81,
        "us_avg_pct": 0.12,
        "eu_avg_pct": -0.22,
        "asia_avg_pct": 0.33,
    }
    current = {
        "wti_pct": -4.81,
        "brent_pct": -4.81,
        "us_avg_pct": 0.12,
        "eu_avg_pct": -0.22,
        "asia_avg_pct": 0.33,
    }
    lines = build_what_changed_lines(previous=previous, current=current)
    joined = " | ".join(lines).lower()
    assert "wti: -4.81% -> -4.81%" not in joined
    assert "brent: -4.81% -> -4.81%" not in joined
    assert "us avg: 0.12% -> 0.12%" not in joined


def test_sector_breadth_delta_is_count_based_wording() -> None:
    previous = {"breadth_up_count": 2.0, "breadth_total_count": 12.0}
    current = {"breadth_up_count": 5.0, "breadth_total_count": 12.0}
    lines = build_what_changed_lines(previous=previous, current=current)
    text = " ".join(lines)
    assert "2/12" in text
    assert "5/12" in text
    assert "improved" in text.lower() or "weakened" in text.lower()


def test_brent_stale_note_when_wti_moves_and_brent_unchanged() -> None:
    previous = {"wti_pct": -4.13, "brent_pct": -4.81}
    current = {"wti_pct": -5.61, "brent_pct": -4.81}
    lines = build_what_changed_lines(previous=previous, current=current)
    assert any("brent" in line.lower() and "stale/provider-held" in line.lower() for line in lines)
