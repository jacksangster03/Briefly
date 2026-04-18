"""Phase 5.7A parameter sweep tests."""

from __future__ import annotations

from app.validation.sweeps import run_parameter_sweep


def test_top_holding_sweep_is_monotonic(
    validation_test_settings,
    stubbed_validation_risk_service,
):
    report = run_parameter_sweep(
        settings=validation_test_settings,
        profile_name="default_user",
        preset_name="balanced_60_40",
        dimension="top_holding_pct",
        values=[10.0, 15.0, 22.0, 30.0],
    )
    assert report["status"] == "pass"
    assert report["monotonic_check"]["passed"] is True
    series = report["monotonic_check"]["series"]
    assert len(series) == 4
    assert series[0] <= series[-1]


def test_equity_expected_return_sweep_is_monotonic(
    validation_test_settings,
    stubbed_validation_risk_service,
):
    report = run_parameter_sweep(
        settings=validation_test_settings,
        profile_name="default_user",
        preset_name="global_multi_asset",
        dimension="equity_expected_return_pct",
        values=[6.0, 7.5, 9.0, 11.0],
    )
    assert report["status"] == "pass"
    assert report["monotonic_check"]["passed"] is True
    series = report["monotonic_check"]["series"]
    assert len(series) == 4
    assert series[0] <= series[-1]
