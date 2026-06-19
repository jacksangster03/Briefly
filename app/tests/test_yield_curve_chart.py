from __future__ import annotations

from datetime import datetime, timezone

from app.briefing.chart_renderer import ChartRenderer
from app.briefing.morning_charts import _yield_curve_spec
from app.schemas.events import MacroDataPoint


def test_yield_curve_spec_contains_prior_week_values_when_available():
    spec = _yield_curve_spec(
        [
            MacroDataPoint(series_id="DGS2", name="US 2Y", value=4.09, previous_value=3.90),
            MacroDataPoint(series_id="DGS5", name="US 5Y", value=4.20, previous_value=4.05),
            MacroDataPoint(series_id="DGS10", name="US 10Y", value=4.62, previous_value=4.40),
            MacroDataPoint(series_id="DGS30", name="US 30Y", value=5.10, previous_value=4.95),
        ],
        market_data_service=None,
        generated_at=datetime(2026, 5, 19, 6, 0, tzinfo=timezone.utc),
    )
    rows = list(spec.get("series", []))
    assert all(row.get("week_ago") is not None for row in rows)
    assert all(row.get("change_bps") is not None for row in rows)


def test_yield_curve_renderer_handles_missing_prior_week_tenor():
    spec = _yield_curve_spec(
        [
            MacroDataPoint(series_id="DGS2", name="US 2Y", value=4.09, previous_value=3.90),
            MacroDataPoint(series_id="DGS5", name="US 5Y", value=4.20),
            MacroDataPoint(series_id="DGS10", name="US 10Y", value=4.62, previous_value=4.40),
            MacroDataPoint(series_id="DGS30", name="US 30Y", value=5.10, previous_value=4.95),
        ],
        market_data_service=None,
        generated_at=datetime(2026, 5, 19, 6, 0, tzinfo=timezone.utc),
    )
    renderer = ChartRenderer()
    asset = renderer.render_yield_curve_from_spec(spec)
    assert asset is not None
    assert asset.key == "yield_curve_shape"

