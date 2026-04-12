"""CLI interface for market-briefing-bot using Click."""

from __future__ import annotations

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
    """market-briefing-bot: Market intelligence delivered to your phone."""
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
@click.pass_context
def morning(ctx):
    """Generate and send the morning briefing."""
    from app.main import run_morning_briefing
    run_morning_briefing(ctx.obj["settings"])


@cli.command()
@click.pass_context
def intraday(ctx):
    """Run a single intraday update cycle."""
    from app.main import run_intraday_update
    run_intraday_update(ctx.obj["settings"])


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
    help="Bind host for control panel server (default from WEB_HOST).",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Bind port for control panel server (default from WEB_PORT).",
)
@click.pass_context
def web_panel(ctx, host: str | None, port: int | None):
    """Run the local FastAPI + HTMX portfolio control panel."""
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
        f"Starting control panel at http://{settings.web_host}:{settings.web_port}/ui/settings"
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
    click.echo("market-briefing-bot status")
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

    click.echo("phase-4 preflight")
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


if __name__ == "__main__":
    cli()
