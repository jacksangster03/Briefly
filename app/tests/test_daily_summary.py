from __future__ import annotations

from app.main import run_daily_summary
from app.settings import Settings
from app.db.models import SentMessage, SessionSendState
from app.db.session import get_session
from datetime import date, datetime
from app.briefing.session_snapshot_service import (
    SnapshotCaptureRequest,
    create_session_snapshot,
)


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


def test_daily_summary_today_not_blocked_by_previous_day_midnight_closing_wrap(validation_isolated_db, monkeypatch) -> None:
    # Simulate a closing-wrap catch-up attributed to previous local date.
    with get_session() as db:
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="closing_wrap",
                local_date=date(2026, 5, 7),
                replay_namespace="",
                message_type="session_brief:closing_wrap",
                idempotency_key="k-old",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=datetime(2026, 5, 7, 22, 2, 0),  # 00:02 local on next day
                updated_at=datetime(2026, 5, 7, 22, 2, 0),
            )
        )
        db.add(
            SessionSendState(
                profile_name="default_user",
                channel="email",
                session_key="morning",
                local_date=date(2026, 5, 8),
                replay_namespace="",
                message_type="session_brief:morning",
                idempotency_key="k-new",
                success=True,
                in_progress=False,
                command_source="scheduler",
                sent_at=datetime(2026, 5, 8, 6, 2, 0),
                updated_at=datetime(2026, 5, 8, 6, 2, 0),
            )
        )
    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )
    summary = run_daily_summary(Settings(), target_date_str="2026-05-08")
    assert (
        "Closing Wrap / Next-Day Setup: not sent" in summary
        or "Closing Wrap / Next-Day Setup: upcoming" in summary
    )


def test_daily_summary_breaking_alerts_zero_when_no_sent_messages_today(validation_isolated_db, monkeypatch) -> None:
    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )
    summary = run_daily_summary(Settings(), target_date_str="2026-05-10")
    assert "Breaking alerts:   0 (today)" in summary


def test_daily_summary_excludes_delivery_alerts_and_failed_attempts_from_breaking_count(validation_isolated_db, monkeypatch) -> None:
    with get_session() as db:
        db.add(
            SentMessage(
                message_type="delivery_alert",
                channel="telegram",
                content_preview="[DELIVERY FAILURE] ...",
                content_hash="da-ok",
                sent_at=datetime(2026, 5, 10, 8, 0, 0),
                success=True,
            )
        )
        db.add(
            SentMessage(
                message_type="delivery_alert",
                channel="telegram",
                content_preview="[DELIVERY FAILURE] ...",
                content_hash="da-fail",
                sent_at=datetime(2026, 5, 10, 9, 0, 0),
                success=False,
            )
        )
        db.add(
            SentMessage(
                message_type="breaking",
                channel="telegram",
                content_preview="Breaking attempt failed",
                content_hash="br-fail",
                sent_at=datetime(2026, 5, 10, 10, 0, 0),
                success=False,
            )
        )

    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )
    summary = run_daily_summary(Settings(), target_date_str="2026-05-10")
    assert "Breaking alerts:   0 (today)" in summary


def test_daily_summary_counts_only_real_channels_for_breaking_alerts(validation_isolated_db, monkeypatch) -> None:
    with get_session() as db:
        db.add(
            SentMessage(
                message_type="breaking",
                channel="stub",
                content_preview="test stub breaking",
                content_hash="stub-1",
                sent_at=datetime(2026, 5, 10, 11, 0, 0),
                success=True,
            )
        )
        db.add(
            SentMessage(
                message_type="breaking",
                channel="telegram",
                content_preview="real breaking",
                content_hash="real-1",
                sent_at=datetime(2026, 5, 10, 12, 0, 0),
                success=True,
            )
        )
        db.add(
            SentMessage(
                message_type="breaking",
                channel="email",
                content_preview="real breaking email",
                content_hash="real-2",
                sent_at=datetime(2026, 5, 10, 13, 0, 0),
                success=True,
            )
        )

    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )
    summary = run_daily_summary(Settings(), target_date_str="2026-05-10")
    assert "Breaking alerts:   2 (today)" in summary


def test_daily_summary_historical_breaking_alerts_not_counted_for_today(validation_isolated_db, monkeypatch) -> None:
    with get_session() as db:
        db.add(
            SentMessage(
                message_type="breaking",
                channel="telegram",
                content_preview="old breaking",
                content_hash="old-1",
                sent_at=datetime(2026, 5, 9, 12, 0, 0),
                success=True,
            )
        )

    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr(
        "app.briefing.session_snapshot_service.list_session_snapshots",
        lambda **kw: [],
        raising=False,
    )
    summary = run_daily_summary(Settings(), target_date_str="2026-05-10")
    assert "Breaking alerts:   0 (today)" in summary


# ---------------------------------------------------------------------------
# Snapshot count in daily summary (regression tests for list_session_snapshots
# kwarg mismatch bug that caused "Archived snapshots: 0/6" even when all six
# sessions had been successfully archived).
# ---------------------------------------------------------------------------

def _make_snapshot_request(session_key: str, target_date: date) -> SnapshotCaptureRequest:
    from datetime import timezone
    return SnapshotCaptureRequest(
        profile_name="default_user",
        session_key=session_key,
        session_title=session_key.replace("_", " ").title(),
        local_date=target_date,
        generated_at_utc=datetime(2026, 5, 6, 8, 0, tzinfo=timezone.utc),
        timezone_name="Europe/Madrid",
        source_type="live_scheduler",
        delivery_attempted=True,
        delivery_success=True,
        delivery_channels={"telegram": "sent", "email": "sent"},
        delivery_reasons={"telegram": "delivered", "email": "delivered"},
        telegram_messages=["Briefing content"],
        email_subject=f"{session_key} briefing",
        email_plain_text="Plain text.",
        email_html="<p>HTML.</p>",
        market_summary=[],
        macro_summary=[],
        portfolio_summary=[],
        chart_selection=[],
        events_count=3,
        store_email_html=True,
    )


def test_daily_summary_snapshot_count_matches_db_state(validation_isolated_db, monkeypatch) -> None:
    """Archived snapshots line must reflect actual DB rows, not always zero."""
    target = date(2026, 5, 6)
    session_keys = ["morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"]

    # Write matching SessionSendState rows so the sessions appear in the summary.
    with get_session() as db:
        for sk in session_keys:
            db.add(
                SessionSendState(
                    profile_name="default_user",
                    channel="email",
                    session_key=sk,
                    local_date=target,
                    replay_namespace="",
                    message_type=f"session_brief:{sk}",
                    idempotency_key=f"k-{sk}",
                    success=True,
                    in_progress=False,
                    command_source="scheduler",
                    sent_at=datetime(2026, 5, 6, 8, 0, 0),
                    updated_at=datetime(2026, 5, 6, 8, 0, 0),
                )
            )

    # Write snapshot rows for three of the six sessions.
    for sk in session_keys[:3]:
        create_session_snapshot(_make_snapshot_request(sk, target))

    monkeypatch.setattr("app.main.init_db", lambda: None)
    summary = run_daily_summary(Settings(), target_date_str="2026-05-06", profile_name="default_user")

    assert "Archived snapshots: 3/6" in summary, (
        f"Expected '3/6' in summary but got:\n{summary}"
    )


def test_daily_summary_all_six_archived_shows_six_of_six(validation_isolated_db, monkeypatch) -> None:
    """When all six sessions are archived the count shows 6/6."""
    target = date(2026, 5, 6)
    session_keys = ["morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"]

    with get_session() as db:
        for sk in session_keys:
            db.add(
                SessionSendState(
                    profile_name="default_user",
                    channel="email",
                    session_key=sk,
                    local_date=target,
                    replay_namespace="",
                    message_type=f"session_brief:{sk}",
                    idempotency_key=f"k-{sk}",
                    success=True,
                    in_progress=False,
                    command_source="scheduler",
                    sent_at=datetime(2026, 5, 6, 8, 0, 0),
                    updated_at=datetime(2026, 5, 6, 8, 0, 0),
                )
            )

    for sk in session_keys:
        create_session_snapshot(_make_snapshot_request(sk, target))

    monkeypatch.setattr("app.main.init_db", lambda: None)
    summary = run_daily_summary(Settings(), target_date_str="2026-05-06", profile_name="default_user")

    assert "Archived snapshots: 6/6" in summary, (
        f"Expected '6/6' in summary but got:\n{summary}"
    )
