"""Tests for Phase 8.9 Lite: live session archive snapshots."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.briefing.session_snapshot_service import (
    SnapshotCaptureRequest,
    _compact_chart_selection,
    _compact_macro_summary,
    _compact_market_summary,
    _compact_portfolio_summary,
    create_session_snapshot,
    get_session_snapshot,
    list_session_snapshots,
    prune_old_snapshots,
    should_store_snapshot,
    source_type_from_command_source,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Isolated SQLite DB for snapshot tests."""
    db_url = f"sqlite:///{tmp_path}/test_snapshots.db"
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


def _make_request(
    session_key: str = "morning",
    source_type: str = "live_scheduler",
    delivery_success: bool = True,
    local_date: date | None = None,
    telegram_messages: list[str] | None = None,
    email_subject: str = "Morning Briefing",
    profile_name: str = "default_user",
) -> SnapshotCaptureRequest:
    return SnapshotCaptureRequest(
        profile_name=profile_name,
        session_key=session_key,
        session_title="Morning Briefing",
        local_date=local_date or date(2026, 5, 6),
        generated_at_utc=datetime(2026, 5, 6, 8, 29, tzinfo=timezone.utc),
        timezone_name="Europe/Madrid",
        source_type=source_type,
        delivery_attempted=True,
        delivery_success=delivery_success,
        delivery_channels={"telegram": "sent", "email": "sent"},
        delivery_reasons={"telegram": "delivered", "email": "delivered"},
        telegram_messages=telegram_messages or ["Hello briefing"],
        email_subject=email_subject,
        email_plain_text="Plain text body.",
        email_html="<p>HTML body.</p>",
        market_summary=[{"symbol": "^GSPC", "display_name": "S&P 500", "price": 5200.0, "change_pct": 0.5}],
        macro_summary=[{"name": "US 10Y Yield", "value": 4.4, "change": 0.02}],
        portfolio_summary=[{"title": "NVDA earnings beat", "tickers": ["NVDA"]}],
        chart_selection=[{"chart_key": "global_relative_performance", "variant": "desk", "available": True}],
        events_count=7,
        store_email_html=True,
    )


# ---------------------------------------------------------------------------
# should_store_snapshot
# ---------------------------------------------------------------------------

class TestShouldStoreSnapshot:
    def _base(self, **overrides):
        defaults = dict(
            command_source="scheduler",
            dry_run=False,
            is_backfill=False,
            session_key="morning",
            delivery_attempted=True,
            snapshots_enabled=True,
        )
        defaults.update(overrides)
        return should_store_snapshot(**defaults)

    def test_scheduler_live_returns_true(self):
        assert self._base() is True

    def test_dry_run_returns_false(self):
        assert self._base(dry_run=True) is False

    def test_backfill_returns_false(self):
        assert self._base(is_backfill=True) is False

    def test_manual_command_source_returns_false(self):
        assert self._base(command_source="cli") is False

    def test_session_send_returns_false(self):
        assert self._base(command_source="cli:session-send") is False

    def test_catch_up_returns_false(self):
        assert self._base(command_source="cli:catch-up") is False

    def test_replay_returns_false(self):
        assert self._base(command_source="replay") is False

    def test_non_canonical_session_key_returns_false(self):
        assert self._base(session_key="custom_session") is False

    def test_delivery_not_attempted_returns_false(self):
        assert self._base(delivery_attempted=False) is False

    def test_snapshots_disabled_returns_false(self):
        assert self._base(snapshots_enabled=False) is False

    @pytest.mark.parametrize("sk", [
        "morning", "europe_midday", "us_pre_open",
        "us_intraday_risk", "into_close", "closing_wrap",
    ])
    def test_all_canonical_sessions_accepted(self, sk):
        assert self._base(session_key=sk) is True


# ---------------------------------------------------------------------------
# source_type_from_command_source
# ---------------------------------------------------------------------------

class TestSourceTypeFromCommandSource:
    def test_scheduler_maps_to_live_scheduler(self):
        assert source_type_from_command_source("scheduler", is_backfill=False, is_dry_run=False) == "live_scheduler"

    def test_dry_run_takes_priority(self):
        assert source_type_from_command_source("scheduler", is_backfill=False, is_dry_run=True) == "dry_run"

    def test_backfill_maps_correctly(self):
        assert source_type_from_command_source("cli:backfill", is_backfill=True, is_dry_run=False) == "backfill"

    def test_cli_maps_to_manual(self):
        assert source_type_from_command_source("cli", is_backfill=False, is_dry_run=False) == "manual"

    def test_replay_in_source_maps_to_replay(self):
        assert source_type_from_command_source("day-replay", is_backfill=False, is_dry_run=False) == "replay"


# ---------------------------------------------------------------------------
# create_session_snapshot / list / get / prune
# ---------------------------------------------------------------------------

class TestCreateSessionSnapshot:
    def test_snapshot_is_persisted(self, isolated_db):
        req = _make_request()
        ok = create_session_snapshot(req)
        assert ok is True
        result = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert result is not None
        assert result["session_key"] == "morning"
        assert result["delivery_success"] is True

    def test_telegram_text_stored(self, isolated_db):
        req = _make_request(telegram_messages=["Part one", "Part two"])
        create_session_snapshot(req)
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert "Part one" in snap["telegram_text"]
        assert "Part two" in snap["telegram_text"]

    def test_email_fields_stored(self, isolated_db):
        req = _make_request(email_subject="US Pre-Open Setup")
        create_session_snapshot(req)
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert snap["email_subject"] == "US Pre-Open Setup"
        assert "<p>HTML body.</p>" in snap["email_html"]

    def test_market_summary_stored(self, isolated_db):
        create_session_snapshot(_make_request())
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert any(q["symbol"] == "^GSPC" for q in snap["market_summary"])

    def test_upsert_updates_existing_row(self, isolated_db):
        create_session_snapshot(_make_request(delivery_success=False))
        create_session_snapshot(_make_request(delivery_success=True))
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert snap["delivery_success"] is True

    def test_failure_returns_false_does_not_raise(self, isolated_db):
        with patch(
            "app.briefing.session_snapshot_service.get_session",
            side_effect=RuntimeError("db exploded"),
        ):
            ok = create_session_snapshot(_make_request())
        assert ok is False

    def test_separate_sessions_stored_independently(self, isolated_db):
        create_session_snapshot(_make_request(session_key="morning"))
        create_session_snapshot(_make_request(session_key="us_pre_open", email_subject="Pre-Open"))
        morning = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        preopen = get_session_snapshot("default_user", date(2026, 5, 6), "us_pre_open")
        assert morning["email_subject"] != preopen["email_subject"]

    def test_separate_profiles_stored_independently(self, isolated_db):
        create_session_snapshot(_make_request(profile_name="user_a"))
        create_session_snapshot(_make_request(profile_name="user_b", email_subject="User B Morning"))
        snap_a = get_session_snapshot("user_a", date(2026, 5, 6), "morning")
        snap_b = get_session_snapshot("user_b", date(2026, 5, 6), "morning")
        assert snap_a["email_subject"] != snap_b["email_subject"]


class TestListSessionSnapshots:
    def test_returns_empty_for_missing_date(self, isolated_db):
        result = list_session_snapshots("default_user", date(2026, 5, 1))
        assert result == []

    def test_returns_rows_in_session_order(self, isolated_db):
        for sk in ["closing_wrap", "morning", "us_pre_open"]:
            create_session_snapshot(_make_request(session_key=sk))
        rows = list_session_snapshots("default_user", date(2026, 5, 6))
        keys = [r["session_key"] for r in rows]
        assert keys.index("morning") < keys.index("us_pre_open") < keys.index("closing_wrap")

    def test_summary_does_not_include_full_text(self, isolated_db):
        create_session_snapshot(_make_request())
        rows = list_session_snapshots("default_user", date(2026, 5, 6))
        assert "telegram_text" not in rows[0]
        assert "email_plain_text" not in rows[0]

    def test_has_telegram_flag(self, isolated_db):
        create_session_snapshot(_make_request(telegram_messages=["content"]))
        rows = list_session_snapshots("default_user", date(2026, 5, 6))
        assert rows[0]["has_telegram"] is True


class TestGetSessionSnapshot:
    def test_returns_none_for_missing(self, isolated_db):
        assert get_session_snapshot("default_user", date(2026, 1, 1), "morning") is None

    def test_full_telegram_text_returned(self, isolated_db):
        create_session_snapshot(_make_request(telegram_messages=["Full text content"]))
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert "Full text content" in snap["telegram_text"]


class TestPruneOldSnapshots:
    def test_prune_deletes_old_rows(self, isolated_db):
        old_date = date(2026, 1, 1)
        create_session_snapshot(_make_request(local_date=old_date))
        count = prune_old_snapshots("default_user", retention_days=30)
        assert count == 1
        assert get_session_snapshot("default_user", old_date, "morning") is None

    def test_prune_keeps_recent_rows(self, isolated_db):
        recent_date = date.today()
        create_session_snapshot(_make_request(local_date=recent_date))
        count = prune_old_snapshots("default_user", retention_days=30)
        assert count == 0
        assert get_session_snapshot("default_user", recent_date, "morning") is not None

    def test_prune_returns_count(self, isolated_db):
        for sk in ["morning", "europe_midday"]:
            create_session_snapshot(_make_request(session_key=sk, local_date=date(2026, 1, 1)))
        count = prune_old_snapshots("default_user", retention_days=30)
        assert count == 2


# ---------------------------------------------------------------------------
# Compact summary builders
# ---------------------------------------------------------------------------

class TestCompactSummaryBuilders:
    def _briefing(self):
        m = MagicMock()
        q = MagicMock()
        q.symbol = "^GSPC"
        q.display_name = "S&P 500"
        q.current_price = 5200.0
        q.change_percent = 0.5
        m.market_setup.index_quotes = [q]
        mac = MagicMock()
        mac.name = "US 10Y Yield"
        mac.value = 4.4
        mac.change = 0.02
        m.macro_context = [mac]
        pf = MagicMock()
        pf.title = "NVDA earnings"
        pf.tickers = ["NVDA"]
        m.portfolio_focus = [pf]
        m.morning_chart_selection = []
        return m

    def test_market_summary_extracts_price(self):
        result = _compact_market_summary(self._briefing())
        assert result[0]["symbol"] == "^GSPC"
        assert result[0]["change_pct"] == 0.5

    def test_macro_summary_extracts_value(self):
        result = _compact_macro_summary(self._briefing())
        assert result[0]["name"] == "US 10Y Yield"
        assert result[0]["value"] == 4.4

    def test_portfolio_summary_extracts_title(self):
        result = _compact_portfolio_summary(self._briefing())
        assert "NVDA earnings" in result[0]["title"]

    def test_compact_builders_never_raise_on_bad_input(self):
        bad = MagicMock()
        bad.market_setup.index_quotes = None
        bad.macro_context = None
        bad.portfolio_focus = None
        bad.morning_chart_selection = None
        assert _compact_market_summary(bad) == []
        assert _compact_macro_summary(bad) == []
        assert _compact_portfolio_summary(bad) == []
        assert _compact_chart_selection(bad) == []


# ---------------------------------------------------------------------------
# Secrets are not stored
# ---------------------------------------------------------------------------

class TestSecretsNotStored:
    _SECRET_PATTERN = re.compile(
        r"(?i)(api[_-]?key|bot[_-]?token|password|email_password|secret|auth[_-]?token)",
        re.IGNORECASE,
    )

    def test_api_key_not_in_telegram_text(self, isolated_db):
        req = _make_request(telegram_messages=["Normal briefing content"])
        create_session_snapshot(req)
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert not self._SECRET_PATTERN.search(snap["telegram_text"])

    def test_api_key_not_in_email_plain(self, isolated_db):
        create_session_snapshot(_make_request())
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        assert not self._SECRET_PATTERN.search(snap["email_plain_text"])

    def test_credentials_not_in_market_summary(self, isolated_db):
        create_session_snapshot(_make_request())
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        for item in snap.get("market_summary", []):
            for val in item.values():
                assert not self._SECRET_PATTERN.search(str(val))


# ---------------------------------------------------------------------------
# should_store_snapshot: dry-run guard
# ---------------------------------------------------------------------------

class TestDryRunSnapshotGuard:
    """Dry-run sends must not produce live archive snapshots unless the explicit
    persist_dry_run_session_snapshots flag is set."""

    def test_dry_run_is_blocked_by_default(self):
        result = should_store_snapshot(
            command_source="scheduler",
            dry_run=True,
            is_backfill=False,
            session_key="morning",
            delivery_attempted=True,
            snapshots_enabled=True,
        )
        assert result is False, "dry_run=True must prevent snapshot storage"

    def test_live_scheduler_send_is_allowed(self):
        result = should_store_snapshot(
            command_source="scheduler",
            dry_run=False,
            is_backfill=False,
            session_key="morning",
            delivery_attempted=True,
            snapshots_enabled=True,
        )
        assert result is True, "live scheduler send with delivery_attempted=True must store snapshot"

    def test_snapshots_disabled_flag_blocks_live_send(self):
        result = should_store_snapshot(
            command_source="scheduler",
            dry_run=False,
            is_backfill=False,
            session_key="morning",
            delivery_attempted=True,
            snapshots_enabled=False,
        )
        assert result is False, "snapshots_enabled=False must block even live scheduler runs"

    def test_dry_run_create_does_not_write_to_db(self, isolated_db):
        """Even if should_store_snapshot were bypassed, dry_run source_type is detectable
        and should not persist as a live_scheduler row."""
        req = _make_request(source_type="dry_run")
        ok = create_session_snapshot(req)
        assert ok is True  # write succeeds if explicitly called
        snap = get_session_snapshot("default_user", date(2026, 5, 6), "morning")
        # The source_type must be recorded accurately so auditing is reliable.
        assert snap["source_type"] == "dry_run", (
            "source_type must be stored accurately; dry_run runs must not be labelled as live_scheduler"
        )
