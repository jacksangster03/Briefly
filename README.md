# market-briefing-bot

Production-grade market intelligence messenger. Delivers a morning briefing, hourly intraday updates, and breaking alerts to your phone via Telegram.

## What it does

- **Morning briefing** before US market open: market setup, macro context, top themes, sector scan, earnings calendar, watchlist
- **Hourly intraday updates** during market hours: only new, material developments (no spam, no repeats)
- **Breaking alerts** for high-threshold events: earnings shocks, FDA decisions, M&A, macro surprises

Messages are optimised for phone reading: numbers first, concise, no hype, clear sections.

## Quick start

```bash
# Clone and install
git clone https://github.com/your-user/market-briefing-bot.git
cd market-briefing-bot
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your API keys (at minimum: FINNHUB_API_KEY)

cp configs/user_profile.example.yaml configs/user_profile.yaml
cp configs/watchlists.example.yaml configs/watchlists.yaml
# Customise your profile and watchlist

# Initialise database
make setup

# Test run (dry run, output to console)
DRY_RUN=true python -m app.cli morning

# Run tests
make test
```

## Required API keys

| Provider | Key | Free tier | Purpose |
|---|---|---|---|
| **Finnhub** | `FINNHUB_API_KEY` | 60 calls/min | Quotes, news, earnings calendar |
| **FRED** | `FRED_API_KEY` | Unlimited | Treasury yields, macro indicators |
| **SEC EDGAR** | `SEC_USER_AGENT` | No key needed | Company filings (8-K, 10-K, etc.) |

## Optional API keys

| Provider | Key | Purpose | Degrades if missing |
|---|---|---|---|
| NewsAPI | `NEWSAPI_KEY` | Broad headline enrichment | Fewer news sources |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Message delivery | Must use dry-run mode |
| Email | `EMAIL_*` vars | Backup delivery channel | No email fallback |
| Polygon | `POLYGON_API_KEY` | Premium market data | Not used in V1 |
| Alpaca | `ALPACA_API_KEY` | Paper trading | Trading module disabled |

## Architecture

```
Providers (Finnhub, FRED, SEC, NewsAPI, yfinance)
    |
Data Services (market, news, macro) with fallback chains
    |
Processing Pipeline (normalise, dedupe, score, rank)
    |
Briefing Generators (morning, intraday, breaking)
    |
Formatter (Telegram-optimised, mobile-first)
    |
Messaging (Telegram, Email, pluggable Slack/Discord)
    |
SQLite persistence + YAML config (user profile, sectors, watchlists)
```

### Key design decisions

- **Provider-agnostic**: each data service has a fallback chain. Finnhub primary, yfinance fallback for quotes.
- **Free-first**: runs meaningfully with only free-tier API keys.
- **Personalisation via config**: user profile, sector weights, watchlists, delivery preferences are all YAML. No hardcoded preferences in business logic.
- **Multi-dimensional scoring**: source credibility, event type importance, personal relevance, novelty, and factual confidence are tracked separately, then combined into a final score.
- **Deduplication pipeline**: exact hash, normalised headline matching, ticker + time window clustering, already-sent check against DB.

## CLI commands

```bash
python -m app.cli morning      # Run morning briefing
python -m app.cli intraday     # Run one intraday update
python -m app.cli breaking     # Run one breaking alert check
python -m app.cli scheduler    # Start full scheduler
python -m app.cli init-db      # Initialise database
python -m app.cli status       # Show config status
python -m app.cli quote NVDA   # Fetch a single quote
python -m app.cli news         # Fetch latest market news
```

Add `--dry-run` to any command to preview output without sending messages.

## Configuration

### User profile (`configs/user_profile.yaml`)

Controls timezone, geographic coverage weights, sector preferences, delivery schedule, and message style.

### Watchlists (`configs/watchlists.yaml`)

Three tiers: primary (highest boost), secondary, monitor. Tickers on your watchlist get boosted relevance in all briefings and a dedicated section in the morning brief.

### Sectors (`configs/sectors.yaml`)

Sector definitions with representative ETFs and key company tickers. Used for the sector scan section and event-to-sector mapping.

### Schedules (`configs/schedules.yaml`)

Morning briefing time, intraday update window, breaking alert polling interval.

## Scoring system

Every event is scored across five dimensions:

1. **Source credibility**: SEC filings (0.95) > Finnhub (0.75) > NewsAPI (0.55) > social (0.25)
2. **Event type weight**: earnings/FDA/M&A (0.90+) > filings (0.60-0.80) > general news (0.45)
3. **Personal relevance**: sector match, watchlist match, region match
4. **Novelty**: whether the user has already seen this event
5. **Factual confidence**: set by source tier, adjusted by corroboration

The composite `final_score` determines inclusion in briefings and alert delivery.

## Docker

```bash
# Full scheduler
docker compose up -d

# One-shot morning briefing
docker compose run morning
```

## Project structure

```
market-briefing-bot/
  app/
    main.py              # Top-level orchestration
    cli.py               # Click CLI
    settings.py          # pydantic-settings config
    scheduler.py         # APScheduler setup
    db/                  # SQLAlchemy models and session
    schemas/             # Pydantic data models
    data_sources/        # Provider implementations
      providers/         # Finnhub, FRED, SEC, NewsAPI, yfinance
    processing/          # Dedupe, scoring, cleaning
    briefing/            # Morning/intraday/breaking generators + formatter
    messaging/           # Telegram, Email delivery
    personalization/     # User profile loader
    universe/            # Sector/ticker universe
    tests/               # pytest suite
  configs/               # YAML configuration files
  scripts/               # Standalone runner scripts
  data/                  # SQLite DB, caches, raw data
```

## Roadmap

- **Phase 1** (current): Morning briefing, intraday updates, breaking alerts, free-first providers, Telegram delivery, SQLite
- **Phase 2**: Improved dedup (semantic similarity), event clustering, source credibility refinement, better formatting
- **Phase 3**: AI summariser abstraction, portfolio tracker, watchlist personalisation, region-aware ranking
- **Phase 4**: Paper trading module, signal engine, risk manager, backtesting scaffold

## Licence

MIT
