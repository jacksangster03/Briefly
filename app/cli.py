"""CLI interface for Briefly using Click."""

from __future__ import annotations

import json
from pathlib import Path

import click

from app.db.session import init_db
from app.logger import setup_logging
from app.settings import get_settings


@click.group()
@click.option("--dry-run/--no-dry-run", default=None, help="Override DRY_RUN setting.")
@click.option(
    "--show-output/--no-show-output",
    default=False,
    help="Print rendered message payloads to the terminal for manual inspection. "
         "Works with or without --dry-run.",
)
@click.option(
    "--email-only",
    is_flag=True,
    default=False,
    help="Send only through email for this run (no Telegram sends).",
)
@click.option(
    "--telegram-only",
    is_flag=True,
    default=False,
    help="Send only through Telegram for this run (no email sends).",
)
@click.pass_context
def cli(ctx, dry_run, show_output, email_only, telegram_only):
    """Briefly: Portfolio intelligence with market briefing delivery."""
    setup_logging()
    ctx.ensure_object(dict)
    settings = get_settings()
    if dry_run is not None:
        settings.dry_run = dry_run
    settings.show_output = show_output
    if email_only and telegram_only:
        raise click.UsageError("Use only one channel override: --email-only or --telegram-only.")
    if email_only:
        settings.delivery_channel = "email"
    elif telegram_only:
        settings.delivery_channel = "telegram"
    ctx.obj["settings"] = settings


@cli.command()
@click.option(
    "--force-morning",
    is_flag=True,
    default=False,
    help="Render Morning Briefing even outside 06:00-10:30 local window.",
)
@click.pass_context
def morning(ctx, force_morning: bool):
    """Generate and send the morning briefing."""
    from app.main import run_morning_briefing
    # Preserve backward-compatible call shape for tests/monkeypatches while
    # still supporting explicit morning override.
    if force_morning:
        run_morning_briefing(ctx.obj["settings"], force_morning=True, auto_route_session=True)
        return
    run_morning_briefing(ctx.obj["settings"])


@cli.command("brief")
@click.pass_context
def brief(ctx):
    """Generate and send the correct session-aware briefing for current local time."""
    from app.main import run_session_brief
    run_session_brief(ctx.obj["settings"])


@cli.command("day-replay")
@click.option(
    "--date",
    "replay_date",
    default="today",
    show_default=True,
    help="Replay date: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--until",
    type=click.Choice(["now", "close", "full-day"], case_sensitive=False),
    default="now",
    show_default=True,
    help="Replay horizon cutoff.",
)
@click.option(
    "--profile",
    "profile_name",
    default=None,
    help="Optional profile override for replay context.",
)
@click.option(
    "--send-test",
    default="",
    help="Comma-separated test delivery channels: telegram,email.",
)
@click.option(
    "--force-all",
    is_flag=True,
    default=False,
    help="Generate all checkpoints even if not yet eligible.",
)
@click.option(
    "--respect-materiality/--ignore-materiality",
    default=True,
    help="Apply session materiality gating during replay.",
)
@click.option(
    "--include-breaking",
    is_flag=True,
    default=False,
    help="Allow replay sends for sessions that cross breaking thresholds.",
)
@click.option(
    "--persist-replay-snapshots",
    is_flag=True,
    default=False,
    help="Persist replay snapshots in isolated replay namespace.",
)
@click.option(
    "--healthcare-enabled",
    is_flag=True,
    default=False,
    help="Force-enable healthcare vertical for replay QA.",
)
@click.option(
    "--vertical",
    default="",
    help="Optional vertical override (example: healthcare).",
)
@click.option(
    "--max-sessions",
    type=int,
    default=8,
    show_default=True,
    help="Safety cap for number of replayed sessions.",
)
@click.pass_context
def day_replay(
    ctx,
    replay_date: str,
    until: str,
    profile_name: str | None,
    send_test: str,
    force_all: bool,
    respect_materiality: bool,
    include_breaking: bool,
    persist_replay_snapshots: bool,
    healthcare_enabled: bool,
    vertical: str,
    max_sessions: int,
):
    """Manual day/session replay tester (dry-run by default)."""
    from app.briefing.day_replay import run_day_replay

    run_day_replay(
        ctx.obj["settings"],
        replay_date=replay_date,
        until=until,
        profile_override=profile_name,
        send_test=send_test,
        force_all=force_all,
        respect_materiality=respect_materiality,
        include_breaking=include_breaking,
        persist_replay_snapshots=persist_replay_snapshots,
        healthcare_enabled=healthcare_enabled,
        vertical=vertical,
        max_sessions=max_sessions,
        show_output=bool(ctx.obj["settings"].show_output),
        email_only=ctx.obj["settings"].normalized_delivery_channel == "email",
        telegram_only=ctx.obj["settings"].normalized_delivery_channel == "telegram",
    )


@cli.command()
@click.pass_context
def intraday(ctx):
    """Run a single intraday update cycle."""
    from app.main import run_intraday_update
    run_intraday_update(ctx.obj["settings"])


@cli.command()
@click.pass_context
def midday(ctx):
    """Run a Europe Midday Check briefing."""
    from app.main import run_morning_briefing
    run_morning_briefing(
        ctx.obj["settings"],
        auto_route_session=False,
        session_override="europe_midday",
    )


@cli.command()
@click.pass_context
def preopen(ctx):
    """Run a US Pre-Open Setup briefing."""
    from app.main import run_morning_briefing
    run_morning_briefing(
        ctx.obj["settings"],
        auto_route_session=False,
        session_override="us_pre_open",
    )


@cli.command()
@click.pass_context
def close(ctx):
    """Run an Into Close Update briefing."""
    from app.main import run_morning_briefing
    run_morning_briefing(
        ctx.obj["settings"],
        auto_route_session=False,
        session_override="into_close",
    )


@cli.command()
@click.pass_context
def breaking(ctx):
    """Run a single breaking alert check."""
    from app.main import run_breaking_check
    run_breaking_check(ctx.obj["settings"])


@cli.command()
@click.pass_context
def scheduler(ctx):
    """Start the full scheduler (morning + intraday + breaking)."""
    from app.scheduler import start_scheduler
    start_scheduler()


@cli.command("web")
@click.option(
    "--host",
    default=None,
    help="Bind host for Briefly control-center server (default from WEB_HOST).",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Bind port for Briefly control-center server (default from WEB_PORT).",
)
@click.pass_context
def web_panel(ctx, host: str | None, port: int | None):
    """Run the local FastAPI + HTMX Briefly control center."""
    import uvicorn

    from app.web.app import create_web_app

    init_db()
    settings = ctx.obj["settings"]
    if host:
        settings.web_host = host
    if port:
        settings.web_port = port

    app = create_web_app(settings)
    click.echo(
        f"Starting Briefly control center at "
        f"http://{settings.web_host}:{settings.web_port}/ui/settings"
    )
    uvicorn.run(
        app,
        host=settings.web_host,
        port=int(settings.web_port),
        log_level=str(settings.log_level).lower(),
    )


@cli.command("import-holdings")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Holdings file (.yaml/.yml/.csv). Defaults to configs/holdings.yaml.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile name to associate with imported holdings.",
)
@click.pass_context
def import_holdings(ctx, file_path: Path | None, profile_name: str):
    """Import holdings from YAML/CSV and persist them for scoring."""
    from app.portfolio.importer import load_holdings_file
    from app.portfolio.service import replace_holdings_snapshot

    init_db()

    settings = ctx.obj["settings"]
    import_path = file_path or (Path(settings.configs_dir) / "holdings.yaml")
    if not import_path.exists():
        raise click.ClickException(
            f"Holdings file not found: {import_path}. "
            "Provide --file or create configs/holdings.yaml."
        )

    snapshot = load_holdings_file(import_path, default_profile=profile_name)
    imported = replace_holdings_snapshot(
        profile_name=snapshot.profile_name or profile_name,
        holdings=snapshot.holdings,
        as_of_date=snapshot.as_of_date,
    )
    click.echo(
        f"Imported {imported} holdings for profile "
        f"'{snapshot.profile_name or profile_name}' from {import_path}"
    )


@cli.command("prefs-show")
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile name to inspect.",
)
def prefs_show(profile_name: str):
    """Show persisted Phase 4.2 preference overrides for a profile."""
    import json

    from app.personalization.preferences_service import get_preferences

    init_db()
    prefs = get_preferences(profile_name)
    click.echo(f"Preferences for profile '{profile_name}':")
    if not prefs:
        click.echo("  (none)")
        return
    for key in sorted(prefs.keys()):
        click.echo(f"  {key} = {json.dumps(prefs[key], ensure_ascii=False)}")


@cli.command("prefs-set")
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile name to update.",
)
@click.option("--key", "pref_key", required=True, help="Preference key (e.g. delivery.morning_channels).")
@click.option(
    "--value",
    "raw_value",
    required=True,
    help="Preference value (JSON or raw string). Example: '[\"email\"]'",
)
def prefs_set(profile_name: str, pref_key: str, raw_value: str):
    """Set one persisted preference override."""
    import json

    from app.personalization.preferences_service import parse_cli_value, set_preference

    init_db()
    parsed = parse_cli_value(raw_value)
    normalized = set_preference(profile_name, pref_key, parsed)
    click.echo(
        f"Set {pref_key} for profile '{profile_name}' to "
        f"{json.dumps(normalized, ensure_ascii=False)}"
    )


@cli.command("prefs-unset")
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile name to update.",
)
@click.option("--key", "pref_key", required=True, help="Preference key to remove.")
def prefs_unset(profile_name: str, pref_key: str):
    """Unset one persisted preference override."""
    from app.personalization.preferences_service import unset_preference

    init_db()
    removed = unset_preference(profile_name, pref_key)
    if removed:
        click.echo(f"Unset {pref_key} for profile '{profile_name}'")
    else:
        click.echo(f"No active override for {pref_key} on profile '{profile_name}'")


@cli.command("prefs-reset")
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile name to reset.",
)
def prefs_reset(profile_name: str):
    """Clear all persisted preference overrides for a profile."""
    from app.personalization.preferences_service import clear_preferences

    init_db()
    count = clear_preferences(profile_name)
    click.echo(f"Cleared {count} preference override(s) for profile '{profile_name}'")


@cli.group("validation")
def validation():
    """Phase 5.7A portfolio validation and simulation harness."""


@validation.command("presets")
def validation_presets():
    """List available canonical validation presets."""
    from app.validation.presets import list_preset_summaries

    for item in list_preset_summaries():
        click.echo(f"- {item['name']}: {item['description']}")


@validation.command("run")
@click.option("--preset", "preset_name", required=True, help="Preset name to apply and validate.")
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile to validate.")
@click.option("--refresh-risk/--no-refresh-risk", default=False, help="Refresh risk cache before validation summary.")
@click.option("--json-output/--text-output", default=False, help="Render report as JSON instead of plain text.")
@click.pass_context
def validation_run(ctx, preset_name: str, profile_name: str, refresh_risk: bool, json_output: bool):
    """Apply one preset and run full validation checks."""
    from app.validation.report import report_to_json, report_to_text
    from app.validation.runner import run_preset_validation

    init_db()
    settings = ctx.obj["settings"]
    report = run_preset_validation(
        settings=settings,
        preset_name=preset_name,
        profile_name=profile_name,
        include_risk_refresh=refresh_risk,
    )
    click.echo(report_to_json(report) if json_output else report_to_text(report))
    if report.get("status") != "pass":
        raise click.ClickException("Validation run failed. See failed checks above.")


@validation.command("sweep")
@click.option("--preset", "preset_name", required=True, help="Base preset to sweep.")
@click.option(
    "--dimension",
    required=True,
    type=click.Choice(["top_holding_pct", "cash_weight_pct", "equity_expected_return_pct"]),
    help="Sweep dimension.",
)
@click.option(
    "--values",
    required=True,
    help="Comma-separated numeric values, e.g. 10,15,20,25",
)
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile to use.")
@click.option("--json-output/--text-output", default=False, help="Render report as JSON instead of compact text.")
@click.pass_context
def validation_sweep(
    ctx,
    preset_name: str,
    dimension: str,
    values: str,
    profile_name: str,
    json_output: bool,
):
    """Run deterministic parameter sweeps and monotonic checks."""
    from app.validation.sweeps import run_parameter_sweep

    init_db()
    try:
        parsed_values = [float(item.strip()) for item in str(values).split(",") if item.strip()]
    except ValueError as exc:
        raise click.ClickException(f"Invalid numeric values: {values}") from exc
    if not parsed_values:
        raise click.ClickException("Provide at least one numeric value for --values.")

    report = run_parameter_sweep(
        settings=ctx.obj["settings"],
        profile_name=profile_name,
        preset_name=preset_name,
        dimension=dimension,
        values=parsed_values,
    )
    if json_output:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"Sweep {preset_name} · {dimension} · status={report['status']} · "
            f"monotonic={report['monotonic_check'].get('passed')}"
        )
        for case in report["cases"]:
            click.echo(
                f"  value={case['value']}: largest={case['largest_position_pct']:.2f} "
                f"cash={case['cash_liquidity_actual_pct']:.2f} "
                f"exp_ret={case['expected_return_pct']:.2f}"
            )
    if report.get("status") != "pass":
        raise click.ClickException("Sweep monotonicity check failed.")


@validation.command("fuzz")
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile to use.")
@click.option("--cases", default=25, show_default=True, type=int, help="Number of randomized fuzz cases.")
@click.option("--seed", default=42, show_default=True, type=int, help="Random seed for reproducibility.")
@click.option("--json-output/--text-output", default=False, help="Render report as JSON instead of compact text.")
@click.pass_context
def validation_fuzz(ctx, profile_name: str, cases: int, seed: int, json_output: bool):
    """Run randomized fuzz validation across constrained portfolio inputs."""
    from app.validation.fuzz import run_fuzz_validation

    init_db()
    report = run_fuzz_validation(
        settings=ctx.obj["settings"],
        profile_name=profile_name,
        cases=cases,
        seed=seed,
    )
    if json_output:
        click.echo(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        summary = report["summary"]
        click.echo(
            f"Fuzz validation · status={report['status']} · cases={summary['total_cases']} · "
            f"passed={summary['passed_cases']} · failed={summary['failed_cases']} "
            f"({summary['pass_rate_pct']}%)"
        )
    if report.get("status") != "pass":
        raise click.ClickException("Fuzz validation found failing cases.")


@cli.group("simulation")
def simulation():
    """Phase 5.8 simulation lab commands."""


@simulation.command("run")
@click.option("--profile", "profile_name", default="default_user", show_default=True)
@click.option("--mode", default="portfolio", show_default=True, type=click.Choice(["portfolio", "stock"]))
@click.option("--methods", default="monte_carlo,historical", show_default=True, help="Comma-separated methods.")
@click.option("--frequency", default="monthly", show_default=True, type=click.Choice(["daily", "weekly", "monthly"]))
@click.option("--horizon-periods", default=60, show_default=True, type=int)
@click.option("--simulation-count", default=2500, show_default=True, type=int)
@click.option("--assumption-source", default="historical", show_default=True, type=click.Choice(["historical", "cma", "manual"]))
@click.option("--benchmark", "benchmark_symbol", default="ACWI", show_default=True)
@click.option("--start-value", default=100.0, show_default=True, type=float)
@click.option("--json-output/--text-output", default=False)
@click.pass_context
def simulation_run(
    ctx,
    profile_name: str,
    mode: str,
    methods: str,
    frequency: str,
    horizon_periods: int,
    simulation_count: int,
    assumption_source: str,
    benchmark_symbol: str,
    start_value: float,
    json_output: bool,
):
    """Run a simulation using current profile holdings."""
    from app.simulation.service import parse_simulation_config, run_simulation
    from app.web.control_plane_service import build_profile_state

    init_db()
    settings = ctx.obj["settings"]
    state = build_profile_state(settings, profile_name)
    payload = {
        "mode": mode,
        "methods": [item.strip() for item in methods.split(",") if item.strip()],
        "frequency": frequency,
        "horizon_periods": horizon_periods,
        "simulation_count": simulation_count,
        "assumption_source": assumption_source,
        "benchmark_symbol": benchmark_symbol,
        "start_value": start_value,
    }
    config = parse_simulation_config(
        payload=payload,
        fallback_holdings=state.get("holdings", []),
        fallback_benchmark_symbol=str(state.get("benchmark", {}).get("base_symbol") or "ACWI"),
    )
    result = run_simulation(
        profile_name=profile_name,
        settings=settings,
        config=config,
        persist=True,
    )
    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        summary = result.get("summary", {})
        click.echo(f"Simulation completed for profile '{profile_name}'")
        click.echo(f"  Median terminal: {summary.get('median_terminal_value')}")
        click.echo(f"  P5/P95: {summary.get('percentile_5_terminal_value')} / {summary.get('percentile_95_terminal_value')}")
        click.echo(f"  Prob loss: {summary.get('probability_of_loss_pct')}%")
        click.echo(f"  VaR95/CVaR95: {summary.get('var_95_pct')}% / {summary.get('cvar_95_pct')}%")


@simulation.command("runs")
@click.option("--profile", "profile_name", default="default_user", show_default=True)
@click.option("--limit", default=20, show_default=True, type=int)
def simulation_runs(profile_name: str, limit: int):
    """List recent simulation runs."""
    from app.simulation.repository import list_simulation_runs

    init_db()
    rows = list_simulation_runs(profile_name, limit=limit)
    if not rows:
        click.echo("(no simulation runs)")
        return
    for row in rows:
        click.echo(
            f"- #{row['run_id']} {','.join(row['methods'])} {row['frequency']} "
            f"h={row['horizon_periods']} sims={row['simulation_count']} {row['status']} {row['created_at']}"
        )


@cli.command("init-db")
@click.pass_context
def init_database(ctx):
    """Initialise the database (create tables)."""
    init_db()
    click.echo("Database initialised.")


@cli.command()
@click.pass_context
def status(ctx):
    """Show current configuration status."""
    settings = ctx.obj["settings"]
    click.echo("Briefly status")
    click.echo(f"  Timezone:    {settings.timezone}")
    click.echo(f"  Dry run:     {settings.dry_run}")
    click.echo(f"  Delivery:    {settings.normalized_delivery_channel}")
    llm_mode = "shadow" if settings.llm_render_shadow_mode else "live"
    llm_enabled = "enabled" if settings.enable_llm_email_render else "disabled"
    click.echo(f"  LLM email:   {llm_enabled} ({llm_mode})")
    click.echo(f"  Database:    {settings.database_url}")
    click.echo(f"  Finnhub:     {'configured' if settings.finnhub_configured else 'not set'}")
    click.echo(f"  FRED:        {'configured' if settings.fred_configured else 'not set'}")
    click.echo(f"  NewsAPI:     {'configured' if settings.newsapi_configured else 'not set'}")
    click.echo(f"  Telegram:    {'configured' if settings.telegram_configured else 'not set'}")
    click.echo(f"  Email:       {'configured' if settings.email_configured else 'not set'}")


@cli.command()
@click.pass_context
def preflight(ctx):
    """Run a Phase 4 delivery preflight check (render + channel readiness)."""
    settings = ctx.obj["settings"]
    llm_mode = "shadow" if settings.llm_render_shadow_mode else "live"
    llm_enabled = settings.enable_llm_email_render

    checks: list[tuple[str, bool, str]] = [
        ("Telegram channel", settings.telegram_configured or settings.dry_run, "configured or dry-run"),
        ("Email channel", settings.email_configured or settings.dry_run, "configured or dry-run"),
        ("Charts enabled", settings.enable_charts, "recommended for rich morning email"),
        (
            "OpenAI key",
            (not llm_enabled) or bool(settings.openai_api_key),
            "required only when ENABLE_LLM_EMAIL_RENDER=true",
        ),
        (
            "LLM API base URL",
            settings.llm_api_base_url.startswith("http"),
            settings.llm_api_base_url,
        ),
    ]

    click.echo("Briefly preflight")
    click.echo(f"  Delivery channel: {settings.normalized_delivery_channel}")
    click.echo(f"  Dry run:          {settings.dry_run}")
    click.echo(f"  LLM render:       {'enabled' if llm_enabled else 'disabled'} ({llm_mode})")
    click.echo(f"  LLM model:        {settings.llm_email_model}")
    click.echo(f"  LLM body cap:     {settings.llm_email_max_chars} chars")
    click.echo(f"  Min sources:      {settings.llm_email_min_source_urls}")
    click.echo("")

    for label, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        click.echo(f"  [{status}] {label}: {detail}")

    warnings: list[str] = []
    sender_addr = (settings.email_user or "").strip().lower()
    recipients = {
        addr.strip().lower()
        for addr in (settings.email_to or "").replace(";", ",").split(",")
        if addr.strip()
    }
    if llm_enabled and not settings.llm_render_shadow_mode and not settings.dry_run:
        warnings.append("LLM live mode is enabled. Use shadow mode first for 3-5 inspected runs.")
    if settings.normalized_delivery_channel == "telegram" and llm_enabled:
        warnings.append("LLM render is email-first; telegram-only runs will not exercise LLM output.")
    if settings.normalized_delivery_channel == "email" and not settings.email_configured and not settings.dry_run:
        warnings.append("Email-only live delivery requested but email credentials are not configured.")
    if sender_addr and sender_addr in recipients:
        warnings.append(
            "Gmail may show Sent + Inbox copies in one thread; this is not necessarily a duplicate send."
        )
        warnings.append(
            "Recommendation: use a dedicated sender account (for example briefly.bot@gmail.com)."
        )

    if warnings:
        click.echo("")
        click.echo("  warnings:")
        for warning in warnings:
            click.echo(f"  - {warning}")

    has_failures = any(not ok for _, ok, _ in checks)
    click.echo("")
    click.echo(f"  result: {'FAIL' if has_failures else 'PASS'}")


@cli.command()
@click.argument("symbol")
@click.pass_context
def quote(ctx, symbol):
    """Fetch a quote for a single symbol (diagnostic)."""
    from app.data_sources.market_data import MarketDataService
    settings = ctx.obj["settings"]
    svc = MarketDataService(settings)
    q = svc.get_quote(symbol.upper())
    if q:
        sign = "+" if q.change >= 0 else ""
        click.echo(f"{q.symbol}: ${q.current_price:.2f} {sign}{q.change:.2f} ({sign}{q.change_percent:.2f}%)")
    else:
        click.echo(f"No data for {symbol.upper()}")


@cli.command()
@click.argument("query", default="")
@click.pass_context
def news(ctx, query):
    """Fetch latest market news (diagnostic)."""
    from app.data_sources.news_data import NewsDataService
    settings = ctx.obj["settings"]
    svc = NewsDataService(settings)
    events = svc.fetch_market_news()
    click.echo(f"Fetched {len(events)} events:")
    for evt in events[:10]:
        tickers = f" [{','.join(evt.tickers[:3])}]" if evt.tickers else ""
        click.echo(f"  [{evt.source}] {evt.title[:100]}{tickers}")


@cli.command("portfolio-snapshot")
@click.option("--profile", "profile_name", default="default_user", help="Profile to render.")
@click.option("--lookback-days", type=int, default=252, help="Lookback window in trading days.")
@click.option("--force", is_flag=True, default=False, help="Bypass cache and recompute.")
@click.option("--plain", is_flag=True, default=False, help="Plain-text output (no HTML tags).")
@click.option("--json", "json_out", is_flag=True, default=False, help="Emit raw advanced_metrics block as JSON.")
@click.pass_context
def portfolio_snapshot(ctx, profile_name: str, lookback_days: int, force: bool, plain: bool, json_out: bool):
    """Render the Phase 5.9 Portfolio Performance Snapshot for a profile.

    Pulls live risk analytics and the 22-metric advanced workbench, then prints a
    Telegram/email-shaped summary block. Use --plain for terminal viewing, --json
    for piping into other tools.
    """
    init_db()
    from app.briefing.portfolio_performance import (
        format_portfolio_performance_plain,
        format_portfolio_performance_snapshot,
    )
    from app.portfolio.holdings_store import load_holdings_for_profile
    from app.benchmark.service import load_benchmark_config
    from app.policy.service import load_policy
    from app.risk.service import compute_risk_analytics

    holdings = load_holdings_for_profile(profile_name) or []
    benchmark = load_benchmark_config(profile_name)
    policy = load_policy(profile_name)

    block = compute_risk_analytics(
        profile_name=profile_name,
        holdings=[h.model_dump() if hasattr(h, "model_dump") else dict(h) for h in holdings],
        benchmark_config=benchmark.model_dump() if benchmark and hasattr(benchmark, "model_dump") else (dict(benchmark) if benchmark else None),
        policy=policy.model_dump() if policy and hasattr(policy, "model_dump") else (dict(policy) if policy else None),
        lookback_days=lookback_days,
        force_refresh=force,
    )

    if json_out:
        click.echo(json.dumps(block.get("advanced_metrics", {}), indent=2, default=str))
        return

    if plain:
        text = format_portfolio_performance_plain(block)
    else:
        text = format_portfolio_performance_snapshot(block)

    if not text:
        click.echo("Portfolio snapshot unavailable. Configure a benchmark and weighted holdings, then retry.")
        ctx.exit(1)
    click.echo(text)


@cli.command("session-send")
@click.option(
    "--session",
    "session_key",
    required=True,
    type=click.Choice(
        ["morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"],
        case_sensitive=False,
    ),
    help="Session to send.",
)
@click.option(
    "--send",
    "channels",
    default="",
    help="Comma-separated channels to use: telegram,email. Overrides global --email-only/--telegram-only.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Bypass idempotency: resend even if already sent today.",
)
@click.pass_context
def session_send(ctx, session_key: str, channels: str, force: bool):
    """Manually send a specific session briefing, bypassing clock routing.

    Uses idempotency unless --force is passed. Channel flags are inherited
    from the global --email-only/--telegram-only options, or override them
    with --send telegram,email.

    Examples:
      python -m app.cli session-send --session europe_midday --send telegram,email
      python -m app.cli session-send --session us_pre_open --force
    """
    from app.main import run_morning_briefing

    settings = ctx.obj["settings"]
    if channels:
        channel_list = [c.strip().lower() for c in channels.split(",") if c.strip()]
        if "email" in channel_list and "telegram" not in channel_list:
            settings.delivery_channel = "email"
        elif "telegram" in channel_list and "email" not in channel_list:
            settings.delivery_channel = "telegram"
        elif channel_list:
            settings.delivery_channel = "all"
    run_morning_briefing(
        settings,
        auto_route_session=False,
        session_override=session_key,
        respect_cadence=False,
        force_send=force,
        command_source="cli:session-send",
    )


@cli.command("catch-up")
@click.option(
    "--date",
    "target_date",
    default="today",
    show_default=True,
    help="Date to catch up: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--send",
    "channels",
    default="",
    help="Comma-separated channels: telegram,email. Overrides global --email-only/--telegram-only.",
)
@click.option(
    "--force-all",
    is_flag=True,
    default=False,
    help="Resend even sessions already successfully sent for the target date.",
)
@click.option(
    "--ignore-materiality",
    is_flag=True,
    default=False,
    help="Skip materiality gating; send all eligible sessions regardless of score.",
)
@click.option(
    "--active-mode",
    is_flag=True,
    default=False,
    help="Treat all six sessions as allowed regardless of profile session_mode.",
)
@click.pass_context
def catch_up(ctx, target_date: str, channels: str, force_all: bool, ignore_materiality: bool, active_mode: bool):
    """Send all sessions that have started for the target date but not yet been delivered.

    Accepts today, yesterday, or a specific YYYY-MM-DD date. For past dates
    all six session windows are treated as elapsed so everything is eligible.

    Examples:
      python -m app.cli catch-up --send telegram,email
      python -m app.cli catch-up --date yesterday --send telegram,email
      python -m app.cli catch-up --active-mode --ignore-materiality
      python -m app.cli catch-up --force-all --send telegram
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.main import run_catch_up
    from app.settings import get_settings as _get_settings

    settings = ctx.obj["settings"]
    if channels:
        channel_list = [c.strip().lower() for c in channels.split(",") if c.strip()]
        if "email" in channel_list and "telegram" not in channel_list:
            settings.delivery_channel = "email"
        elif "telegram" in channel_list and "email" not in channel_list:
            settings.delivery_channel = "telegram"
        elif channel_list:
            settings.delivery_channel = "all"

    # Detect past dates early so we can warn before any sends happen.
    _tz_name = settings.timezone or "Europe/Madrid"
    _local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(_tz_name))
    _s = (target_date or "today").strip().lower()
    if _s not in ("today",):
        _is_past = True
        if _s == "yesterday":
            from datetime import timedelta
            _target_d = (_local_now - timedelta(days=1)).date()
        else:
            from datetime import date as _date
            try:
                _target_d = _date.fromisoformat(_s)
                _is_past = _target_d < _local_now.date()
            except ValueError:
                _is_past = False
                _target_d = _local_now.date()
        if _is_past and _target_d < _local_now.date():
            click.echo(
                "\nNOTE: targeting a past date. Messages will carry a HISTORICAL BACKFILL - NOT LIVE"
                " banner so recipients can see they were generated after the fact using"
                " the latest available provider data, not the original session-time snapshot.\n"
            )

    try:
        summary = run_catch_up(
            settings,
            target_date_str=target_date,
            force_all=force_all,
            ignore_materiality=ignore_materiality,
            active_mode=active_mode,
            command_source="cli:catch-up",
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("\nCatch-up summary")
    click.echo(f"  {'Session':<22} {'Action':<14} {'Reason'}")
    click.echo(f"  {'-'*22} {'-'*14} {'-'*30}")
    for row in summary:
        click.echo(f"  {row['session']:<22} {row['action']:<14} {row.get('reason', '')}")
    sent_count = sum(1 for r in summary if r["action"] == "sent")
    skipped_count = sum(1 for r in summary if r["action"] in {"skipped", "already_sent"})
    click.echo(f"\n  Sent: {sent_count}   Skipped/already sent: {skipped_count}")


@cli.command("backfill")
@click.option(
    "--date",
    "target_date",
    required=True,
    help="Past date to backfill: yesterday | YYYY-MM-DD. Future dates are rejected.",
)
@click.option(
    "--send",
    "channels",
    default="",
    help="Comma-separated channels: telegram,email. Overrides global --email-only/--telegram-only.",
)
@click.option(
    "--force-all",
    is_flag=True,
    default=False,
    help="Resend even sessions already recorded as sent for the target date.",
)
@click.option(
    "--ignore-materiality",
    is_flag=True,
    default=False,
    help="Skip materiality gating; send all eligible sessions regardless of score.",
)
@click.option(
    "--active-mode",
    is_flag=True,
    default=False,
    help="Treat all six sessions as allowed regardless of profile session_mode.",
)
@click.option(
    "--allow-today",
    is_flag=True,
    default=False,
    help="Allow backfilling today's date (for testing). Messages are still labelled as backfill.",
)
@click.pass_context
def backfill(ctx, target_date: str, channels: str, force_all: bool, ignore_materiality: bool, active_mode: bool, allow_today: bool):
    """Backfill sessions for a past date, clearly labelling every message as historical.

    Every Telegram message and email will carry a prominent HISTORICAL BACKFILL - NOT LIVE
    banner so recipients can see the content was generated after the fact using the latest
    available provider data, not the original session-time snapshot.

    Use catch-up for today's missed sessions (no banner). Use backfill for any prior date.

    Examples:
      python -m app.cli backfill --date yesterday --send telegram,email
      python -m app.cli backfill --date 2026-05-05 --active-mode --ignore-materiality
    """
    from datetime import datetime, date as _date, timedelta, timezone
    from zoneinfo import ZoneInfo

    from app.main import run_catch_up

    settings = ctx.obj["settings"]

    _tz_name = settings.timezone or "Europe/Madrid"
    _local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(_tz_name))
    _today = _local_now.date()

    _s = (target_date or "").strip().lower()
    if _s == "yesterday":
        resolved_date = (_local_now - timedelta(days=1)).date()
    else:
        try:
            resolved_date = _date.fromisoformat(_s)
        except ValueError as exc:
            raise click.ClickException(f"Invalid date: {target_date!r}. Use yesterday or YYYY-MM-DD.") from exc

    if resolved_date > _today:
        raise click.ClickException(
            f"Future date {resolved_date.isoformat()} is not allowed. "
            "Backfill only works for past dates."
        )
    if resolved_date == _today and not allow_today:
        raise click.ClickException(
            f"Date {resolved_date.isoformat()} is today. Use catch-up for today's missed sessions, "
            "or pass --allow-today if you intentionally want a backfill-labelled send."
        )

    if channels:
        channel_list = [c.strip().lower() for c in channels.split(",") if c.strip()]
        if "email" in channel_list and "telegram" not in channel_list:
            settings.delivery_channel = "email"
        elif "telegram" in channel_list and "email" not in channel_list:
            settings.delivery_channel = "telegram"
        elif channel_list:
            settings.delivery_channel = "all"

    click.echo(
        f"\nBackfilling {resolved_date.isoformat()}. Every message will carry a "
        "HISTORICAL BACKFILL - NOT LIVE banner.\n"
    )

    try:
        summary = run_catch_up(
            settings,
            target_date_str=resolved_date.isoformat(),
            force_all=force_all,
            ignore_materiality=ignore_materiality,
            active_mode=active_mode,
            command_source="cli:backfill",
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("\nBackfill summary")
    click.echo(f"  {'Session':<22} {'Action':<14} {'Reason'}")
    click.echo(f"  {'-'*22} {'-'*14} {'-'*30}")
    for row in summary:
        click.echo(f"  {row['session']:<22} {row['action']:<14} {row.get('reason', '')}")
    sent_count = sum(1 for r in summary if r["action"] == "sent")
    skipped_count = sum(1 for r in summary if r["action"] in {"skipped", "already_sent"})
    click.echo(f"\n  Sent: {sent_count}   Skipped/already sent: {skipped_count}")


@cli.command("schedule-status")
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile to inspect.",
)
@click.pass_context
def schedule_status(ctx, profile_name: str):
    """Show scheduler state, current session window, and today's send history.

    Includes:
      - Scheduler lock status (running or not)
      - Current and next session windows
      - Profile cadence preferences
      - Per-channel send state for each session today
    """
    import fcntl as _fcntl_mod
    from pathlib import Path as _Path
    from zoneinfo import ZoneInfo

    from app.briefing.session_routing import next_session_window, resolve_session_window
    from app.db.models import ProviderHealthLog, SessionSendState
    from app.db.session import get_session, init_db
    from app.main import _allowed_sessions_for_mode, _allowed_sessions_for_profile_day
    from app.personalization.user_profile import load_user_profile

    import datetime as _dt

    init_db()
    settings = ctx.obj["settings"]
    profile = load_user_profile(settings)
    tz = ZoneInfo(profile.timezone or settings.timezone)
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    local_now = now_utc.astimezone(tz)
    today = local_now.date()

    current_window = resolve_session_window(
        now=now_utc,
        timezone_name=profile.timezone or settings.timezone,
        session_template=getattr(profile, "session_template", None),
    )
    next_window = next_session_window(
        now=now_utc,
        timezone_name=profile.timezone or settings.timezone,
        session_template=getattr(profile, "session_template", None),
    )

    # Scheduler lock status
    lock_path = _Path(settings.data_dir) / "state" / "scheduler.lock"
    scheduler_running = False
    lock_status_msg = "not held"
    try:
        if lock_path.exists():
            handle = lock_path.open("a+")
            try:
                _fcntl_mod.flock(handle.fileno(), _fcntl_mod.LOCK_EX | _fcntl_mod.LOCK_NB)
                _fcntl_mod.flock(handle.fileno(), _fcntl_mod.LOCK_UN)
                lock_status_msg = "not held (file exists, scheduler not running)"
            except OSError:
                scheduler_running = True
                lock_status_msg = "held (scheduler is running)"
            finally:
                handle.close()
        else:
            lock_status_msg = "lock file not found (scheduler has not run yet)"
    except Exception as exc:
        lock_status_msg = f"unable to probe ({exc})"

    from app.briefing.session_metadata import ASIA_COVERAGE_NOTE, get_session_meta, sessions_for_profile
    from app.briefing.session_templates import get_session_template_for_profile
    template_name, profile_sessions = get_session_template_for_profile(profile)

    current_meta = get_session_meta(current_window.key)
    next_meta = get_session_meta(next_window.key)

    click.echo("Briefly schedule status")
    click.echo(f"  Timezone:         {profile.timezone or settings.timezone}")
    click.echo(f"  Market region:    {getattr(profile, 'market_region', '') or 'EMEA'}")
    click.echo(f"  Sub-region:       {getattr(profile, 'sub_region', '') or 'Eurozone'}")
    click.echo(f"  Market focus:     {getattr(profile, 'market_focus_region', '') or '(inferred from location)'}")
    click.echo(f"  Template:         {template_name}")
    click.echo(f"  Template override:{(' ' + profile.session_template_override) if getattr(profile, 'session_template_override', '') else ' (none)'}")
    click.echo(f"  Local time:       {local_now.strftime('%Y-%m-%d %H:%M')}")
    click.echo(f"  Current session:  {current_window.key}")
    click.echo(f"    Label:          {current_meta.label if current_meta else current_window.title}")
    click.echo(f"    Focus:          {current_meta.focus if current_meta else '—'}")
    click.echo(f"    Window:         {current_meta.window_str if current_meta else '—'}")
    click.echo(f"  Next session:     {next_window.key}")
    click.echo(f"    Label:          {next_meta.label if next_meta else next_window.title}")
    click.echo(f"    Focus:          {next_meta.focus if next_meta else '—'}")
    click.echo(f"    Window:         {next_meta.window_str if next_meta else '—'}")
    click.echo(f"  Scheduler lock:   {lock_status_msg}")
    if scheduler_running:
        click.echo("  WARNING: Do not also run CLI brief/morning manually or start a second")
        click.echo("           service instance — duplicate sends can occur if two processes")
        click.echo("           race the idempotency window. Use --force only for recovery.")
    click.echo("")
    click.echo("  Provider health (latest)")
    provider_names = ["fmp_news", "mediastack", "gdelt", "finnhub", "fred", "newsapi"]
    with get_session() as db_sess:
        for provider in provider_names:
            row = (
                db_sess.query(ProviderHealthLog)
                .filter(ProviderHealthLog.provider == provider)
                .order_by(ProviderHealthLog.timestamp.desc())
                .first()
            )
            if row is None:
                click.echo(f"    {provider}: no recent data")
                continue
            status = "ok" if row.success else "degraded"
            detail = (row.error_message or "").lower()
            if "unauthorized" in detail or row.status_code == 401:
                status = "unauthorized / disabled"
            elif "rate_limited" in detail or row.status_code == 429:
                status = "rate-limited / budget exhausted"
            elif "circuit breaker open" in detail:
                status = "circuit open"
            elif "timeout" in detail:
                status = "timeout"
            click.echo(f"    {provider}: {status}")

    click.echo("")
    click.echo("  Session schedule (all times local)")
    click.echo(f"  {'Key':<22} {'Label':<32} {'Window':<14} {'Focus'}")
    click.echo(f"  {'-'*22} {'-'*32} {'-'*14} {'-'*40}")
    for meta in profile_sessions:
        click.echo(f"  {meta.key:<22} {meta.label:<32} {meta.window_str:<14} {meta.focus}")
    click.echo("")
    click.echo(f"  Note: {ASIA_COVERAGE_NOTE}")
    click.echo("")
    click.echo("  Cadence preferences")
    allowed = _allowed_sessions_for_profile_day(
        mode=profile.session_mode,
        weekend_mode=profile.weekend_mode,
        weekday_idx=local_now.weekday(),
    )
    always_list = profile.always_send_sessions or []
    click.echo(f"    session_mode:             {profile.session_mode}")
    click.echo(f"    allowed_sessions:         {', '.join(sorted(allowed)) if allowed else '(none)'}")
    click.echo(f"    always_send_sessions:     {', '.join(always_list) if always_list else '(none)'}")
    click.echo(f"    suppress_low_materiality: {profile.suppress_low_materiality}")
    click.echo(f"    weekend_mode:             {profile.weekend_mode}")
    click.echo(f"    sunday_news_materiality:  {profile.sunday_news_materiality}")
    if local_now.weekday() >= 5:
        weekday_sessions = _allowed_sessions_for_mode("active")
        suppressed = sorted(weekday_sessions - set(allowed))
        click.echo(f"    weekend_weekday_suppressed: {', '.join(suppressed) if suppressed else '(none)'}")
    click.echo("")
    click.echo(f"  Today's send state ({today.isoformat()})")

    channels = ["telegram", "email"]

    with get_session() as db_sess:
        rows = (
            db_sess.query(SessionSendState)
            .filter(
                SessionSendState.profile_name == profile_name,
                SessionSendState.local_date == today,
                SessionSendState.replay_namespace == "",
            )
            .order_by(SessionSendState.session_key, SessionSendState.channel)
            .all()
        )
    state_map: dict[tuple[str, str], SessionSendState] = {
        (r.session_key, r.channel): r for r in rows
    }

    for meta in profile_sessions:
        sk = meta.key
        for ch in channels:
            row = state_map.get((sk, ch))
            if row is None:
                state_str = "(not attempted)"
            elif row.success:
                if row.sent_at:
                    sent_utc = row.sent_at.replace(tzinfo=_dt.timezone.utc)
                    sent_at = sent_utc.astimezone(tz).strftime("%H:%M")
                else:
                    sent_at = "?"
                state_str = f"sent at {sent_at}"
            elif row.in_progress:
                state_str = "in progress"
            else:
                state_str = f"failed: {(row.error_message or '')[:60]}"
            click.echo(f"    {sk:<22} {ch:<10} {state_str}")

    if local_now.weekday() >= 5:
        for wk_key, wk_label in (
            ("saturday_weekend_briefing", "Weekend Briefing"),
            ("sunday_weekend_watch", "Sunday Weekend Watch"),
        ):
            for ch in channels:
                row = state_map.get((wk_key, ch))
                if row is None:
                    state_str = "(not attempted)"
                elif row.success:
                    if row.sent_at:
                        sent_utc = row.sent_at.replace(tzinfo=_dt.timezone.utc)
                        sent_at = sent_utc.astimezone(tz).strftime("%H:%M")
                    else:
                        sent_at = "?"
                    state_str = f"sent at {sent_at}"
                elif row.in_progress:
                    state_str = "in progress"
                else:
                    state_str = f"failed: {(row.error_message or '')[:60]}"
                click.echo(f"    {wk_key:<22} {ch:<10} {state_str}")


@cli.command("daily-summary")
@click.option(
    "--date",
    "target_date",
    default="today",
    show_default=True,
    help="Date to summarise: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile to inspect.",
)
@click.pass_context
def daily_summary(ctx, target_date: str, profile_name: str):
    """Print a daily summary of all six session send states.

    Shows each session's label, focus, and per-channel delivery status.
    No provider calls are made; reads from SQLite only.

    Example:
        python -m app.cli daily-summary
        python -m app.cli daily-summary --date yesterday
    """
    from app.db.session import init_db
    from app.main import run_daily_summary

    init_db()
    settings = ctx.obj["settings"]
    summary = run_daily_summary(settings, target_date_str=target_date, profile_name=profile_name)
    click.echo(summary)


@cli.command("session-audit")
@click.option("--date", "target_date", default="today", show_default=True, help="Date: today | yesterday | YYYY-MM-DD.")
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile name.")
@click.option(
    "--live-check",
    is_flag=True,
    default=False,
    help="Regenerate provider-backed diagnostics (may call market/news providers).",
)
@click.option(
    "--classifier-details",
    is_flag=True,
    default=False,
    help="With --live-check, print compact classifier examples (included/suppressed/rejected).",
)
@click.pass_context
def session_audit(ctx, target_date: str, profile_name: str, live_check: bool, classifier_details: bool):
    """Audit incremental session behavior, freshness basis, and section modes."""
    from app.main import run_session_audit
    report = run_session_audit(
        ctx.obj["settings"],
        target_date_str=target_date,
        profile_name=profile_name,
        live_check=live_check,
        classifier_details=classifier_details,
    )
    click.echo(report)


@cli.command("news-review")
@click.option("--date", "target_date", default="today", show_default=True, help="Date: today | yesterday | YYYY-MM-DD.")
@click.option("--limit", default=50, show_default=True, type=int, help="Max rows to show.")
@click.option("--dedupe", is_flag=True, default=False, help="Collapse repeated story/headline rows into one representative row.")
@click.option("--unlabelled-only", is_flag=True, default=False, help="Show only rows without manual labels.")
@click.pass_context
def news_review(ctx, target_date: str, limit: int, dedupe: bool, unlabelled_only: bool):
    """Print local news-classifier label rows for manual review."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.db.models import NewsClassifierLabel
    from app.db.session import get_session
    from app.ml.news_dataset import dedupe_label_rows

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)
    s = (target_date or "today").strip().lower()
    if s == "today":
        d = local_now.date()
    elif s == "yesterday":
        d = (local_now - timedelta(days=1)).date()
    else:
        from datetime import date as _date
        d = _date.fromisoformat(s)

    with get_session() as db:
        rows = list((
            db.query(NewsClassifierLabel)
            .filter(NewsClassifierLabel.local_date == d)
            .order_by(
                NewsClassifierLabel.included_in_briefing.desc(),
                NewsClassifierLabel.sent_as_breaking.desc(),
                NewsClassifierLabel.deterministic_score.desc(),
                NewsClassifierLabel.updated_at.desc(),
            )
            .limit(max(1, int(limit)))
            .all()
        ))
    if dedupe:
        rows = dedupe_label_rows(rows)
    if unlabelled_only:
        def _is_unlabelled(row) -> bool:
            return not any(
                [
                    bool(getattr(row, "manual_story_type", "")),
                    bool(getattr(row, "manual_suppression_reason", "")),
                    getattr(row, "manual_breaking_eligible", None) is not None,
                    bool(getattr(row, "manual_ticker_mismatch_risk", "")),
                    bool(getattr(row, "manual_stale_reprint_risk", "")),
                ]
            )
        rows = [r for r in rows if _is_unlabelled(r)]
    rows = rows[: max(1, int(limit))]

    click.echo(
        f"NEWS REVIEW | date={d.isoformat()} | rows={len(rows)} | dedupe={'on' if dedupe else 'off'} "
        f"| unlabelled_only={'on' if unlabelled_only else 'off'}"
    )
    if not rows:
        click.echo("No rows found. Run session-audit --live-check first.")
        return
    for row in rows:
        sessions_seen = int(getattr(row, "sessions_seen", 1) or 1)
        session_keys_agg = str(getattr(row, "session_keys_agg", "") or "")
        local_dates_agg = str(getattr(row, "local_dates_agg", "") or "")
        click.echo(
            f"[{row.id}] {row.headline[:110]} | det={row.deterministic_story_type or '-'} "
            f"fresh={row.deterministic_freshness_state or '-'} upd={row.deterministic_update_status or '-'} "
            f"suppress={row.deterministic_suppression_reason or '-'} break={row.deterministic_breaking_eligible} "
            f"manual={row.manual_story_type or '-'} label_source={row.label_source} "
            f"sessions_seen={sessions_seen}"
        )
        if dedupe and (session_keys_agg or local_dates_agg):
            click.echo(f"    sessions={session_keys_agg or '-'} dates={local_dates_agg or '-'}")


@cli.command("news-label-set")
@click.option("--id", "row_id", required=True, type=int, help="news_classifier_labels row id.")
@click.option("--story-type", default=None, help="Manual story type.")
@click.option("--suppression-reason", default=None, help="Manual suppression reason.")
@click.option("--breaking-eligible", default=None, type=click.Choice(["true", "false"], case_sensitive=False))
@click.option("--ticker-mismatch-risk", default=None, type=click.Choice(["low", "medium", "high"], case_sensitive=False))
@click.option("--stale-reprint-risk", default=None, type=click.Choice(["low", "medium", "high"], case_sensitive=False))
@click.option("--notes", default=None, help="Optional notes.")
@click.pass_context
def news_label_set(
    ctx,
    row_id: int,
    story_type: str | None,
    suppression_reason: str | None,
    breaking_eligible: str | None,
    ticker_mismatch_risk: str | None,
    stale_reprint_risk: str | None,
    notes: str | None,
):
    """Set manual label fields on one news-classifier row."""
    from app.db.models import NewsClassifierLabel
    from app.db.session import get_session
    from datetime import datetime, timezone

    init_db()
    with get_session() as db:
        row = db.query(NewsClassifierLabel).filter(NewsClassifierLabel.id == int(row_id)).first()
        if row is None:
            raise click.ClickException(f"Row id {row_id} not found.")
        changed = False
        if story_type is not None:
            row.manual_story_type = str(story_type).strip()
            changed = True
        if suppression_reason is not None:
            row.manual_suppression_reason = str(suppression_reason).strip()
            changed = True
        if breaking_eligible is not None:
            row.manual_breaking_eligible = str(breaking_eligible).strip().lower() == "true"
            changed = True
        if ticker_mismatch_risk is not None:
            row.manual_ticker_mismatch_risk = str(ticker_mismatch_risk).strip().lower()
            changed = True
        if stale_reprint_risk is not None:
            row.manual_stale_reprint_risk = str(stale_reprint_risk).strip().lower()
            changed = True
        if notes is not None:
            row.notes = str(notes)
            changed = True
        if not changed:
            raise click.ClickException("No fields provided to update.")
        row.label_source = "manual"
        row.updated_at = datetime.now(timezone.utc)
    click.echo(f"Updated news label row {row_id}.")


@cli.command("news-dataset-export")
@click.option("--from", "from_date", default=None, help="Start date YYYY-MM-DD.")
@click.option("--to", "to_date", default="today", show_default=True, help="End date: today|yesterday|YYYY-MM-DD.")
@click.option("--output", "output_path", required=True, help="Output CSV path.")
@click.option("--format", "output_format", default="csv", show_default=True, type=click.Choice(["csv"], case_sensitive=False))
@click.option("--dedupe-headlines", is_flag=True, default=False, help="Export one row per stable headline/story key.")
@click.pass_context
def news_dataset_export(
    ctx,
    from_date: str | None,
    to_date: str,
    output_path: str,
    output_format: str,
    dedupe_headlines: bool,
):
    """Export local news-classifier label dataset to CSV."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.db.session import get_session
    from app.ml.news_dataset import export_news_labels

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)

    def _parse(value: str | None):
        if not value:
            return None
        s = value.strip().lower()
        if s == "today":
            return local_now.date()
        if s == "yesterday":
            return (local_now - timedelta(days=1)).date()
        from datetime import date as _date
        return _date.fromisoformat(s)

    d_from = _parse(from_date)
    d_to = _parse(to_date)
    with get_session() as db:
        path = export_news_labels(
            db,
            output_path=output_path,
            from_date=d_from,
            to_date=d_to,
            format=output_format,
            dedupe_headlines=dedupe_headlines,
        )
    click.echo(f"Exported news labels to {path}")


@cli.command("news-label-quality")
@click.option("--from", "from_date", default=None, help="Start date YYYY-MM-DD.")
@click.option("--to", "to_date", default="today", show_default=True, help="End date: today|yesterday|YYYY-MM-DD.")
@click.option("--limit-disagreements", default=10, show_default=True, type=int, help="Max disagreement candidates to print.")
@click.option("--dedupe", is_flag=True, default=False, help="Compute coverage and distributions on deduped stories.")
@click.pass_context
def news_label_quality(ctx, from_date: str | None, to_date: str, limit_disagreements: int, dedupe: bool):
    """Summarize local ML label quality and coverage."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from collections import Counter

    from app.db.models import NewsClassifierLabel, NewsClassifierShadowRun
    from app.db.session import get_session
    from app.ml.news_dataset import dedupe_label_rows

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)

    def _parse(value: str | None):
        if not value:
            return None
        s = value.strip().lower()
        if s == "today":
            return local_now.date()
        if s == "yesterday":
            return (local_now - timedelta(days=1)).date()
        from datetime import date as _date
        return _date.fromisoformat(s)

    d_from = _parse(from_date)
    d_to = _parse(to_date)

    with get_session() as db:
        q = db.query(NewsClassifierLabel)
        if d_from is not None:
            q = q.filter(NewsClassifierLabel.local_date >= d_from)
        if d_to is not None:
            q = q.filter(NewsClassifierLabel.local_date <= d_to)
        rows = list(q.order_by(NewsClassifierLabel.updated_at.desc(), NewsClassifierLabel.id.desc()).all())
        deduped = dedupe_label_rows(rows)
        stats_rows = deduped if dedupe else rows

        manual_count = sum(
            1
            for r in stats_rows
            if any(
                [
                    bool(r.manual_story_type),
                    bool(r.manual_suppression_reason),
                    r.manual_breaking_eligible is not None,
                    bool(r.manual_ticker_mismatch_risk),
                    bool(r.manual_stale_reprint_risk),
                ]
            )
        )
        manual_pct = (manual_count / len(stats_rows) * 100.0) if stats_rows else 0.0

        class_dist = Counter((r.deterministic_story_type or "unknown") for r in stats_rows)
        stale_reprint_dist = Counter((r.deterministic_freshness_state or "unknown") for r in stats_rows)
        breaking_dist = Counter(
            "true" if r.deterministic_breaking_eligible is True else ("false" if r.deterministic_breaking_eligible is False else "unknown")
            for r in stats_rows
        )

        shadow_q = db.query(NewsClassifierShadowRun).filter(NewsClassifierShadowRun.agreement.is_(False))
        if d_from is not None:
            shadow_q = shadow_q.filter(NewsClassifierShadowRun.local_date >= d_from)
        if d_to is not None:
            shadow_q = shadow_q.filter(NewsClassifierShadowRun.local_date <= d_to)
        disagreements = list(
            shadow_q.order_by(
                NewsClassifierShadowRun.ml_confidence.desc(),
                NewsClassifierShadowRun.created_at.desc(),
            )
            .limit(max(1, int(limit_disagreements)))
            .all()
        )

    click.echo(
        "LABEL QUALITY SUMMARY | "
        f"from={d_from.isoformat() if d_from else '-'} to={d_to.isoformat() if d_to else '-'} "
        f"| dedupe={'on' if dedupe else 'off'}"
    )
    click.echo(f"total_rows={len(rows)}")
    click.echo(f"deduped_stories={len(deduped)}")
    click.echo(f"manual_label_coverage={manual_count}/{len(stats_rows)} ({manual_pct:.1f}%)")
    click.echo(f"class_distribution={dict(class_dist)}")
    click.echo(f"stale_reprint_distribution={dict(stale_reprint_dist)}")
    click.echo(f"breaking_eligible_distribution={dict(breaking_dist)}")
    click.echo(f"top_disagreement_candidates={len(disagreements)}")
    for row in disagreements:
        click.echo(
            f"[{row.id}] event={row.event_id or '-'} session={row.session_key or '-'} date={row.local_date or '-'} "
            f"det={row.deterministic_story_type or '-'} ml={row.ml_story_type or '-'} "
            f"det_break={row.deterministic_breaking_eligible} ml_break={row.ml_breaking_eligible} "
            f"conf={row.ml_confidence if row.ml_confidence is not None else 0:.2f} reason={row.disagreement_reason or '-'}"
        )


@cli.group("snapshots")
def snapshots_group():
    """Read and manage live session archive snapshots (Phase 8.9 Lite)."""


@snapshots_group.command("list")
@click.option(
    "--date",
    "target_date",
    default="today",
    show_default=True,
    help="Date to inspect: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile to inspect.",
)
@click.pass_context
def snapshots_list(ctx, target_date: str, profile_name: str):
    """List live session snapshots stored for a given date.

    Only scheduler-generated sessions appear here. Backfills and dry runs
    are not stored.

    Examples:
      python -m app.cli snapshots list --date today
      python -m app.cli snapshots list --date yesterday
      python -m app.cli snapshots list --date 2026-05-05
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from app.briefing.session_snapshot_service import list_session_snapshots
    from app.db.session import init_db

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)

    s = (target_date or "today").strip().lower()
    if s == "today":
        d = local_now.date()
    elif s == "yesterday":
        d = (local_now - timedelta(days=1)).date()
    else:
        from datetime import date as _date
        try:
            d = _date.fromisoformat(s)
        except ValueError as exc:
            raise click.ClickException(f"Invalid date: {target_date!r}. Use today, yesterday, or YYYY-MM-DD.") from exc

    rows = list_session_snapshots(profile_name, d)
    if not rows:
        click.echo(f"No snapshots stored for {d.isoformat()} (profile: {profile_name}).")
        click.echo("Snapshots are only saved for live scheduler runs (not dry runs, backfills, or manual sends).")
        return

    click.echo(f"\nSession snapshots for {d.isoformat()} (profile: {profile_name})")
    click.echo(f"  {'Session':<22} {'Generated':<18} {'Delivery':<10} {'Channels':<24} {'Events'}")
    click.echo(f"  {'-'*22} {'-'*18} {'-'*10} {'-'*24} {'-'*6}")
    for row in rows:
        channels = ", ".join(
            f"{ch}:{st}" for ch, st in (row.get("delivery_channels") or {}).items()
        )
        delivery = "OK" if row["delivery_success"] else "FAILED"
        click.echo(
            f"  {row['session_key']:<22} {row['generated_at_local']:<18} "
            f"{delivery:<10} {channels:<24} {row['events_count']}"
        )
    click.echo(f"\n  {len(rows)} session(s) found.")


@snapshots_group.command("show")
@click.option(
    "--date",
    "target_date",
    required=True,
    help="Date: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--session",
    "session_key",
    required=True,
    type=click.Choice(
        ["morning", "europe_midday", "us_pre_open", "us_intraday_risk", "into_close", "closing_wrap"],
        case_sensitive=False,
    ),
    help="Session key to show.",
)
@click.option(
    "--format",
    "output_format",
    default="telegram",
    show_default=True,
    type=click.Choice(["telegram", "email-text", "email-html", "summary"], case_sensitive=False),
    help="Output format.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile to inspect.",
)
@click.pass_context
def snapshots_show(ctx, target_date: str, session_key: str, output_format: str, profile_name: str):
    """Show the stored content of a live session snapshot.

    Reads from SQLite only. No providers are called and nothing is regenerated.

    Examples:
      python -m app.cli snapshots show --date yesterday --session morning
      python -m app.cli snapshots show --date yesterday --session us_pre_open --format email-text
      python -m app.cli snapshots show --date 2026-05-05 --session closing_wrap --format summary
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from app.briefing.session_snapshot_service import get_session_snapshot
    from app.db.session import init_db

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)

    s = (target_date or "today").strip().lower()
    if s == "today":
        d = local_now.date()
    elif s == "yesterday":
        d = (local_now - timedelta(days=1)).date()
    else:
        from datetime import date as _date
        try:
            d = _date.fromisoformat(s)
        except ValueError as exc:
            raise click.ClickException(f"Invalid date: {target_date!r}. Use today, yesterday, or YYYY-MM-DD.") from exc

    snap = get_session_snapshot(profile_name, d, session_key)
    if snap is None:
        raise click.ClickException(
            f"No snapshot found for {session_key} on {d.isoformat()} (profile: {profile_name}). "
            "Only scheduler-generated live sessions are archived."
        )

    click.echo(f"\n{'='*60}")
    click.echo(f"Snapshot: {snap['session_title']} — {snap['local_date']} {snap['generated_at_local']} {snap['timezone_name']}")
    click.echo(f"Source: {snap['source_type']} | Delivery: {'OK' if snap['delivery_success'] else 'FAILED'}")
    channels_str = ", ".join(f"{ch}:{st}" for ch, st in snap.get("delivery_channels", {}).items())
    click.echo(f"Channels: {channels_str or '(none)'} | Events: {snap['events_count']}")
    click.echo(f"{'='*60}\n")

    if output_format == "telegram":
        if snap["telegram_text"]:
            click.echo(snap["telegram_text"])
        else:
            click.echo("(no Telegram content stored)")

    elif output_format == "email-text":
        if snap["email_subject"]:
            click.echo(f"Subject: {snap['email_subject']}\n")
            click.echo(snap["email_plain_text"] or "(no plain text stored)")
        else:
            click.echo("(no email content stored)")

    elif output_format == "email-html":
        if snap["email_html"]:
            click.echo(snap["email_html"])
        else:
            click.echo("(no HTML email stored — check snapshots.store_email_html preference)")

    elif output_format == "summary":
        click.echo("Market summary:")
        for q in snap.get("market_summary") or []:
            sign = "+" if q.get("change_pct", 0) >= 0 else ""
            click.echo(f"  {q.get('display_name', q.get('symbol')):<30} {sign}{q.get('change_pct', 0):.2f}%")
        click.echo("\nMacro summary:")
        for m in snap.get("macro_summary") or []:
            click.echo(f"  {m.get('name', ''):<35} {m.get('value', '')}")
        if snap.get("portfolio_summary"):
            click.echo("\nPortfolio focus:")
            for p in snap["portfolio_summary"]:
                tickers = ", ".join(p.get("tickers") or [])
                click.echo(f"  {p.get('title', '')[:80]}" + (f"  [{tickers}]" if tickers else ""))


@snapshots_group.command("prune")
@click.option(
    "--retention-days",
    type=int,
    default=30,
    show_default=True,
    help="Delete snapshots older than this many days.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile to prune.",
)
@click.pass_context
def snapshots_prune(ctx, retention_days: int, profile_name: str):
    """Delete snapshots older than retention_days.

    Pruning also runs automatically after each live scheduler session send,
    so this command is mainly useful for manual cleanup or changing retention.

    Examples:
      python -m app.cli snapshots prune --retention-days 30
      python -m app.cli snapshots prune --retention-days 14
    """
    from app.briefing.session_snapshot_service import prune_old_snapshots
    from app.db.session import init_db

    init_db()
    count = prune_old_snapshots(profile_name, retention_days=retention_days)
    click.echo(f"Pruned {count} snapshot(s) older than {retention_days} day(s) for profile '{profile_name}'.")


@snapshots_group.command("replay")
@click.option(
    "--date",
    "target_date",
    required=True,
    help="Date of the snapshot to replay: today | yesterday | YYYY-MM-DD.",
)
@click.option(
    "--session",
    "session_key",
    required=True,
    type=click.Choice(
        ["morning", "europe_midday", "us_pre_open", "us_intraday_risk_check", "into_close", "closing_wrap"],
        case_sensitive=False,
    ),
    help="Session key to replay.",
)
@click.option(
    "--profile",
    "profile_name",
    default="default_user",
    show_default=True,
    help="Profile whose snapshot archive is used.",
)
@click.option(
    "--channel",
    "channel",
    default="all",
    show_default=True,
    type=click.Choice(["all", "telegram", "email"], case_sensitive=False),
    help="Which channel(s) to replay to.",
)
@click.option(
    "--no-banner",
    "no_banner",
    is_flag=True,
    default=False,
    help="Omit the SNAPSHOT REPLAY prefix from the delivered content.",
)
@click.pass_context
def snapshots_replay(ctx, target_date: str, session_key: str, profile_name: str, channel: str, no_banner: bool):
    """Re-deliver an archived snapshot exactly as originally sent.

    Loads stored Telegram text and email content from the session archive
    and sends them through the configured messenger channels. No provider
    data is fetched, no LLM is called, and no new snapshot is created.
    Idempotency keys are not written.

    A SNAPSHOT REPLAY banner is prepended to all content so the recipient
    can distinguish replays from live sends. Use --no-banner to suppress it.

    Examples:
      python -m app.cli snapshots replay --date yesterday --session morning
      python -m app.cli snapshots replay --date 2026-05-05 --session closing_wrap --channel telegram
      python -m app.cli snapshots replay --date today --session morning --no-banner --channel email
    """
    from app.briefing.snapshot_replay import replay_snapshot
    from app.db.session import init_db

    init_db()
    settings = ctx.obj["settings"]
    tz = ZoneInfo(settings.timezone or "Europe/Madrid")
    local_now = datetime.now(timezone.utc).astimezone(tz)

    s = (target_date or "today").strip().lower()
    if s == "today":
        d = local_now.date()
    elif s == "yesterday":
        d = (local_now - timedelta(days=1)).date()
    else:
        from datetime import date as _date
        try:
            d = _date.fromisoformat(s)
        except ValueError as exc:
            raise click.ClickException(f"Invalid date: {target_date!r}. Use today, yesterday, or YYYY-MM-DD.") from exc

    click.echo(
        f"\nSnapshot replay: profile={profile_name!r} session={session_key!r} "
        f"date={d.isoformat()} channel={channel} dry_run={settings.dry_run}"
    )
    if settings.dry_run:
        click.echo("  [DRY RUN] No messages will actually be sent.")

    result = replay_snapshot(
        profile_name=profile_name,
        session_key=session_key,
        local_date=d,
        channel=channel,
        no_banner=no_banner,
        settings=settings,
    )

    if result.errors and not result.telegram_attempted and not result.email_attempted:
        click.echo(f"\n  ERROR: {result.errors[0]}")
        raise SystemExit(1)

    if result.telegram_attempted:
        status = "sent" if result.telegram_ok else ("skipped" if not result.telegram_reason or result.telegram_reason in {"Telegram not configured", "no Telegram text stored in snapshot"} else "failed")
        icon = "OK" if result.telegram_ok else ("--" if status == "skipped" else "FAIL")
        click.echo(f"  [{icon}] telegram: {status}" + (f" — {result.telegram_reason}" if result.telegram_reason else ""))

    if result.email_attempted:
        status = "sent" if result.email_ok else ("skipped" if not result.email_reason or result.email_reason in {"email not configured", "no email content stored in snapshot"} else "failed")
        icon = "OK" if result.email_ok else ("--" if status == "skipped" else "FAIL")
        click.echo(f"  [{icon}] email:    {status}" + (f" — {result.email_reason}" if result.email_reason else ""))

    if result.errors:
        click.echo(f"\n  {len(result.errors)} error(s) — check logs for details.")
        raise SystemExit(1)

    click.echo("")


# ── llm-usage ────────────────────────────────────────────────────────────────

@cli.group("llm-usage")
def llm_usage():
    """View LLM API call history, cost estimates, and budget status (Phase 9.5/9.6)."""


@llm_usage.command("summary")
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile to query.")
@click.option("--days", default=30, show_default=True, help="Lookback window in calendar days.")
def llm_usage_summary(profile_name: str, days: int):
    """Summarise LLM token usage and costs grouped by date and model.

    Also shows current-month spend vs. configured budget.

    Examples:
      python -m app.cli llm-usage summary
      python -m app.cli llm-usage summary --days 7
      python -m app.cli llm-usage summary --profile default_user --days 14
    """
    import datetime as _dt
    from app.db.session import init_db
    from app.llm.usage_tracker import query_monthly_spend, query_usage_summary
    from app.settings import get_settings

    init_db()
    settings = get_settings()
    now = _dt.datetime.now(_dt.timezone.utc)
    budget = float(settings.llm_monthly_budget_usd or 0.0)
    monthly_spend = query_monthly_spend(profile_name, now.year, now.month)
    month_label = now.strftime("%B %Y")

    click.echo(f"\nLLM usage — {month_label}, profile: {profile_name}")
    if monthly_spend is not None:
        if budget > 0:
            pct = (monthly_spend / budget) * 100
            remaining = max(0.0, budget - monthly_spend)
            status = "OVER BUDGET" if monthly_spend >= budget else "OK"
            click.echo(f"  Month spend:  ${monthly_spend:.6f} / ${budget:.4f} budget ({pct:.1f}%)  [{status}]")
            click.echo(f"  Remaining:    ${remaining:.6f}")
        else:
            click.echo(f"  Month spend:  ${monthly_spend:.6f}  (no budget set)")
    else:
        click.echo("  Month spend:  n/a (set llm_email_input/output_cost_per_1m_tokens to enable cost tracking)")
        if budget > 0:
            click.echo(f"  Budget:       ${budget:.4f} configured but unenforced (cost rates not set)")

    rows = query_usage_summary(profile_name, days=days)
    if not rows:
        click.echo(f"\nNo LLM usage recorded in the last {days} day(s).")
        return

    total_calls = sum(r["calls"] for r in rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    cost_rows = [r["estimated_cost_usd"] for r in rows if r["estimated_cost_usd"] is not None]
    total_cost = sum(cost_rows) if cost_rows else None

    click.echo(f"\n  Last {days} day(s) totals:")
    click.echo(f"  Calls:        {total_calls}")
    click.echo(f"  Tokens:       {total_tokens:,}")
    if total_cost is not None:
        click.echo(f"  Cost:         ${total_cost:.6f}")
    click.echo("")
    click.echo(f"  {'Date':<12} {'Model':<22} {'Calls':>5} {'Prompt':>8} {'Completion':>11} {'Total':>8} {'Cost USD':>12}")
    click.echo("  " + "-" * 82)
    for r in rows:
        cost_str = f"${r['estimated_cost_usd']:.6f}" if r["estimated_cost_usd"] is not None else "n/a"
        click.echo(
            f"  {r['date']:<12} {r['model']:<22} {r['calls']:>5} "
            f"{r['prompt_tokens']:>8,} {r['completion_tokens']:>11,} {r['total_tokens']:>8,} {cost_str:>12}"
        )
    click.echo("")


@llm_usage.command("list")
@click.option("--profile", "profile_name", default="default_user", show_default=True, help="Profile to query.")
@click.option("--days", default=7, show_default=True, help="Lookback window in calendar days.")
@click.option("--limit", default=50, show_default=True, help="Maximum rows to return.")
def llm_usage_list(profile_name: str, days: int, limit: int):
    """List individual LLM API calls, newest first.

    Examples:
      python -m app.cli llm-usage list
      python -m app.cli llm-usage list --days 14 --limit 20
    """
    from app.db.session import init_db
    from app.llm.usage_tracker import query_usage_rows

    init_db()
    rows = query_usage_rows(profile_name, days=days, limit=limit)
    if not rows:
        click.echo(f"No LLM usage recorded in the last {days} day(s) for profile '{profile_name}'.")
        return

    click.echo(f"\nLLM calls — last {days} day(s), profile: {profile_name} (showing up to {limit})")
    click.echo(f"  {'Date':<12} {'Session':<22} {'Model':<18} {'Mode':<8} {'Prompt':>7} {'Compl':>7} {'Cost':>10}")
    click.echo("  " + "-" * 90)
    for r in rows:
        cost_str = f"${r['estimated_cost_usd']:.6f}" if r["estimated_cost_usd"] is not None else "n/a"
        click.echo(
            f"  {r['local_date']:<12} {r['session_key']:<22} {r['model']:<18} {r['mode']:<8} "
            f"{r['prompt_tokens']:>7,} {r['completion_tokens']:>7,} {cost_str:>10}"
        )
    click.echo("")


@cli.command("delivery-log")
@click.option("--date", "target_date", default="today", help="Date: today | yesterday | YYYY-MM-DD")
@click.option("--profile", "profile_name", default="default_user", help="Profile name")
@click.pass_context
def delivery_log(ctx, target_date: str, profile_name: str) -> None:
    """Show delivery history for a date: session, channel, subject, source, sent time, hash, success."""
    from app.main import get_session
    from app.db.models import SentMessage, SessionSendState
    from app.personalization.user_profile import load_user_profile
    from datetime import date as _date, datetime as _dt, timedelta
    from datetime import timezone
    from zoneinfo import ZoneInfo
    import re

    settings = ctx.obj["settings"]
    init_db()

    tz_name = settings.timezone or "Europe/Madrid"
    try:
        profile = load_user_profile(settings, profile_name)
        tz_name = profile.timezone or tz_name
    except Exception:
        pass
    tz = ZoneInfo(tz_name)

    now_local = _dt.now(tz)
    if target_date.lower() == "today":
        target = now_local.date()
    elif target_date.lower() == "yesterday":
        target = (now_local - timedelta(days=1)).date()
    else:
        target = _date.fromisoformat(target_date)

    date_str = target.strftime("%A %d %b %Y")
    print(f"\nDelivery Log — {date_str} ({tz_name})\n")

    with get_session() as db:
        rows = (
            db.query(SessionSendState)
            .filter(
                SessionSendState.profile_name == profile_name,
                SessionSendState.local_date == target,
            )
            .order_by(SessionSendState.sent_at)
            .all()
        )
        sent_rows = (
            db.query(SentMessage)
            .filter(
                SentMessage.message_type.like("session_brief:%"),
                SentMessage.sent_at.isnot(None),
            )
            .order_by(SentMessage.sent_at.asc())
            .all()
        )

    if not rows:
        print("  No delivery records found for this date.")
        return

    from app.briefing.session_metadata import label_for
    html_re = re.compile(r"<[^>]+>")

    # Build a lightweight lookup between session_send_state and sent_messages.
    # We match by channel + canonical message_type + close send timestamps.
    sent_lookup: dict[int, SentMessage] = {}
    for row in rows:
        if row.sent_at is None:
            continue
        best: SentMessage | None = None
        best_delta: float | None = None
        for sent in sent_rows:
            if sent.channel != row.channel:
                continue
            if sent.message_type != row.message_type:
                continue
            if sent.sent_at is None:
                continue
            delta = abs((sent.sent_at - row.sent_at).total_seconds())
            if delta > 180:
                continue
            if best is None or (best_delta is not None and delta < best_delta):
                best = sent
                best_delta = delta
        if best is not None:
            sent_lookup[row.id] = best
    print(
        f"  {'Session':<24} {'Channel':<8} {'Subject':<38} {'Source':<10} "
        f"{'Sent at local':<18} {'Hash':<16} {'OK':<5}"
    )
    print("  " + "-" * 132)
    for r in rows:
        label = label_for(r.session_key)
        session_label = f"{label} ({r.session_key})"
        sent_str = ""
        if r.sent_at:
            sent_utc = r.sent_at.replace(tzinfo=timezone.utc)
            sent_local = sent_utc.astimezone(tz)
            sent_str = sent_local.strftime("%Y-%m-%d %H:%M")
        ok = "✓" if r.success else "✗"
        src = r.command_source or "?"
        sent_row = sent_lookup.get(r.id)
        content_hash = (sent_row.content_hash if sent_row else "") or "-"
        subject = "-"
        if sent_row and sent_row.content_preview:
            stripped = html_re.sub("", sent_row.content_preview)
            first_line = next((line.strip() for line in stripped.splitlines() if line.strip()), "")
            subject = first_line or "-"
        if subject == "-":
            subject = label
        if len(subject) > 38:
            subject = subject[:35] + "..."
        print(
            f"  {session_label:<24} {r.channel:<8} {subject:<38} {src:<10} "
            f"{sent_str:<18} {content_hash:<16} {ok:<5}"
        )
    print()


@cli.command("version")
@click.pass_context
def version_cmd(ctx, **kwargs) -> None:
    """Show app version, git commit, and scheduler status."""
    import subprocess
    import os

    # Git info
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/Users/jack/market-briefing-bot",
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        commit = "unknown"

    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd="/Users/jack/market-briefing-bot",
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        branch = "unknown"

    try:
        commit_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci"],
            cwd="/Users/jack/market-briefing-bot",
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        commit_date = "unknown"

    print(f"Briefly")
    print(f"  Branch:  {branch}")
    print(f"  Commit:  {commit}  ({commit_date})")

    # Check scheduler lock file (same path as app/scheduler.py)
    settings = ctx.obj["settings"]
    lock_path = os.path.join(settings.data_dir, "state", "scheduler.lock")
    if os.path.exists(lock_path):
        try:
            import fcntl
            with open(lock_path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            print(f"  Scheduler: not running (lock file exists but no process holds it)")
        except BlockingIOError:
            print(f"  Scheduler: running (lock held)")
    else:
        print(f"  Scheduler: not running")


if __name__ == "__main__":
    cli()
