from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.events import NormalisedEvent
from app.settings import Settings
from app.verticals.events import VerticalEvent
from app.verticals.scoring import deterministic_vertical_score
from app.verticals.sources.ai_tech import collect_ai_tech_events


def test_sec_filing_maps_to_ai_tech_event(monkeypatch):
    class _FakeSEC:
        def __init__(self, *args, **kwargs):
            pass

        def search_filings(self, **kwargs):
            return [
                NormalisedEvent(
                    source="sec_edgar",
                    title="8-K: AI capex update",
                    summary="Capex increased for AI data centers",
                    url="https://sec.example/8k",
                    tickers=["NVDA"],
                    published_at=datetime.now(timezone.utc),
                )
            ]

    monkeypatch.setattr("app.verticals.sources.ai_tech.SECProvider", _FakeSEC)
    monkeypatch.setattr("app.verticals.sources.ai_tech.requests.get", lambda *a, **k: type("R", (), {"status_code": 500, "text": ""})())
    events, health = collect_ai_tech_events(settings=Settings(sec_user_agent="Briefly test@example.com"), watchlist=["NVDA"])
    assert any(e.event_type in {"ai_capex", "data_center_infrastructure"} for e in events)
    assert health["sec"]["status"] == "ok"


def test_arxiv_or_github_failure_degrades_safely(monkeypatch):
    class _FakeSEC:
        def __init__(self, *args, **kwargs):
            pass

        def search_filings(self, **kwargs):
            return []

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.verticals.sources.ai_tech.SECProvider", _FakeSEC)
    monkeypatch.setattr("app.verticals.sources.ai_tech.requests.get", _boom)
    _events, health = collect_ai_tech_events(settings=Settings(sec_user_agent="Briefly test@example.com"))
    assert health["arxiv"]["status"] == "error"
    assert health["github"]["status"] in {"stub_inactive", "disabled"}


def test_watchlist_relevance_boosts_relevant_tickers():
    high = VerticalEvent(vertical="ai_tech", source_name="sec_edgar", source_tier="official", title="AI filing", portfolio_relevance=0.8, market_relevance=0.6, novelty_score=0.7, diagnostics={"freshness_score": 0.8})
    low = VerticalEvent(vertical="ai_tech", source_name="sec_edgar", source_tier="official", title="AI filing 2", portfolio_relevance=0.1, market_relevance=0.6, novelty_score=0.7, diagnostics={"freshness_score": 0.8})
    assert deterministic_vertical_score(high) > deterministic_vertical_score(low)


def test_broad_media_does_not_dominate_official():
    official = VerticalEvent(vertical="ai_tech", source_name="sec_edgar", source_tier="official", title="Official filing", market_relevance=0.7, portfolio_relevance=0.4, novelty_score=0.6, diagnostics={"freshness_score": 0.7})
    broad = VerticalEvent(vertical="ai_tech", source_name="media", source_tier="broad_media", title="Media story", market_relevance=0.8, portfolio_relevance=0.4, novelty_score=0.6, diagnostics={"freshness_score": 0.7})
    assert deterministic_vertical_score(official) > deterministic_vertical_score(broad)


def test_missing_sec_user_agent_is_disabled_with_reason():
    _events, health = collect_ai_tech_events(settings=Settings(sec_user_agent=""))
    assert health["sec"]["status"] == "disabled"
    assert "missing SEC_USER_AGENT" in str(health["sec"]["last_error"])
