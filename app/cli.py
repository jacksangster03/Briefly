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
@click.pass_context
def cli(ctx, dry_run, show_output):
    """market-briefing-bot: Market intelligence delivered to your phone."""
    setup_logging()
    ctx.ensure_object(dict)
    settings = get_settings()
    if dry_run is not None:
        settings.dry_run = dry_run
    settings.show_output = show_output
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
    click.echo(f"  Database:    {settings.database_url}")
    click.echo(f"  Finnhub:     {'configured' if settings.finnhub_configured else 'not set'}")
    click.echo(f"  FRED:        {'configured' if settings.fred_configured else 'not set'}")
    click.echo(f"  NewsAPI:     {'configured' if settings.newsapi_configured else 'not set'}")
    click.echo(f"  Telegram:    {'configured' if settings.telegram_configured else 'not set'}")
    click.echo(f"  Email:       {'configured' if settings.email_configured else 'not set'}")


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
