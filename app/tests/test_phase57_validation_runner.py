"""Phase 5.7A: preset and validation runner tests."""

from __future__ import annotations

from app.validation.presets import get_preset, list_preset_names, list_preset_summaries
from app.validation.runner import apply_preset, run_preset_validation


def test_presets_are_listed_and_resolvable():
    names = list_preset_names()
    assert "concentrated_ai_growth" in names
    assert "balanced_60_40" in names
    summaries = list_preset_summaries()
    assert any(item["name"] == "allocation_drift_case" for item in summaries)

    preset = get_preset("single_name_breach_case")
    assert preset["policy"]["single_name_limit_percent"] == 12.0
    assert len(preset["holdings"]) >= 5


def test_run_preset_validation_returns_structured_report(
    validation_test_settings,
    stubbed_validation_risk_service,
):
    apply_result = apply_preset(
        profile_name="default_user",
        preset_name="allocation_drift_case",
    )
    assert apply_result["holdings_imported"] > 0

    report = run_preset_validation(
        settings=validation_test_settings,
        preset_name="allocation_drift_case",
        profile_name="default_user",
    )
    assert report["preset"] == "allocation_drift_case"
    assert report["summary"]["holdings_count"] > 0
    assert report["summary"]["rebalance_trades"] >= 1
    assert report["summary"]["cma_available"] is True
    assert isinstance(report["failed_checks"], list)
    assert isinstance(report["failed_invariants"], list)
