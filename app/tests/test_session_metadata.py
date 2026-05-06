"""Tests for app/briefing/session_metadata.py and app/main.run_daily_summary."""

from __future__ import annotations

import pytest

from app.briefing.session_metadata import (
    ALL_SESSIONS,
    ASIA_COVERAGE_NOTE,
    get_session_meta,
    label_for,
    focus_for,
)


# ---------------------------------------------------------------------------
# SessionMeta structure
# ---------------------------------------------------------------------------

class TestAllSessions:
    def test_six_sessions(self) -> None:
        assert len(ALL_SESSIONS) == 6

    def test_ordered_chronologically(self) -> None:
        starts = [s.window_start for s in ALL_SESSIONS]
        assert starts == sorted(starts)

    def test_all_have_label_and_focus(self) -> None:
        for meta in ALL_SESSIONS:
            assert meta.label, f"{meta.key} has no label"
            assert meta.focus, f"{meta.key} has no focus"

    def test_all_canonical_keys_present(self) -> None:
        keys = {s.key for s in ALL_SESSIONS}
        expected = {
            "morning", "europe_midday", "us_pre_open",
            "us_intraday_risk", "into_close", "closing_wrap",
        }
        assert keys == expected

    def test_morning_window(self) -> None:
        from datetime import time
        meta = get_session_meta("morning")
        assert meta is not None
        assert meta.window_start == time(6, 0)
        assert meta.window_end == time(10, 30)

    def test_closing_wrap_window(self) -> None:
        from datetime import time
        meta = get_session_meta("closing_wrap")
        assert meta is not None
        assert meta.window_start == time(22, 0)

    def test_window_str_format(self) -> None:
        meta = get_session_meta("morning")
        assert meta is not None
        assert meta.window_str == "06:00-10:30"


class TestGetSessionMeta:
    def test_canonical_key_lookup(self) -> None:
        meta = get_session_meta("us_intraday_risk")
        assert meta is not None
        assert meta.label == "US Intraday Risk Check"

    def test_alias_intraday(self) -> None:
        meta = get_session_meta("intraday")
        assert meta is not None
        assert meta.key == "us_intraday_risk"

    def test_alias_midday(self) -> None:
        meta = get_session_meta("midday")
        assert meta is not None
        assert meta.key == "europe_midday"

    def test_alias_preopen(self) -> None:
        meta = get_session_meta("preopen")
        assert meta is not None
        assert meta.key == "us_pre_open"

    def test_alias_close(self) -> None:
        meta = get_session_meta("close")
        assert meta is not None
        assert meta.key == "into_close"

    def test_unknown_key_returns_none(self) -> None:
        assert get_session_meta("nonexistent") is None

    def test_empty_string_returns_none(self) -> None:
        assert get_session_meta("") is None

    def test_case_insensitive_via_normalisation(self) -> None:
        # The function normalises to lowercase internally
        assert get_session_meta("Morning") is not None


class TestLabelFor:
    def test_known_key(self) -> None:
        assert label_for("morning") == "Morning Briefing"

    def test_alias(self) -> None:
        assert label_for("intraday") == "US Intraday Risk Check"

    def test_unknown_falls_back_to_key(self) -> None:
        assert label_for("unknown_session") == "unknown_session"


class TestFocusFor:
    def test_morning_focus(self) -> None:
        assert "Asia" in focus_for("morning")

    def test_closing_wrap_focus(self) -> None:
        assert "next-day" in focus_for("closing_wrap")

    def test_unknown_returns_empty(self) -> None:
        assert focus_for("bogus") == ""


class TestAsiaCoverageNote:
    def test_asia_note_mentions_morning(self) -> None:
        assert "Morning Briefing" in ASIA_COVERAGE_NOTE

    def test_asia_note_mentions_separate_session(self) -> None:
        assert "Asia" in ASIA_COVERAGE_NOTE


# ---------------------------------------------------------------------------
# run_daily_summary (smoke test via mock DB)
# ---------------------------------------------------------------------------

class TestRunDailySummary:
    def test_returns_string(self, monkeypatch) -> None:
        from app.main import run_daily_summary
        from app.settings import Settings

        monkeypatch.setattr("app.main.init_db", lambda: None)
        # Patch get_session to return an empty context manager
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        @contextmanager
        def _fake_session():
            mock = MagicMock()
            mock.query.return_value.filter.return_value.all.return_value = []
            mock.query.return_value.filter.return_value.count.return_value = 0
            yield mock

        monkeypatch.setattr("app.main.get_session", _fake_session)
        monkeypatch.setattr(
            "app.briefing.session_snapshot_service.list_session_snapshots",
            lambda **kw: [],
            raising=False,
        )

        result = run_daily_summary(Settings(), target_date_str="2026-05-06", profile_name="default_user")
        assert isinstance(result, str)
        assert "DAILY SUMMARY" in result

    def test_all_sessions_mentioned(self, monkeypatch) -> None:
        from app.main import run_daily_summary
        from app.settings import Settings
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        monkeypatch.setattr("app.main.init_db", lambda: None)

        @contextmanager
        def _fake_session():
            mock = MagicMock()
            mock.query.return_value.filter.return_value.all.return_value = []
            mock.query.return_value.filter.return_value.count.return_value = 0
            yield mock

        monkeypatch.setattr("app.main.get_session", _fake_session)
        monkeypatch.setattr(
            "app.briefing.session_snapshot_service.list_session_snapshots",
            lambda **kw: [],
            raising=False,
        )

        result = run_daily_summary(Settings(), target_date_str="2026-05-06")
        for label in ("Morning Briefing", "Europe Midday Check", "US Pre-Open Setup",
                      "US Intraday Risk Check", "Into Close Update", "Closing Wrap"):
            assert label in result, f"Daily summary missing session: {label}"

    def test_asia_note_included(self, monkeypatch) -> None:
        from app.main import run_daily_summary
        from app.settings import Settings
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        monkeypatch.setattr("app.main.init_db", lambda: None)

        @contextmanager
        def _fake_session():
            mock = MagicMock()
            mock.query.return_value.filter.return_value.all.return_value = []
            mock.query.return_value.filter.return_value.count.return_value = 0
            yield mock

        monkeypatch.setattr("app.main.get_session", _fake_session)
        monkeypatch.setattr(
            "app.briefing.session_snapshot_service.list_session_snapshots",
            lambda **kw: [],
            raising=False,
        )

        result = run_daily_summary(Settings(), target_date_str="2026-05-06")
        assert "Asia" in result
