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
    if llm_enabled and not settings.llm_render_shadow_mode and not settings.dry_run:
        warnings.append("LLM live mode is enabled. Use shadow mode first for 3-5 inspected runs.")
    if settings.normalized_delivery_channel == "telegram" and llm_enabled:
        warnings.append("LLM render is email-first; telegram-only runs will not exercise LLM output.")
    if settings.normalized_delivery_channel == "email" and not settings.email_configured and not settings.dry_run:
        warnings.append("Email-only live delivery requested but email credentials are not configured.")

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


if __name__ == "__main__":
    cli()
