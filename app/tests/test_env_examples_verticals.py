from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_env_example_contains_vertical_defaults():
    text = _read(".env.example")
    assert "VERTICALS_INCLUDE_IN_BRIEFING=false" in text
    assert "VERTICALS_GEOPOLITICS_ENABLED=false" in text
    assert "VERTICALS_AI_TECH_ENABLED=false" in text


def test_env_example_has_safe_sec_user_agent_placeholder():
    text = _read(".env.example")
    assert 'SEC_USER_AGENT="Briefly/0.1 your-email@example.com"' in text
    assert "sk-" not in text.lower()
    assert "ghp_" not in text.lower()


def test_myenv_example_contains_vertical_section():
    text = _read("myenv.example")
    assert "Vertical Intelligence: source configuration" in text
    assert "VERTICALS_INCLUDE_IN_BRIEFING=false" in text
    assert "VERTICALS_GEOPOLITICS_ENABLED=false" in text
    assert "VERTICALS_AI_TECH_ENABLED=false" in text


def test_api_setup_doc_mentions_required_sources():
    text = _read("docs/API_SETUP.md")
    for token in ("GDELT", "ClinicalTrials.gov", "openFDA", "SEC EDGAR", "arXiv", "GitHub"):
        assert token in text
