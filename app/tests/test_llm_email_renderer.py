"""Phase 4 tests: LLM email render guardrails and fallback behavior."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.briefing.llm_email_renderer import LLMEmailRenderer
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.delivery import ChartAsset, EmailRenderResult
from app.schemas.events import NormalisedEvent, QuoteData
from app.settings import Settings


def _sample_briefing() -> tuple[MorningBriefing, list[NormalisedEvent]]:
    event = NormalisedEvent(
        title="Tesla cuts Model Y prices in Europe after demand softens",
        summary="Price action signals a near-term demand reset for EV buyers.",
        tickers=["TSLA"],
        source="finnhub",
        event_type="guidance",
        cluster_id="tesla_cluster_1",
        url="https://example.com/tesla-demand-reset",
        published_at=datetime(2026, 4, 12, 7, 30, tzinfo=timezone.utc),
    )
    briefing = MorningBriefing(
        generated_at=datetime(2026, 4, 12, 8, 45, tzinfo=timezone.utc),
        session_mode="saturday",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(
                    symbol="SPY",
                    display_name="S&P 500",
                    current_price=679.46,
                    change_percent=0.03,
                    timestamp=datetime(2026, 4, 11, 20, 0, tzinfo=timezone.utc),
                    source="yfinance",
                )
            ]
        ),
        top_themes=[event],
        portfolio_focus=[event],
        watchlist_events=[event],
    )
    return briefing, [event]


def _deterministic_email() -> EmailRenderResult:
    return EmailRenderResult(
        subject="Weekend Briefing | Sat 12 Apr",
        plain_text="Deterministic fallback body.",
        html_body="<html><body>Deterministic fallback body.</body></html>",
        inline_assets=[
            ChartAsset(
                key="market_snapshot",
                title="Market Snapshot",
                caption="Major index moves.",
                filename="market-snapshot.png",
                content_id="market-snapshot-cid",
                content=b"\x89PNGfake",
            )
        ],
    )


def test_validate_candidate_rejects_unknown_ticker():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, events = _sample_briefing()
    payload = renderer._build_payload(briefing, events)
    candidate = {
        "subject": "Weekend Briefing",
        "body": "Tesla (TSLA) and Apple (AAPL) are both in focus.",
        "source_urls": ["https://example.com/tesla-demand-reset"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert any("Unknown ticker references" in error for error in errors)


def test_validate_candidate_rejects_unknown_url():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, events = _sample_briefing()
    payload = renderer._build_payload(briefing, events)
    candidate = {
        "subject": "Weekend Briefing",
        "body": "Demand reset remains in focus.",
        "source_urls": ["https://evil.example.com/fake-link"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert any("Unknown source URL" in error for error in errors)


def test_validate_candidate_rejects_unknown_numeric_tokens():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, events = _sample_briefing()
    payload = renderer._build_payload(briefing, events)
    candidate = {
        "subject": "Weekend Briefing",
        "body": "Shares surged +9.99% on a surprise update.",
        "source_urls": ["https://example.com/tesla-demand-reset"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert any("Unknown numeric tokens" in error for error in errors)


def test_validate_candidate_enforces_min_source_urls():
    settings = Settings(
        enable_llm_email_render=True,
        openai_api_key="test-key",
        llm_email_min_source_urls=2,
    )
    renderer = LLMEmailRenderer(settings)
    briefing, events = _sample_briefing()
    second = events[0].model_copy(deep=True)
    second.cluster_id = "tesla_cluster_2"
    second.url = "https://example.com/tesla-second-source"
    payload = renderer._build_payload(briefing, events + [second])

    candidate = {
        "subject": "Weekend Briefing",
        "body": "Tesla (TSLA) demand remains in focus.",
        "source_urls": ["https://example.com/tesla-demand-reset"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert any("Insufficient source URLs" in error for error in errors)


def test_render_morning_shadow_mode_keeps_deterministic_active(monkeypatch):
    settings = Settings(
        enable_llm_email_render=True,
        llm_render_shadow_mode=True,
        openai_api_key="test-key",
    )
    renderer = LLMEmailRenderer(settings)
    briefing, events = _sample_briefing()
    deterministic = _deterministic_email()

    def _fake_request(_prompt):
        return (
            {
                "subject": "Weekend Briefing | Sat 12 Apr",
                "body": (
                    "Tesla pricing in Europe remains the lead portfolio signal into next week.\n\n"
                    "The move matters because EV demand elasticity can reset margin expectations."
                ),
                "source_urls": ["https://example.com/tesla-demand-reset"],
            },
            0, 0, None,
        )

    monkeypatch.setattr(renderer, "_request_llm", _fake_request)
    monkeypatch.setattr("app.llm.usage_tracker.log_llm_usage", lambda **_kw: None)
    decision = renderer.render_morning(
        briefing=briefing,
        deterministic_email=deterministic,
        selected_events=events,
    )

    assert decision.mode == "shadow"
    assert decision.active_email.subject == deterministic.subject
    assert decision.shadow_preview is not None
    assert "Sources" in decision.shadow_preview.plain_text
    assert len(decision.shadow_preview.inline_assets) == len(deterministic.inline_assets)


def test_render_morning_live_mode_activates_valid_llm_output(monkeypatch):
    settings = Settings(
        enable_llm_email_render=True,
        llm_render_shadow_mode=False,
        openai_api_key="test-key",
    )
    renderer = LLMEmailRenderer(settings)
    briefing, events = _sample_briefing()

    monkeypatch.setattr(
        renderer,
        "_request_llm",
        lambda _prompt: (
            {
                "subject": "Weekend Portfolio Brief | Sat 12 Apr",
                "body": "Tesla pricing resets remain the key portfolio risk into Monday's reopen.",
                "source_urls": ["https://example.com/tesla-demand-reset"],
            },
            0, 0, None,
        ),
    )
    monkeypatch.setattr("app.llm.usage_tracker.log_llm_usage", lambda **_kw: None)

    decision = renderer.render_morning(
        briefing=briefing,
        deterministic_email=_deterministic_email(),
        selected_events=events,
    )

    assert decision.mode == "live"
    assert decision.active_email.subject == "Weekend Portfolio Brief | Sat 12 Apr"
    assert "Sources" in decision.active_email.plain_text
    assert decision.shadow_preview is None


def test_render_morning_falls_back_on_validation_failure(monkeypatch):
    settings = Settings(
        enable_llm_email_render=True,
        llm_render_shadow_mode=False,
        openai_api_key="test-key",
    )
    renderer = LLMEmailRenderer(settings)
    briefing, events = _sample_briefing()
    deterministic = _deterministic_email()

    monkeypatch.setattr(
        renderer,
        "_request_llm",
        lambda _prompt: (
            {
                "subject": "Weekend Briefing",
                "body": "Tesla (TSLA) and Apple (AAPL) are in focus.",
                "source_urls": ["https://example.com/tesla-demand-reset"],
            },
            0, 0, None,
        ),
    )
    monkeypatch.setattr("app.llm.usage_tracker.log_llm_usage", lambda **_kw: None)

    decision = renderer.render_morning(
        briefing=briefing,
        deterministic_email=deterministic,
        selected_events=events,
    )

    assert decision.mode == "fallback"
    assert decision.active_email.subject == deterministic.subject
    assert decision.validation_errors


def test_estimate_usage_cost_with_configured_rates():
    settings = Settings(
        enable_llm_email_render=True,
        openai_api_key="test-key",
        llm_email_input_cost_per_1m_tokens=0.15,
        llm_email_output_cost_per_1m_tokens=0.60,
    )
    renderer = LLMEmailRenderer(settings)
    cost = renderer._estimate_usage_cost(prompt_tokens=2000, completion_tokens=500)
    assert cost is not None
    assert cost == pytest.approx(0.0006, rel=1e-6)


def test_estimate_usage_cost_disabled_when_rates_missing():
    renderer = LLMEmailRenderer(
        Settings(
            enable_llm_email_render=True,
            openai_api_key="test-key",
            llm_email_input_cost_per_1m_tokens=0.0,
            llm_email_output_cost_per_1m_tokens=0.0,
        )
    )
    assert renderer._estimate_usage_cost(prompt_tokens=2000, completion_tokens=500) is None


def test_validate_candidate_accepts_safe_percent_equivalent_numeric():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, events = _sample_briefing()
    payload = renderer._build_payload(briefing, events)
    candidate = {
        "subject": "Weekend Briefing",
        "body": "S&P 500 held near 679.460 while TSLA remained in focus.",
        "source_urls": ["https://example.com/tesla-demand-reset"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert not any("Unknown numeric tokens" in error for error in errors)


def test_validate_candidate_accepts_rounded_numeric_token_within_tolerance():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, events = _sample_briefing()
    briefing.market_setup.index_quotes[0].display_name = "VIX"
    briefing.market_setup.index_quotes[0].current_price = 17.74
    payload = renderer._build_payload(briefing, events)
    candidate = {
        "subject": "Weekend Briefing",
        "body": "VIX held near 17.7 while TSLA remained in focus.",
        "source_urls": ["https://example.com/tesla-demand-reset"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert not any("Unknown numeric tokens" in error for error in errors)


def test_build_payload_allows_title_and_exchange_ticker_mentions():
    renderer = LLMEmailRenderer(Settings(enable_llm_email_render=True, openai_api_key="test-key"))
    briefing, _ = _sample_briefing()
    event = NormalisedEvent(
        title="Nebius (NBIS) Eyes Foray Into Full-Stack AI",
        summary="Oklo (NYSE:OKLO) signs expansion deal tied to AI datacenter demand.",
        tickers=[],
        source="newsapi",
        event_type="company_news",
        cluster_id="cluster_nbis_oklo",
        url="https://example.com/nbis-oklo",
        published_at=datetime(2026, 4, 12, 7, 50, tzinfo=timezone.utc),
    )
    payload = renderer._build_payload(briefing, [event])
    candidate = {
        "subject": "Weekend Briefing",
        "body": "NBIS and OKLO remain in focus for AI infrastructure demand.",
        "source_urls": ["https://example.com/nbis-oklo"],
    }
    errors, _ = renderer._validate_candidate(candidate, payload)
    assert not any("Unknown ticker references" in error for error in errors)
