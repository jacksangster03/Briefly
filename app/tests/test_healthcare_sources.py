from __future__ import annotations

from datetime import datetime, timezone

from app.healthcare.schemas import HealthcareSourceEvent
from app.verticals.healthcare_bridge import healthcare_source_to_vertical_event


def test_clinicaltrials_sample_normalises_to_vertical_event():
    src = HealthcareSourceEvent(
        source_key="clinicaltrials",
        source_tier="official",
        stable_event_key="abc",
        title="Phase 3 diabetes trial completed",
        summary="Topline expected soon",
        source_url="https://clinicaltrials.gov/study/NCT1",
        published_at=datetime.now(timezone.utc),
        healthcare_event_type="trial_completion",
        tickers=["LLY"],
        confidence=0.9,
    )
    evt = healthcare_source_to_vertical_event(src)
    assert evt.vertical == "healthcare"
    assert evt.event_type == "trial_completion"
    assert "LLY" in evt.tickers


def test_openfda_sample_normalises_to_safety_event():
    src = HealthcareSourceEvent(
        source_key="openfda",
        source_tier="official",
        stable_event_key="def",
        title="Recall notice",
        summary="Safety warning issued",
        healthcare_event_type="recall",
        regulator="FDA",
    )
    evt = healthcare_source_to_vertical_event(src)
    assert evt.event_type == "recall"
    assert evt.diagnostics["regulator"] == "FDA"


def test_missing_company_ticker_mapping_does_not_crash():
    src = HealthcareSourceEvent(
        source_key="sec",
        source_tier="official",
        stable_event_key="ghi",
        title="Material filing",
        summary="No ticker mapped",
        healthcare_event_type="material_filing",
    )
    evt = healthcare_source_to_vertical_event(src)
    assert evt.tickers == []
