from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.briefing.formatter import TelegramFormatter
from app.healthcare.classifier import classify_healthcare_event
from app.healthcare.schemas import HealthcareSourceEvent
from app.healthcare.scorer import score_healthcare_event
from app.healthcare.section_builder import build_healthcare_section, filter_breaking_healthcare_events
from app.healthcare.sources.clinicaltrials import fetch_clinicaltrials_source_events
from app.healthcare.sources.company_ir import fetch_sec_healthcare_source_events
from app.healthcare.sources.fda import fetch_openfda_source_events
from app.healthcare.schemas import HealthcareBriefingSection
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MorningBriefing
from app.schemas.events import NormalisedEvent
from app.verticals.plugins.healthcare import HealthcareVerticalPlugin


def _profile(enabled: bool = True) -> UserProfile:
    return UserProfile(
        timezone="Europe/Madrid",
        watchlist_primary=["LLY", "NVO"],
        healthcare={
            "enabled": enabled,
            "max_items_morning": 4,
            "max_items_intraday": 2,
            "breaking_alerts": True,
            "themes": ["GLP-1", "peptides", "obesity", "API manufacturing", "CDMO", "FDA", "EMA", "clinical trials"],
            "tickers": ["LLY", "NVO", "TMO"],
            "assets": ["tirzepatide", "semaglutide", "wegovy", "mounjaro"],
            "minimum_severity_morning": "medium",
            "minimum_severity_intraday": "high",
            "minimum_severity_breaking": "critical",
        },
    )


def _evt(title: str, summary: str, *, event_type: str = "news", tickers: list[str] | None = None, source: str = "newsapi") -> NormalisedEvent:
    return NormalisedEvent(
        title=title,
        summary=summary,
        event_type=event_type,
        tickers=tickers or [],
        source=source,
        published_at=datetime.now(timezone.utc),
        url="https://example.com",
        raw_data={"source_name": "Reuters"},
    )


def test_healthcare_disabled_no_section():
    section = build_healthcare_section(profile=_profile(enabled=False), session_key="morning", events=[_evt("FDA approves drug", "FDA approved...")])
    assert section is None


def test_healthcare_enabled_adds_section_when_signal_exists():
    section = build_healthcare_section(
        profile=_profile(enabled=True),
        session_key="morning",
        events=[_evt("FDA approves Eli Lilly obesity therapy", "The FDA approved tirzepatide for obesity.", tickers=["LLY"])],
    )
    assert section is not None
    assert section.enabled is True
    assert section.items


def test_classifier_fda_approval_is_critical():
    event = _evt("FDA approval for GLP-1 therapy", "The FDA approved a new obesity treatment.", tickers=["LLY"])
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is not None
    assert classified.event_type == "fda_approval"
    assert classified.severity == "critical"


def test_classifier_phase3_is_high():
    event = _evt("Phase 3 trial data met primary endpoint", "Phase 3 obesity trial met endpoints.", tickers=["NVO"])
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is not None
    assert classified.event_type == "clinical_data"
    assert classified.severity in {"high", "critical"}


def test_glp1_tags_detected():
    event = _evt("GLP-1 demand rises", "Semaglutide and tirzepatide demand remains strong.", tickers=["LLY", "NVO"])
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is not None
    tags = set(classified.modality + classified.therapy_areas)
    assert "GLP-1" in tags or "obesity" in tags


def test_low_signal_generic_health_article_suppressed():
    event = _evt(
        "Top 5 healthcare stocks to buy now",
        "Fantastic news for shareholders and room to run.",
        source="newsapi",
    )
    event.raw_data = {"source_name": "Unknown Blog"}
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is None


def test_openai_power_story_is_not_healthcare():
    event = _evt(
        "Power crunch could cripple OpenAI and Anthropic",
        "AI infrastructure and power grid constraints may impact model training economics.",
        source="newsapi",
    )
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is None


def test_radiology_labor_story_requires_healthcare_anchor():
    event = _evt(
        "Geoffrey Hinton says AI could replace radiologists",
        "Broad labour-market commentary on AI replacement risk in radiology roles.",
        source="newsapi",
    )
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is None


def test_cabaletta_offering_classified_as_biotech_financing():
    event = _evt(
        "Cabaletta Bio announces follow-on public offering",
        "Cabaletta Bio launched a follow-on public offering to extend development runway.",
        tickers=["CABA"],
        source="newsapi",
    )
    event.raw_data = {"source_name": "Reuters"}
    classified = classify_healthcare_event(event, healthcare_prefs=_profile().healthcare_preferences)
    assert classified is not None
    assert classified.event_type == "biotech_financing"


def test_generic_ai_capacity_story_never_gets_api_manufacturing_lens():
    profile = _profile()
    prefs = profile.healthcare_preferences
    event = _evt(
        "AI data-center capacity expands amid power constraints",
        "Infrastructure capacity and energy demand are increasing for model training.",
        source="newsapi",
    )
    classified = classify_healthcare_event(event, healthcare_prefs=prefs)
    assert classified is None


def test_portfolio_watchlist_ticker_gets_relevance_boost():
    prefs = _profile().healthcare_preferences
    profile = _profile()
    watch_evt = classify_healthcare_event(
        _evt("FDA updates Eli Lilly label", "FDA label update for obesity therapy.", tickers=["LLY"]),
        healthcare_prefs=prefs,
    )
    other_evt = classify_healthcare_event(
        _evt("FDA updates competitor label", "FDA label update for obesity therapy.", tickers=["XYZ"]),
        healthcare_prefs=prefs,
    )
    assert watch_evt is not None and other_evt is not None
    watch_scored = score_healthcare_event(watch_evt, profile=profile, healthcare_prefs=prefs)
    other_scored = score_healthcare_event(other_evt, profile=profile, healthcare_prefs=prefs)
    assert watch_scored.relevance_score > other_scored.relevance_score


def test_lilly_guidance_ranks_above_generic_healthcare():
    profile = _profile()
    prefs = profile.healthcare_preferences
    lilly = classify_healthcare_event(
        _evt("Eli Lilly raises guidance on obesity demand", "LLY raised guidance as GLP-1 demand remains strong.", tickers=["LLY"]),
        healthcare_prefs=prefs,
    )
    generic = classify_healthcare_event(
        _evt("Healthcare sentiment update", "Broad healthcare sector commentary without hard catalyst.", source="newsapi"),
        healthcare_prefs=prefs,
    )
    assert lilly is not None
    lilly_scored = score_healthcare_event(lilly, profile=profile, healthcare_prefs=prefs)
    generic_score = 0.0
    if generic is not None:
        generic_score = score_healthcare_event(generic, profile=profile, healthcare_prefs=prefs).relevance_score
    assert lilly_scored.relevance_score > generic_score


def test_section_omitted_when_no_relevant_items():
    section = build_healthcare_section(
        profile=_profile(),
        session_key="morning",
        events=[_evt("General market update", "No healthcare details", source="finnhub")],
    )
    assert section is None


def test_intraday_only_high_or_critical():
    section = build_healthcare_section(
        profile=_profile(),
        session_key="us_intraday_risk",
        events=[
            _evt("FDA approves therapy", "FDA approved GLP-1 drug.", tickers=["LLY"]),
            _evt("Company IR update", "Investor relations update without catalyst.", event_type="company_news", tickers=["LLY"]),
        ],
    )
    assert section is not None
    assert section.items
    assert all(item.severity in {"high", "critical"} for item in section.items)


def test_breaking_biotech_filter_only_critical():
    events = [
        _evt("FDA approves obesity drug", "FDA approved GLP-1 drug.", tickers=["LLY"]),
        _evt("Healthcare commentary", "General biotech sector commentary.", source="newsapi"),
    ]
    items = filter_breaking_healthcare_events(profile=_profile(), events=events)
    assert items
    assert all(item.severity == "critical" for item in items)


def test_renderer_includes_healthcare_section_lines():
    formatter = TelegramFormatter("Europe/Madrid")
    briefing = MorningBriefing()
    briefing.session_key = "morning"
    briefing.session_title = "Morning Briefing"
    briefing.healthcare_intelligence = HealthcareBriefingSection(
        enabled=True,
        read="High-signal healthcare items are focused on GLP-1 and manufacturing catalysts.",
        items=[
            {
                "title": "Eli Lilly raises obesity guidance",
                "summary": "LLY raised guidance as GLP-1 demand remains strong.",
                "event_type": "earnings_guidance",
                "severity": "high",
                "company_display": "LLY",
                "asset_display": "tirzepatide",
                "theme_tags": ["GLP-1", "obesity"],
                "market_relevance": "Obesity therapeutics remain a major pharma growth pool.",
                "portfolio_lens": "Relevant to watchlist exposure.",
                "source_line": "Reuters",
            }
        ],
        confidence="MEDIUM",
    )
    rendered = "\n".join(formatter.format_morning_briefing(briefing))
    assert "HEALTHCARE / BIOTECH INTELLIGENCE" in rendered
    assert "Why market-relevant" in rendered
    assert "Portfolio lens" in rendered


def test_sec_healthcare_keyword_detection_normalizes_event(monkeypatch):
    class _StubSECProvider:
        def __init__(self, **_kwargs):
            pass

        def is_configured(self):
            return True

        def search_filings(self, **_kwargs):
            return [
                _evt(
                    "8-K: LLY reports FDA approval update",
                    "FDA approval and Phase 3 endpoint update included in filing.",
                    source="sec_edgar",
                    tickers=["LLY"],
                ),
                _evt(
                    "8-K: META EX-1.1",
                    "Routine filing update with no operating catalyst.",
                    source="sec_edgar",
                    tickers=["META"],
                ),
            ]

    monkeypatch.setattr("app.healthcare.sources.company_ir.SECProvider", _StubSECProvider)
    settings = SimpleNamespace(
        enable_healthcare_official_sources=True,
        enable_healthcare_sec_source=True,
        sec_user_agent="Briefly test@example.com",
        healthcare_source_timeout_seconds=5,
    )
    events, health = fetch_sec_healthcare_source_events(settings=settings, profile=_profile(), budget_allowed=True, limit=10)
    assert len(events) == 1
    assert events[0].source_key == "sec"
    assert events[0].healthcare_event_type in {"fda_approval", "clinical_trial_result", "sec_filing"}
    assert health.suppressed_count >= 1


def test_clinicaltrials_parser_normalizes_trial_event(monkeypatch):
    payload = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT12345678", "briefTitle": "Phase 3 obesity readout"},
                    "statusModule": {"overallStatus": "Completed", "hasResults": True, "lastUpdatePostDate": "2026-05-08"},
                    "designModule": {"phases": ["Phase 3"]},
                    "descriptionModule": {"briefSummary": "Topline endpoint met."},
                    "conditionsModule": {"conditions": ["Obesity"]},
                    "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Eli Lilly"}},
                }
            }
        ]
    }

    def _fake_fetch_payload(**_kwargs):
        return payload

    monkeypatch.setattr("app.healthcare.sources.clinicaltrials._fetch_payload", _fake_fetch_payload)
    settings = SimpleNamespace(
        enable_healthcare_official_sources=True,
        enable_healthcare_clinicaltrials_source=True,
        clinicaltrials_base_url="https://clinicaltrials.gov/api/v2",
        healthcare_source_timeout_seconds=5,
    )
    events, health = fetch_clinicaltrials_source_events(settings=settings, profile=_profile(), budget_allowed=True, limit=10)
    assert events
    assert events[0].source_key == "clinicaltrials"
    assert events[0].healthcare_event_type in {"clinical_trial_result", "clinical_trial_start", "clinical_trial_update"}
    assert health.status == "ok"


def test_openfda_parser_normalizes_safety_event(monkeypatch):
    payload = {
        "results": [
            {
                "classification": "Class II",
                "reason_for_recall": "Labeling issue discovered",
                "product_description": "Drug injection",
                "recall_number": "D-1234-2026",
                "report_date": "20260508",
                "status": "Ongoing",
                "recalling_firm": "Example Pharma",
            }
        ]
    }

    def _fake_fetch(**_kwargs):
        return payload

    monkeypatch.setattr("app.healthcare.sources.fda._fetch_enforcement_payload", _fake_fetch)
    settings = SimpleNamespace(
        enable_healthcare_official_sources=True,
        enable_healthcare_openfda_source=True,
        fda_openfda_base_url="https://api.fda.gov",
        fda_openfda_api_key="",
        healthcare_source_timeout_seconds=5,
    )
    events, health = fetch_openfda_source_events(settings=settings, budget_allowed=True, limit=10)
    assert events
    assert events[0].source_key == "openfda"
    assert events[0].healthcare_event_type in {"drug_safety_warning", "label_update"}
    assert health.status == "ok"


def test_disabled_official_sources_are_not_called(monkeypatch):
    called = {"value": False}

    def _explode(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("network should not be called when source disabled")

    monkeypatch.setattr("app.healthcare.sources.fda._fetch_enforcement_payload", _explode)
    settings = SimpleNamespace(
        enable_healthcare_official_sources=False,
        enable_healthcare_openfda_source=True,
        fda_openfda_base_url="https://api.fda.gov",
        fda_openfda_api_key="",
    )
    events, health = fetch_openfda_source_events(settings=settings, budget_allowed=True, limit=10)
    assert events == []
    assert health.status == "disabled"
    assert called["value"] is False


def test_source_failure_is_non_blocking_and_reported(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.healthcare.sources.fda._fetch_enforcement_payload", _fail)
    settings = SimpleNamespace(
        enable_healthcare_official_sources=True,
        enable_healthcare_openfda_source=True,
        fda_openfda_base_url="https://api.fda.gov",
        fda_openfda_api_key="",
        healthcare_source_timeout_seconds=5,
    )
    events, health = fetch_openfda_source_events(settings=settings, budget_allowed=True, limit=10)
    assert events == []
    assert health.status == "error"
    assert "boom" in (health.last_error or "")


def test_healthcare_plugin_active_mode_tracks_official_candidates_in_diagnostics():
    plugin = HealthcareVerticalPlugin()
    profile = _profile(enabled=True)
    plugin._last_source_health = {
        "sec": {"status": "ok", "fetched_count": 2, "normalized_count": 1, "suppressed_count": 1},
        "clinicaltrials": {"status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
        "openfda": {"status": "disabled", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
        "ema": {"status": "stub_inactive", "fetched_count": 0, "normalized_count": 0, "suppressed_count": 0},
    }
    plugin._last_official_candidate_count = 1
    plugin._last_official_suppressed_count = 1
    plugin._last_official_fetch_at = datetime.now(timezone.utc)
    metrics = plugin.audit_metrics(profile=profile, session_key="morning", candidate_events=[_evt("FDA update", "FDA update", tickers=["LLY"])])
    assert metrics["official_candidate_count"] == 1
    assert metrics["official_suppressed_count"] == 1
