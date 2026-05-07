from __future__ import annotations

from app.main import run_daily_summary
from app.settings import Settings


def test_daily_summary_smoke(monkeypatch) -> None:
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
    summary = run_daily_summary(Settings(), target_date_str="2026-05-06")
    assert "DAILY SUMMARY" in summary
