"""Deterministic taxonomy for healthcare / biotech intelligence."""

from __future__ import annotations

HEALTHCARE_EVENT_TYPES: tuple[str, ...] = (
    "fda_approval",
    "fda_crl",
    "fda_safety",
    "ema_chmp",
    "clinical_data",
    "trial_start",
    "trial_halt",
    "trial_completion",
    "earnings_guidance",
    "m_and_a",
    "licensing_deal",
    "manufacturing_capacity",
    "api_supply_chain",
    "shortage",
    "pricing_reimbursement",
    "company_ir",
    "sec_filing",
    "general_news",
)

MODALITY_TAGS: tuple[str, ...] = (
    "peptide",
    "GLP-1",
    "incretin",
    "small molecule",
    "antibody",
    "ADC",
    "gene therapy",
    "cell therapy",
    "RNA",
    "obesity",
    "diabetes",
    "oncology",
    "rare disease",
    "immunology",
    "manufacturing",
    "CDMO",
    "API",
)

SOURCE_QUALITY_BUCKETS: tuple[str, ...] = (
    "primary_regulator",
    "company_ir",
    "sec",
    "clinical_registry",
    "tier1_wire",
    "trade_press",
    "generic_news",
    "low_signal",
)

SEVERITY_ORDER: tuple[str, ...] = ("low", "medium", "high", "critical")

SEVERITY_SCORE = {
    "low": 25.0,
    "medium": 50.0,
    "high": 75.0,
    "critical": 92.0,
}

SOURCE_QUALITY_SCORE = {
    "primary_regulator": 1.0,
    "company_ir": 0.92,
    "sec": 0.92,
    "clinical_registry": 0.88,
    "tier1_wire": 0.82,
    "trade_press": 0.68,
    "generic_news": 0.5,
    "low_signal": 0.25,
}

EVENT_SEVERITY = {
    "fda_approval": "critical",
    "fda_crl": "critical",
    "fda_safety": "high",
    "ema_chmp": "high",
    "clinical_data": "high",
    "trial_start": "medium",
    "trial_halt": "critical",
    "trial_completion": "medium",
    "earnings_guidance": "medium",
    "m_and_a": "high",
    "licensing_deal": "medium",
    "manufacturing_capacity": "medium",
    "api_supply_chain": "medium",
    "shortage": "high",
    "pricing_reimbursement": "medium",
    "company_ir": "medium",
    "sec_filing": "medium",
    "general_news": "low",
}

CRITICAL_EVENT_TYPES = {
    "fda_approval",
    "fda_crl",
    "trial_halt",
    "clinical_data",
    "m_and_a",
}

