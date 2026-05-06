"""Tests for Phase 9.5: LLM usage tracking."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/test_llm_usage.db"
    from app.db import session as db_session_mod
    monkeypatch.setattr(db_session_mod, "_engine", None)
    monkeypatch.setattr(db_session_mod, "_SessionLocal", None)
    orig_get_settings = db_session_mod.get_settings

    def patched_settings():
        s = orig_get_settings()
        s.database_url = db_url
        return s

    monkeypatch.setattr(db_session_mod, "get_settings", patched_settings)
    from app.db.session import init_db
    init_db()
    yield db_url
    monkeypatch.setattr(db_session_mod, "_engine", None)
    monkeypatch.setattr(db_session_mod, "_SessionLocal", None)


# ---------------------------------------------------------------------------
# log_llm_usage — persistence
# ---------------------------------------------------------------------------

class TestLogLLMUsage:
    def test_row_is_persisted(self, isolated_db):
        from app.llm.usage_tracker import log_llm_usage, query_usage_rows

        log_llm_usage(
            profile_name="test_user",
            session_key="morning",
            local_date=date(2026, 5, 6),
            model="gpt-4o-mini",
            call_type="email_render",
            mode="live",
            prompt_tokens=1000,
            completion_tokens=400,
            estimated_cost_usd=0.000456,
        )

        rows = query_usage_rows("test_user", days=7, limit=10)
        assert len(rows) == 1
        r = rows[0]
        assert r["session_key"] == "morning"
        assert r["model"] == "gpt-4o-mini"
        assert r["mode"] == "live"
        assert r["prompt_tokens"] == 1000
        assert r["completion_tokens"] == 400
        assert r["total_tokens"] == 1400
        assert abs(r["estimated_cost_usd"] - 0.000456) < 1e-9

    def test_null_cost_stored_as_none(self, isolated_db):
        from app.llm.usage_tracker import log_llm_usage, query_usage_rows

        log_llm_usage(
            profile_name="test_user",
            session_key="closing_wrap",
            local_date=date(2026, 5, 6),
            model="gpt-4o-mini",
            call_type="email_render",
            mode="shadow",
            prompt_tokens=500,
            completion_tokens=200,
            estimated_cost_usd=None,
        )

        rows = query_usage_rows("test_user", days=7, limit=10)
        assert len(rows) == 1
        assert rows[0]["estimated_cost_usd"] is None

    def test_negative_tokens_clamped_to_zero(self, isolated_db):
        from app.llm.usage_tracker import log_llm_usage, query_usage_rows

        log_llm_usage(
            profile_name="test_user",
            session_key="morning",
            local_date=date(2026, 5, 6),
            model="gpt-4o-mini",
            call_type="email_render",
            mode="fallback",
            prompt_tokens=-1,
            completion_tokens=-5,
            estimated_cost_usd=None,
        )

        rows = query_usage_rows("test_user", days=7, limit=10)
        assert rows[0]["prompt_tokens"] == 0
        assert rows[0]["completion_tokens"] == 0
        assert rows[0]["total_tokens"] == 0

    def test_db_failure_does_not_raise(self, monkeypatch):
        """log_llm_usage must swallow exceptions so LLM render never breaks."""
        from app.llm import usage_tracker

        def bad_get_session():
            raise RuntimeError("DB offline")

        monkeypatch.setattr(usage_tracker, "get_session", bad_get_session)

        # Should not raise
        from app.llm.usage_tracker import log_llm_usage
        log_llm_usage(
            profile_name="test_user",
            session_key="morning",
            local_date=date(2026, 5, 6),
            model="gpt-4o-mini",
            call_type="email_render",
            mode="live",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost_usd=None,
        )

    def test_multiple_profiles_isolated(self, isolated_db):
        from app.llm.usage_tracker import log_llm_usage, query_usage_rows

        for profile in ("alice", "bob"):
            log_llm_usage(
                profile_name=profile,
                session_key="morning",
                local_date=date(2026, 5, 6),
                model="gpt-4o-mini",
                call_type="email_render",
                mode="live",
                prompt_tokens=100,
                completion_tokens=50,
                estimated_cost_usd=None,
            )

        alice_rows = query_usage_rows("alice", days=7, limit=10)
        bob_rows = query_usage_rows("bob", days=7, limit=10)
        assert len(alice_rows) == 1
        assert len(bob_rows) == 1


# ---------------------------------------------------------------------------
# query_usage_summary — aggregation
# ---------------------------------------------------------------------------

class TestQueryUsageSummary:
    def test_aggregates_across_calls(self, isolated_db):
        from app.llm.usage_tracker import log_llm_usage, query_usage_summary

        for _ in range(3):
            log_llm_usage(
                profile_name="test_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
                model="gpt-4o-mini",
                call_type="email_render",
                mode="live",
                prompt_tokens=1000,
                completion_tokens=400,
                estimated_cost_usd=0.0005,
            )

        rows = query_usage_summary("test_user", days=7)
        assert len(rows) == 1
        r = rows[0]
        assert r["calls"] == 3
        assert r["prompt_tokens"] == 3000
        assert r["completion_tokens"] == 1200
        assert r["total_tokens"] == 4200
        assert abs(r["estimated_cost_usd"] - 0.0015) < 1e-9

    def test_empty_for_unknown_profile(self, isolated_db):
        from app.llm.usage_tracker import query_usage_summary

        rows = query_usage_summary("nobody", days=30)
        assert rows == []

    def test_cutoff_excludes_old_rows(self, isolated_db):
        """Rows older than the cutoff should not appear in the summary."""
        from app.db.models import LLMUsageLog
        from app.db.session import get_session
        from app.llm.usage_tracker import query_usage_summary

        old_date = date(2020, 1, 1)
        row = LLMUsageLog(
            profile_name="test_user",
            called_at_utc=datetime(2020, 1, 1, 8, 0, tzinfo=timezone.utc),
            local_date=old_date,
            session_key="morning",
            model="gpt-4o-mini",
            call_type="email_render",
            mode="live",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            estimated_cost_usd=0.001,
        )
        with get_session() as db:
            db.add(row)

        rows = query_usage_summary("test_user", days=7)
        assert rows == []


# ---------------------------------------------------------------------------
# LLMEmailRenderer integration — usage is logged after API call
# ---------------------------------------------------------------------------

class TestRendererLogsUsage:
    def _make_settings(self):
        from app.settings import Settings
        s = Settings()
        s.enable_llm_email_render = True
        s.llm_render_shadow_mode = False
        s.openai_api_key = "test-key"
        s.llm_email_model = "gpt-4o-mini"
        s.llm_email_input_cost_per_1m_tokens = 0.15
        s.llm_email_output_cost_per_1m_tokens = 0.60
        return s

    def _make_minimal_briefing(self):
        from app.schemas.briefings import MorningBriefing
        b = MagicMock(spec=MorningBriefing)
        b.session_mode = "morning"
        b.session_key = "morning"
        return b

    def _make_render_result(self):
        from app.schemas.delivery import EmailRenderResult
        return EmailRenderResult(
            subject="Test",
            plain_text="test body",
            html_body="<p>test</p>",
            inline_assets=[],
        )

    def test_live_mode_logs_usage(self):
        from app.briefing.llm_email_renderer import LLMEmailRenderer

        settings = self._make_settings()
        renderer = LLMEmailRenderer(settings)

        parsed_result = {"subject": "Test Subject", "body": "Test body text.", "source_urls": ["https://example.com/1", "https://example.com/2"]}

        with patch.object(renderer, "_request_llm", return_value=(parsed_result, 800, 300, 0.000255)) as mock_req, \
             patch.object(renderer, "_validate_candidate", return_value=([], ["https://example.com/1"])), \
             patch.object(renderer, "_build_render_result", return_value=self._make_render_result()), \
             patch.object(renderer, "_build_payload") as mock_payload, \
             patch("app.llm.usage_tracker.log_llm_usage") as mock_log:
            mock_payload.return_value = MagicMock(prompt={"events": [{"title": "something"}]})
            decision = renderer.render_morning(
                briefing=self._make_minimal_briefing(),
                deterministic_email=self._make_render_result(),
                selected_events=[],
                profile_name="test_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
            )

        assert decision.mode == "live"
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["mode"] == "live"
        assert call_kwargs["prompt_tokens"] == 800
        assert call_kwargs["completion_tokens"] == 300
        assert call_kwargs["profile_name"] == "test_user"
        assert call_kwargs["session_key"] == "morning"

    def test_shadow_mode_logs_usage(self):
        from app.briefing.llm_email_renderer import LLMEmailRenderer

        settings = self._make_settings()
        settings.llm_render_shadow_mode = True
        renderer = LLMEmailRenderer(settings)

        parsed_result = {"subject": "S", "body": "B", "source_urls": ["https://example.com/1", "https://example.com/2"]}

        with patch.object(renderer, "_request_llm", return_value=(parsed_result, 600, 200, None)), \
             patch.object(renderer, "_validate_candidate", return_value=([], [])), \
             patch.object(renderer, "_build_render_result", return_value=self._make_render_result()), \
             patch.object(renderer, "_build_payload") as mock_payload, \
             patch("app.llm.usage_tracker.log_llm_usage") as mock_log:
            mock_payload.return_value = MagicMock(prompt={"events": [{"title": "x"}]})
            decision = renderer.render_morning(
                briefing=self._make_minimal_briefing(),
                deterministic_email=self._make_render_result(),
                selected_events=[],
                profile_name="test_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
            )

        assert decision.mode == "shadow"
        mock_log.assert_called_once()
        assert mock_log.call_args[1]["mode"] == "shadow"

    def test_validation_failure_logs_fallback(self):
        from app.briefing.llm_email_renderer import LLMEmailRenderer

        settings = self._make_settings()
        renderer = LLMEmailRenderer(settings)

        parsed_result = {"subject": "", "body": "", "source_urls": []}

        with patch.object(renderer, "_request_llm", return_value=(parsed_result, 500, 100, None)), \
             patch.object(renderer, "_validate_candidate", return_value=(["missing subject"], [])), \
             patch.object(renderer, "_build_payload") as mock_payload, \
             patch("app.llm.usage_tracker.log_llm_usage") as mock_log:
            mock_payload.return_value = MagicMock(prompt={"events": [{"title": "x"}]})
            decision = renderer.render_morning(
                briefing=self._make_minimal_briefing(),
                deterministic_email=self._make_render_result(),
                selected_events=[],
                profile_name="test_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
            )

        assert decision.mode == "fallback"
        mock_log.assert_called_once()
        assert mock_log.call_args[1]["mode"] == "fallback"

    def test_api_exception_does_not_log(self):
        """If the API call itself raises, no usage row is written."""
        from app.briefing.llm_email_renderer import LLMEmailRenderer

        settings = self._make_settings()
        renderer = LLMEmailRenderer(settings)

        with patch.object(renderer, "_request_llm", side_effect=RuntimeError("timeout")), \
             patch.object(renderer, "_build_payload") as mock_payload, \
             patch("app.llm.usage_tracker.log_llm_usage") as mock_log:
            mock_payload.return_value = MagicMock(prompt={"events": [{"title": "x"}]})
            decision = renderer.render_morning(
                briefing=self._make_minimal_briefing(),
                deterministic_email=self._make_render_result(),
                selected_events=[],
                profile_name="test_user",
                session_key="morning",
                local_date=date(2026, 5, 6),
            )

        assert decision.mode == "fallback"
        mock_log.assert_not_called()
