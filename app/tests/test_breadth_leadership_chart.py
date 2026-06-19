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


def test_breadth_chart_omits_unavailable_optional_factors():
    spec = _breadth_leadership_spec(
        {
            "total_indices": 4,
            "up_indices": 2,
            "breadth": 0.5,
            "small_vs_large": 0.2,
            "growth_vs_defensive": -0.1,
            "semis_vs_market": None,
        }
    )
    names = [str(row.get("name")) for row in spec.get("series", [])]
    assert "Semis-Market" not in names
