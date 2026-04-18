"""Phase 5.7A randomized fuzz validation tests."""

from __future__ import annotations

from app.validation.fuzz import run_fuzz_validation


def test_fuzz_validation_runs_and_reports_summary(
    validation_test_settings,
    stubbed_validation_risk_service,
):
    report = run_fuzz_validation(
        settings=validation_test_settings,
        profile_name="default_user",
        cases=5,
        seed=123,
    )
    assert report["summary"]["total_cases"] == 5
    assert report["summary"]["passed_cases"] + report["summary"]["failed_cases"] == 5
    assert isinstance(report["cases"], list)
    assert len(report["cases"]) == 5
    assert report["status"] in {"pass", "fail"}
