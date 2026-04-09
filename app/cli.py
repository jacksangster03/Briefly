"""CLI interface for market-briefing-bot using Click."""

from __future__ import annotations

import click

from app.db.session import init_db
from app.logger import setup_logging
from app.settings import get_settings


@click.group()
@click.option("--dry-run/--no-dry-run", default=None, help="Override DRY_RUN setting.")
@click.pass_context
def cli(ctx, dry_run):
    """market-briefing-bot: Market intelligence delivered to your phone."""
    setup_logging()
    ctx.ensure_object(dict)
    settings = get_settings()
    if dry_run is not None:
        settings.dry_run = dry_run
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
