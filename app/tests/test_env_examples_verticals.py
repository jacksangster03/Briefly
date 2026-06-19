from __future__ import annotations

from pathlib import Path
import re


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_env_example_exists():
    assert Path(".env.example").exists()


def test_myenv_example_is_not_tracked_template():
    assert not Path("myenv.example").exists()


def test_env_example_contains_vertical_defaults():
    text = _read(".env.example")
    assert "VERTICALS_INCLUDE_IN_BRIEFING=false" in text
    assert "VERTICALS_GEOPOLITICS_ENABLED=false" in text
    assert "VERTICALS_AI_TECH_ENABLED=false" in text


def test_env_example_contains_vertical_variables():
    text = _read(".env.example")
    required = (
        "VERTICALS_BRIEFING_SESSIONS='[\"morning\"]'",
        "VERTICALS_INTRADAY_MATERIALITY_THRESHOLD=0.75",
        "GDELT_ENABLED=true",
        "GDELT_TIMEOUT_SECONDS=10",
        "GDELT_MAX_RECORDS=50",
        "VERTICALS_HEALTHCARE_ENABLED=true",
        "CLINICALTRIALS_ENABLED=true",
        "OPENFDA_ENABLED=true",
        "OPENFDA_API_KEY=",
        "EMA_ENABLED=true",
        "VERTICALS_AI_TECH_ENABLED=false",
        "ARXIV_ENABLED=true",
        "ARXIV_TIMEOUT_SECONDS=10",
        "GITHUB_ENABLED=false",
        "GITHUB_TOKEN=",
        "NEWSAPI_KEY=",
        "FINNHUB_API_KEY=",
    )
    for key in required:
        assert key in text


def test_env_example_has_required_vertical_comments_and_sec_placeholder():
    text = _read(".env.example")
    for token in (
        "GDELT: free/no key",
        "ClinicalTrials.gov: free/no key",
        "openFDA: free key optional/recommended",
        "SEC EDGAR: no API key",
        "arXiv: free/no key",
        "GitHub: token optional",
    ):
        assert token in text
    assert 'SEC_USER_AGENT="Briefly/0.1 your-email@example.com"' in text


def test_env_example_has_no_obvious_real_secrets():
    text = _read(".env.example")
    assert "sk-live-" not in text.lower()
    assert "sk-proj-" not in text.lower()
    assert "ghp_" not in text.lower()
    assert "xoxb-" not in text.lower()
    # Guard against accidentally committing bearer/JWT-like long tokens.
    assert re.search(r"[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", text) is None


def test_api_setup_doc_mentions_required_sources():
    text = _read("docs/API_SETUP.md")
    for token in ("GDELT", "ClinicalTrials.gov", "openFDA", "SEC EDGAR", "arXiv", "GitHub"):
        assert token in text
    assert "cp .env.example .env" in text
    assert "myenv.example" not in text
    lower = text.lower()
    assert (
        "never commit .env" in lower
        or "never commit `.env`" in lower
        or "real secrets only in local `.env`" in lower
    )
