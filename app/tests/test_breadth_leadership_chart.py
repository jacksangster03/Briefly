from __future__ import annotations

from app.briefing.morning_charts import _breadth_leadership_spec


def test_breadth_chart_does_not_repeat_regional_average_bars():
    spec = _breadth_leadership_spec(
        {
            "total_indices": 9,
            "up_indices": 5,
            "breadth": 0.56,
            "us_avg": -0.12,
            "eu_avg": 0.44,
            "asia_avg": 0.05,
            "small_vs_large": -0.61,
            "growth_vs_defensive": -0.34,
        }
    )
    names = [str(row.get("name")) for row in spec.get("series", [])]
    assert "US Avg Move" not in names
    assert "Europe Avg Move" not in names
    assert "Asia Avg Move" not in names
    assert "Breadth % Up" in names
    assert "Small-Large" in names
    assert "Growth-Defensive" in names

