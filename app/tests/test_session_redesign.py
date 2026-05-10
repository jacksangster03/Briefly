from __future__ import annotations

from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from app.briefing.formatter import TelegramFormatter
from app.briefing.morning_charts import build_morning_chart_bundle
from app.briefing.session_materiality import compute_materiality
from app.briefing.session_routing import next_session_window, resolve_session_window
from app.cli import cli
from app.personalization.user_profile import UserProfile
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.schemas.events import EarningsEvent, MacroDataPoint, NormalisedEvent, PricePoint, QuoteData, SectorSnapshot
from app.schemas.portfolio import PortfolioHolding


class _StubMarketData:
    def get_price_history(self, symbol: str, period: str = "3mo", interval: str = "1d"):
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        return [
            PricePoint(symbol=symbol, timestamp=start + timedelta(days=i), close=100.0 + i)
            for i in range(30)
        ]

    def get_quotes(self, symbols: list[str]):
        return [
            QuoteData(symbol=symbol, display_name=symbol, current_price=100.0, change=1.0, change_percent=1.0)
            for symbol in symbols
        ]


def _sample_profile(*, density: str = "desk") -> UserProfile:
    return UserProfile(
        name="default_user",
        timezone="Europe/Madrid",
        delivery={"email_density_mode": density},
        watchlist_primary=["NVDA", "MSFT", "AAPL", "AMD", "META"],
        portfolio_holdings=[
            PortfolioHolding(profile_name="default_user", symbol="QQQ", weight_pct=40.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="ACWI", weight_pct=35.0, bucket="core"),
            PortfolioHolding(profile_name="default_user", symbol="BND", weight_pct=25.0, bucket="hedge"),
        ],
    )


def _sample_briefing(session_key: str = "us_intraday_risk") -> MorningBriefing:
    return MorningBriefing(
        generated_at=datetime(2026, 5, 4, 15, 40, tzinfo=timezone.utc),
        session_key=session_key,
        session_title="US Intraday Risk Check",
        market_setup=MarketSetup(
            index_quotes=[
                QuoteData(symbol="^GSPC", display_name="S&P 500 (SPX)", current_price=7200, change=8, change_percent=0.11),
                QuoteData(symbol="^IXIC", display_name="Nasdaq Composite (COMP)", current_price=25000, change=80, change_percent=0.32),
                QuoteData(symbol="^DJI", display_name="Dow Jones (DJIA)", current_price=49400, change=-60, change_percent=-0.12),
                QuoteData(symbol="^RUT", display_name="Russell 2000 (RUT)", current_price=2812, change=5, change_percent=0.18),
                QuoteData(symbol="^STOXX50E", display_name="EURO STOXX 50", current_price=5881, change=-45, change_percent=-0.76),
                QuoteData(symbol="^N225", display_name="Nikkei 225", current_price=59513, change=400, change_percent=0.68),
                QuoteData(symbol="^VIX", display_name="VIX", current_price=17.40, change=0.42, change_percent=2.47),
            ],
            macro_quotes=[
                QuoteData(symbol="CL=F", display_name="WTI Crude Oil (CL1:COM)", current_price=102.8, change=0.5, change_percent=0.49),
                QuoteData(symbol="BZ=F", display_name="Brent Crude", current_price=106.0, change=1.0, change_percent=0.96),
                QuoteData(symbol="GC=F", display_name="Gold (GC1:COM)", current_price=4638.0, change=-40, change_percent=-0.86),
                QuoteData(symbol="^TNX", display_name="10Y US Treasury Yield", current_price=4.40, change=0.04, change_percent=0.92),
            ],
        ),
        macro_context=[
            MacroDataPoint(series_id="DGS2", name="US 2Y Treasury Yield", value=3.88, change=0.01),
            MacroDataPoint(series_id="DGS10", name="US 10Y Treasury Yield", value=4.40, change=0.04),
            MacroDataPoint(series_id="DGS30", name="US 30Y Treasury Yield", value=4.98, change=0.02),
            MacroDataPoint(series_id="T10Y2Y", name="10Y-2Y Yield Spread", value=0.51, change=-0.01),
        ],
        watchlist_quotes=[
            QuoteData(symbol="AMD", display_name="AMD", current_price=180, change=-7, change_percent=-3.76),
            QuoteData(symbol="AMZN", display_name="AMZN", current_price=200, change=5, change_percent=2.73),
            QuoteData(symbol="MSFT", display_name="MSFT", current_price=420, change=2, change_percent=0.48),
            QuoteData(symbol="NVDA", display_name="NVDA", current_price=900, change=-2, change_percent=-0.22),
            QuoteData(symbol="AAPL", display_name="AAPL", current_price=220, change=1, change_percent=0.45),
        ],
        portfolio_quotes=[
            QuoteData(symbol="QQQ", display_name="QQQ", current_price=500, change=-1, change_percent=-0.2),
            QuoteData(symbol="ACWI", display_name="ACWI", current_price=110, change=-1.2, change_percent=-1.1),
            QuoteData(symbol="BND", display_name="BND", current_price=72, change=-0.4, change_percent=-0.6),
        ],
        what_changed_lines=[
            "VIX: 17.40 (+0.90)",
            "US 10Y: 4.40% (+0.06%)",
            "WTI: +0.49% (+1.10%)",
        ],
    )


def test_session_window_routing_boundaries():
    tz = "Europe/Madrid"
    assert resolve_session_window(now=datetime(2026, 5, 4, 5, 30, tzinfo=timezone.utc), timezone_name=tz).key == "morning"
    assert resolve_session_window(now=datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc), timezone_name=tz).key == "europe_midday"
    assert resolve_session_window(now=datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc), timezone_name=tz).key == "us_pre_open"
    assert resolve_session_window(now=datetime(2026, 5, 4, 14, 0, tzinfo=timezone.utc), timezone_name=tz).key == "us_intraday_risk"
    assert resolve_session_window(now=datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc), timezone_name=tz).key == "into_close"
    assert resolve_session_window(now=datetime(2026, 5, 4, 21, 5, tzinfo=timezone.utc), timezone_name=tz).key == "closing_wrap"


def test_next_session_window():
    tz = "Europe/Madrid"
    nxt = next_session_window(now=datetime(2026, 5, 4, 11, 10, tzinfo=timezone.utc), timezone_name=tz)
    assert nxt.key == "us_pre_open"
    nxt2 = next_session_window(now=datetime(2026, 5, 4, 22, 10, tzinfo=timezone.utc), timezone_name=tz)
    assert nxt2.key == "morning"


def test_materiality_high_score_routes_to_breaking():
    briefing = _sample_briefing()
    briefing.session_quality_score = 0.8
    result = compute_materiality(
        briefing,
        previous={
            "session_quality": -0.2,
            "vix_level": 15.2,
            "us10y": 4.25,
            "wti_pct": -1.8,
            "brent_pct": -1.1,
            "us_avg_pct": -0.7,
            "eu_avg_pct": 0.3,
            "asia_avg_pct": -0.1,
            "portfolio_contrib_pct": 0.4,
        },
    )
    assert result.score >= 8
    assert result.decision == "breaking_alert"


def test_intraday_desk_stack_includes_required_session_cards():
    briefing = _sample_briefing("us_intraday_risk")
    profile = _sample_profile(density="desk")
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    keys = {row["chart_key"] for row in selected}
    assert bundle["meta"]["email_density_mode"] == "desk"
    assert 4 <= len(selected) <= 5
    assert "watchlist_movers_card" in keys
    assert "setup_confirmation_card" in keys
    assert "pnl_attribution_waterfall" in keys
    assert bundle["meta"]["required_charts_missing"] == []


def test_vix_174_is_watchful():
    briefing = _sample_briefing("us_intraday_risk")
    profile = _sample_profile(density="desk")
    bundle, _selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    chart_map = {row["chart_key"]: row for row in bundle["charts"]}
    assert chart_map["volatility_regime_card"]["meta"]["regime"] == "watchful"


def test_morning_full_includes_global_equity_leadership():
    briefing = _sample_briefing("morning")
    briefing.session_title = "Morning Briefing"
    profile = _sample_profile(density="full")
    bundle, selected = build_morning_chart_bundle(
        briefing=briefing,
        profile=profile,
        market_data_service=_StubMarketData(),
    )
    selected_keys = {row["chart_key"] for row in selected}
    assert bundle["meta"]["email_density_mode"] == "full"
    assert "global_relative_performance" in selected_keys


def test_intraday_output_is_shorter_and_omits_full_calendar():
    formatter = TelegramFormatter("Europe/Madrid")
    morning = _sample_briefing("morning")
    morning.session_title = "Morning Briefing"
    morning.session_mode = "weekday"
    morning.earnings_calendar = [
        EarningsEvent(symbol="MSFT", company_name="Microsoft", report_date="2026-05-05"),
        EarningsEvent(symbol="NVDA", company_name="NVIDIA", report_date="2026-05-06"),
    ]
    morning.global_news = [
        NormalisedEvent(event_id="g1", title="Macro item", summary="Rates and oil update", source="reuters"),
    ]
    morning.top_themes = [
        NormalisedEvent(event_id="t1", title="Theme item", summary="Guidance shift", source="reuters"),
    ]
    morning.sector_scan = [SectorSnapshot(sector_key="tech", display_name="Technology", etf_symbol="XLK", top_events=[])]
    for i in range(20):
        morning.what_changed_lines.append(f"line {i}")
    intraday = _sample_briefing("us_intraday_risk")
    intraday.session_title = "US Intraday Risk Check"
    intraday.session_mode = "weekday"
    intraday.earnings_calendar = morning.earnings_calendar
    morning_text = "\n".join(formatter.format_morning_briefing(morning))
    intraday_text = "\n".join(formatter.format_morning_briefing(intraday))
    assert intraday_text.count("<b>") < morning_text.count("<b>")
    assert "WHAT CHANGED" in intraday_text
    assert "EARNINGS CALENDAR" not in intraday_text
    assert "MACRO CONTEXT" not in intraday_text
    assert "MARKET SNAPSHOT" in intraday_text


def test_closing_wrap_uses_closing_language():
    formatter = TelegramFormatter("Europe/Madrid")
    closing = _sample_briefing("closing_wrap")
    closing.session_title = "Closing Wrap / Next-Day Setup"
    closing.session_mode = "weekday"
    closing.market_setup_analysis = "Regional divergence persisted into the close while rates pressure stayed elevated."
    rendered = "\n".join(formatter.format_morning_briefing(closing))
    assert "DAY VERDICT" in rendered
    assert "CONFIRMED DRIVERS" in rendered
    assert "TOMORROW SETUP" in rendered
    assert "WATCH INTO CLOSE" not in rendered


def test_provider_health_note_user_facing():
    note = TelegramFormatter._provider_health_note("alpha_vantage:0, finnhub:100, fmp:0, gdelt:0")
    assert note == "Provider notes: GDELT unavailable; FMP unavailable; core providers available."


def test_cli_morning_force_override_passes_force_flag(monkeypatch):
    called: dict = {}

    def _fake_run(settings, **kwargs):
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "morning", "--force-morning"])
    assert result.exit_code == 0, result.output
    assert called["kwargs"].get("force_morning") is True


def test_cli_brief_dispatches_to_session_brief(monkeypatch):
    called = {"count": 0}

    def _fake_run(settings, **kwargs):
        called["count"] += 1
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_session_brief", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["--dry-run", "brief"])
    assert result.exit_code == 0, result.output
    assert called["count"] == 1


def test_cli_session_preview_forces_requested_session_key(monkeypatch):
    called: dict = {}

    def _fake_run(settings, **kwargs):
        called["settings"] = settings
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["session-preview", "--session", "morning"])
    assert result.exit_code == 0, result.output
    assert "QA SESSION PREVIEW, NOT LIVE" in result.output
    assert called["kwargs"].get("auto_route_session") is False
    assert called["kwargs"].get("session_override") == "morning"
    assert called["kwargs"].get("qa_session_preview") is True
    assert called["kwargs"].get("command_source") == "cli:session-preview"
    assert bool(called["settings"].dry_run) is True
    assert bool(getattr(called["settings"], "persist_dry_run_session_snapshots", True)) is False


def test_cli_session_preview_preopen_forces_us_pre_open(monkeypatch):
    called: dict = {}

    def _fake_run(settings, **kwargs):
        called["kwargs"] = kwargs

    monkeypatch.setattr("app.main.run_morning_briefing", _fake_run)
    runner = CliRunner()
    result = runner.invoke(cli, ["session-preview", "--session", "us_pre_open"])
    assert result.exit_code == 0, result.output
    assert called["kwargs"].get("session_override") == "us_pre_open"


# ---------------------------------------------------------------------------
# Idempotency cross-session tests (tasks a-j)
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from app.db.models import SessionSendState  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import run_morning_briefing, run_catch_up  # noqa: E402
from app.settings import Settings  # noqa: E402


class _StubGenerator2:
    def __init__(self, *args, **kwargs):
        pass

    def generate(self, *, session_key: str = "morning", session_title: str = "Morning Briefing") -> MorningBriefing:
        return MorningBriefing(
            generated_at=datetime(2026, 5, 6, 8, 0, tzinfo=timezone.utc),
            session_key=session_key,
            session_title=session_title,
        )


class _StubEmailFormatter2:
    def __init__(self, *args, **kwargs):
        pass

    def format_morning_briefing(self, briefing):
        from app.schemas.delivery import EmailRenderResult
        return EmailRenderResult(
            subject=briefing.session_title,
            plain_text=f"{briefing.session_title}: email",
            html_body=f"<p>{briefing.session_title}: email</p>",
            inline_assets=[],
        )


class _StubTelegramFormatter2:
    def __init__(self, *args, **kwargs):
        pass

    def format_morning_briefing(self, briefing):
        return [f"{briefing.session_title}: telegram"]


class _StubLLMRenderer2:
    def __init__(self, *args, **kwargs):
        pass

    def render_morning(self, *, deterministic_email, **kwargs):
        return SimpleNamespace(active_email=deterministic_email, shadow_preview=None)


def _apply_session_monkeypatches(monkeypatch, send_log: list):
    """Wire up all stubs needed for run_morning_briefing tests."""

    class _FakeEmailMessenger:
        name = "email"
        dry_run = False
        last_error = ""

        def __init__(self, s):
            pass

        def is_configured(self):
            return True

        def send_rich(self, *, subject, plain_text, html_body, inline_assets=None):
            send_log.append(f"email:{subject}")
            return True

    monkeypatch.setattr("app.main.load_user_profile", lambda s: UserProfile(name="default_user"))
    monkeypatch.setattr("app.main.load_sector_universe", lambda s: object())
    monkeypatch.setattr("app.main._build_services", lambda s: (object(), object(), object()))
    monkeypatch.setattr("app.main.MorningBriefingGenerator", _StubGenerator2)
    monkeypatch.setattr("app.main.TelegramFormatter", _StubTelegramFormatter2)
    monkeypatch.setattr("app.main.EmailFormatter", _StubEmailFormatter2)
    monkeypatch.setattr("app.main.LLMEmailRenderer", _StubLLMRenderer2)
    monkeypatch.setattr("app.main.EmailMessenger", _FakeEmailMessenger)
    monkeypatch.setattr("app.main.load_previous_snapshot", lambda **kw: (None, {}))
    monkeypatch.setattr("app.main.persist_snapshot", lambda **kw: None)
    monkeypatch.setattr("app.main.record_sent_events", lambda *a, **kw: None)


def _settings_email_live(tmp_path) -> Settings:
    return Settings(
        dry_run=False,
        delivery_channel="email",
        email_user="sender@example.com",
        email_password="secret",
        email_to="recipient@example.com",
        data_dir=str(tmp_path),
    )


# b. Morning send does not block Europe Midday.
def test_morning_send_does_not_block_europe_midday(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []
    _apply_session_monkeypatches(monkeypatch, send_log)
    settings = _settings_email_live(tmp_path)

    run_morning_briefing(settings, auto_route_session=False, session_override="morning")
    run_morning_briefing(settings, auto_route_session=False, session_override="europe_midday")

    assert any("morning" in s.lower() or "Morning" in s for s in send_log), send_log
    assert any("europe" in s.lower() or "Midday" in s for s in send_log), send_log

    with get_session() as db:
        states = db.query(SessionSendState).filter_by(profile_name="default_user").all()
    session_keys_sent = {s.session_key for s in states if s.success}
    assert "morning" in session_keys_sent
    assert "europe_midday" in session_keys_sent


# c. Morning send does not block US Pre-Open.
def test_morning_send_does_not_block_us_pre_open(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []
    _apply_session_monkeypatches(monkeypatch, send_log)
    settings = _settings_email_live(tmp_path)

    run_morning_briefing(settings, auto_route_session=False, session_override="morning")
    run_morning_briefing(settings, auto_route_session=False, session_override="us_pre_open")

    with get_session() as db:
        states = db.query(SessionSendState).filter_by(profile_name="default_user").all()
    session_keys_sent = {s.session_key for s in states if s.success}
    assert "morning" in session_keys_sent
    assert "us_pre_open" in session_keys_sent


# d. Different sessions create different SessionSendState rows.
def test_different_sessions_create_different_state_rows(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []
    _apply_session_monkeypatches(monkeypatch, send_log)
    settings = _settings_email_live(tmp_path)

    for session in ["morning", "europe_midday", "us_pre_open"]:
        run_morning_briefing(settings, auto_route_session=False, session_override=session)

    with get_session() as db:
        rows = db.query(SessionSendState).filter_by(profile_name="default_user", channel="email").all()

    assert len(rows) == 3
    keys = {r.session_key for r in rows}
    assert keys == {"morning", "europe_midday", "us_pre_open"}
    for r in rows:
        assert r.message_type == f"session_brief:{r.session_key}"
        assert r.success is True


# e. Default mode only allows morning + us_pre_open (with respect_cadence=True).
def test_default_mode_suppresses_europe_midday(monkeypatch, tmp_path, validation_isolated_db):
    suppressed: list[str] = []
    send_log: list[str] = []

    def _fake_load_profile(s):
        return UserProfile(name="default_user", delivery={"session_mode": "default"})

    monkeypatch.setattr("app.main.load_user_profile", _fake_load_profile)
    monkeypatch.setattr("app.main.load_sector_universe", lambda s: object())
    monkeypatch.setattr("app.main._build_services", lambda s: (object(), object(), object()))
    monkeypatch.setattr("app.main.has_cadence_marker", lambda *a, **kw: False)
    monkeypatch.setattr("app.main.record_cadence_marker", lambda **kw: None)

    logged: list[str] = []
    real_logger_info = __import__("app.main", fromlist=["logger"]).logger.info

    def _capture_info(msg, *args):
        formatted = msg % args if args else msg
        logged.append(formatted)

    import app.main as main_mod
    monkeypatch.setattr(main_mod.logger, "info", _capture_info)

    settings = _settings_email_live(tmp_path)
    run_morning_briefing(settings, auto_route_session=False, session_override="europe_midday", respect_cadence=True)

    assert any("not_in_mode_schedule" in line for line in logged), logged


# f. Active mode allows all scheduled sessions when cadence marker absent.
def test_active_mode_allows_europe_midday(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []

    def _fake_load_profile(s):
        return UserProfile(name="default_user", delivery={"session_mode": "active", "suppress_low_materiality": False})

    _apply_session_monkeypatches(monkeypatch, send_log)
    monkeypatch.setattr("app.main.load_user_profile", _fake_load_profile)
    monkeypatch.setattr("app.main.has_cadence_marker", lambda *a, **kw: False)
    monkeypatch.setattr("app.main.record_cadence_marker", lambda **kw: None)

    settings = _settings_email_live(tmp_path)
    run_morning_briefing(settings, auto_route_session=False, session_override="europe_midday", respect_cadence=True)

    with get_session() as db:
        rows = db.query(SessionSendState).filter_by(
            profile_name="default_user", session_key="europe_midday", channel="email"
        ).all()
    assert rows, "europe_midday should have been sent in active mode"
    assert rows[0].success is True


# g. active-mode + ignore-materiality via catch-up sends all eligible sessions.
def test_catch_up_active_mode_ignore_materiality(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []
    _apply_session_monkeypatches(monkeypatch, send_log)

    import datetime as _dt

    # Simulate local time = 16:00 CEST (14:00 UTC) so morning, midday, pre-open, intraday have started.
    fake_now = _dt.datetime(2026, 5, 6, 14, 0, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr("app.main.datetime", type("_FakeDT", (), {
        "now": staticmethod(lambda tz=None: fake_now.astimezone(tz) if tz else fake_now),
    }))

    settings = _settings_email_live(tmp_path)
    summary = run_catch_up(
        settings,
        force_all=False,
        ignore_materiality=True,
        active_mode=True,
        command_source="test:catch-up",
    )

    sent = [r for r in summary if r["action"] == "sent"]
    session_keys_attempted = {r["session"] for r in sent}
    # All sessions whose window started before 16:00 CEST (14:00 UTC) should be attempted.
    assert "morning" in session_keys_attempted
    assert "europe_midday" in session_keys_attempted
    assert "us_pre_open" in session_keys_attempted


# h. catch-up sends missed sessions but does not resend already-sent sessions.
def test_catch_up_skips_already_sent_sessions(monkeypatch, tmp_path, validation_isolated_db):
    send_log: list[str] = []
    _apply_session_monkeypatches(monkeypatch, send_log)

    import datetime as _dt
    fake_now = _dt.datetime(2026, 5, 6, 14, 0, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr("app.main.datetime", type("_FakeDT", (), {
        "now": staticmethod(lambda tz=None: fake_now.astimezone(tz) if tz else fake_now),
    }))

    settings = _settings_email_live(tmp_path)

    # First, send morning via normal path so it records success in SessionSendState.
    run_morning_briefing(settings, auto_route_session=False, session_override="morning")
    send_log.clear()

    # Now run catch-up in active mode: morning should be skipped, others attempted.
    summary = run_catch_up(
        settings,
        force_all=False,
        ignore_materiality=True,
        active_mode=True,
        command_source="test:catch-up",
    )

    already_sent = [r for r in summary if r["action"] == "already_sent"]
    assert any(r["session"] == "morning" for r in already_sent), summary


# i. Stale scheduler lock is detected and reported clearly.
def test_schedule_status_detects_held_lock(monkeypatch, tmp_path, validation_isolated_db):
    import fcntl as _fcntl

    class _LockedFcntl:
        LOCK_EX = _fcntl.LOCK_EX
        LOCK_NB = _fcntl.LOCK_NB
        LOCK_UN = _fcntl.LOCK_UN

        @staticmethod
        def flock(fd, flags):
            if flags & _fcntl.LOCK_NB:
                raise OSError("locked")

    monkeypatch.setattr("app.main.load_user_profile", lambda s: UserProfile(name="default_user"))

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        import os
        data_dir = str(tmp_path / "data")
        os.makedirs(f"{data_dir}/state", exist_ok=True)
        lock_path = f"{data_dir}/state/scheduler.lock"
        open(lock_path, "a").close()

        settings_args = [f"--data-dir={data_dir}"] if False else []
        import app.cli as cli_mod
        import fcntl as real_fcntl

        orig_open = open

        def _patched_flock(fd, flags):
            if flags & real_fcntl.LOCK_NB:
                raise OSError("simulated lock held")
            real_fcntl.flock(fd, flags)

        monkeypatch.setattr(real_fcntl, "flock", _patched_flock)

        from app.settings import Settings as _S
        monkeypatch.setattr("app.cli.get_settings", lambda: _S(dry_run=True, data_dir=data_dir))

        result = runner.invoke(cli, ["schedule-status"])
        assert result.exit_code == 0, result.output
        assert "held" in result.output.lower() or "unable" in result.output.lower(), result.output


# j. Scheduler logs suppression reason for each non-sent session.
def test_scheduler_logs_suppression_reason_for_non_sent_session(monkeypatch, tmp_path, validation_isolated_db):
    logged: list[str] = []

    def _fake_load_profile(s):
        return UserProfile(name="default_user", delivery={"session_mode": "default"})

    monkeypatch.setattr("app.main.load_user_profile", _fake_load_profile)
    monkeypatch.setattr("app.main.load_sector_universe", lambda s: object())
    monkeypatch.setattr("app.main._build_services", lambda s: (object(), object(), object()))
    monkeypatch.setattr("app.main.has_cadence_marker", lambda *a, **kw: False)
    monkeypatch.setattr("app.main.record_cadence_marker", lambda **kw: None)

    import app.main as main_mod
    monkeypatch.setattr(main_mod.logger, "info", lambda msg, *a: logged.append((msg % a) if a else msg))

    settings = _settings_email_live(tmp_path)
    run_morning_briefing(settings, auto_route_session=False, session_override="into_close", respect_cadence=True)

    suppression_logs = [line for line in logged if "not_in_mode_schedule" in line or "suppress" in line.lower()]
    assert suppression_logs, f"Expected a suppression log for into_close in default mode. Got: {logged}"
    assert "into_close" in suppression_logs[0]
