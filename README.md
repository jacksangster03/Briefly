# market-briefing-bot

Market intelligence delivered to your phone. The bot generates a morning briefing, curated intraday updates, and breaking alerts, then sends them through Telegram with SQLite-backed state, YAML configuration, and provider fallback logic.

The current product is no longer just a headline feed:
- it deduplicates and clusters overlapping stories
- it formats messages for mobile reading
- it handles weekends differently from live weekday sessions
- it supports watchlists and persisted portfolio holdings
- it includes operational safeguards for flaky providers

## Current capabilities

- **Morning briefing**
  - market setup, macro context, top themes, sector scan, watchlist, and a portfolio-first section
- **Intraday updates**
  - only new, material developments above threshold
  - weekend-aware formatting when cash equity markets are closed
- **Breaking alerts**
  - high-threshold event checks with clearer market context
- **Portfolio-aware intelligence**
  - holdings import from YAML or CSV
  - holdings persistence in SQLite
  - holdings-aware relevance scoring and intraday tie-breaks
- **Phase 3.5 rich delivery**
  - historical price retrieval for chart rendering
  - static PNG chart cards for morning/weekend briefs
  - HTML email rendering with inline charts
  - optional Telegram hero-chart delivery (text-first by default)
- **Phase 3.6 editorial trust**
  - section-level trust gating for `TOP THEMES` and `SECTOR SCAN`
  - weekend-specific source/title penalties under thin source mixes
  - cross-section event de-duplication to reduce repeated headlines
- **Operational resilience**
  - quote fallback to `yfinance`
  - fail-fast behavior for degraded quote/news paths
  - provider circuit breaker to prevent repeated timeout stalls in one process
- **Manual inspection mode**
  - `--show-output` prints the rendered Telegram payload to the terminal even on live runs

## Quick start

```bash
git clone https://github.com/jacksangster03/market-briefing-bot.git
cd market-briefing-bot
pip install -e ".[dev]"

cp .env.example .env
cp configs/user_profile.example.yaml configs/user_profile.yaml
cp configs/watchlists.example.yaml configs/watchlists.yaml

make setup
make test
```

At minimum, configure:
- `FINNHUB_API_KEY`
- `FRED_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Core commands

```bash
python -m app.cli morning
python -m app.cli intraday
python -m app.cli breaking
python -m app.cli scheduler
python -m app.cli init-db
python -m app.cli status
python -m app.cli quote NVDA
python -m app.cli news
```

Channel-control examples:

```bash
# send only email (skip Telegram) for this run
python -m app.cli --email-only morning

# send only Telegram (skip email) for this run
python -m app.cli --telegram-only morning
```

### Dry-run and output inspection

Use `--dry-run` to avoid live delivery:

```bash
python -m app.cli --dry-run morning
python -m app.cli --dry-run intraday
python -m app.cli --dry-run breaking
```

Use `--show-output` to always print the rendered Telegram payload to the terminal:

```bash
python -m app.cli --show-output --dry-run morning
python -m app.cli --show-output --dry-run intraday
python -m app.cli --show-output --dry-run breaking
```

Combine with channel controls when validating one surface at a time:

```bash
python -m app.cli --dry-run --show-output --email-only morning
python -m app.cli --dry-run --show-output --telegram-only morning
```

For morning runs, `--show-output` now also prints a rich-email preview summary and lists any generated chart cards, even if email delivery is not configured yet.

This is useful when:
- you want to inspect the exact rendered message locally
- you want live delivery **and** terminal output during testing
- you are debugging scoring/formatter behavior without relying on Telegram history

## Portfolio holdings workflow

Example holdings config:

- [configs/holdings.example.yaml](/Users/jack/market-briefing-bot/configs/holdings.example.yaml)

Import holdings into the SQLite state store:

```bash
python -m app.cli import-holdings --file configs/holdings.example.yaml --profile default_user
```

Supported formats:
- YAML (`.yaml`, `.yml`)
- CSV (`.csv`)

Once imported, later morning/intraday runs load holdings automatically and use them for:
- direct holding relevance boosts
- sector exposure read-through
- `PORTFOLIO FOCUS` selection in the morning briefing
- intraday prioritization when two stories are close in quality/score

## Provider behavior and resilience

### Normal provider flow

- **Finnhub**
  - primary source for quotes, market news, company news, and earnings calendar
- **NewsAPI**
  - headline enrichment / fallback source
- **FRED**
  - macro indicators and Treasury context
- **SEC EDGAR**
  - filings
- **yfinance**
  - quote fallback when primary quote data is unavailable

### Degraded-provider behavior

The bot now includes several protections to keep runs responsive:

- quote fetches use a tighter timeout/retry budget than news fetches
- quote batches abort early after consecutive misses instead of timing out symbol-by-symbol for the whole universe
- a session-level provider circuit breaker short-circuits repeated calls after consecutive terminal failures
- `yfinance` fills quote gaps when Finnhub quotes are unhealthy

This matters most for:
- manual testing from terminal
- live morning runs during provider incidents
- long-running scheduler sessions that would otherwise keep burning timeout budget on every cycle

## Configuration

### Environment (`.env`)

Important settings include:
- `FINNHUB_API_KEY`
- `NEWSAPI_KEY`
- `FRED_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `PROVIDER_TIMEOUT`
- `PROVIDER_MAX_RETRIES`
- `ENABLE_CHARTS`
- `TELEGRAM_SEND_CHARTS`
- `DELIVERY_CHANNEL` (`all`, `telegram`, `email`)

Recommended delivery defaults:
- keep Telegram text-first: `TELEGRAM_SEND_CHARTS=false`
- use rich charts in email: `ENABLE_CHARTS=true`
- leave channel routing at `DELIVERY_CHANNEL=all` and override per-run with CLI flags

General runtime settings are defined in:
- [app/settings.py](/Users/jack/market-briefing-bot/app/settings.py)

### YAML config

- user profile:
  - [configs/user_profile.example.yaml](/Users/jack/market-briefing-bot/configs/user_profile.example.yaml)
- watchlists:
  - [configs/watchlists.example.yaml](/Users/jack/market-briefing-bot/configs/watchlists.example.yaml)
- schedules:
  - [configs/schedules.yaml](/Users/jack/market-briefing-bot/configs/schedules.yaml)
- sectors:
  - [configs/sectors.yaml](/Users/jack/market-briefing-bot/configs/sectors.yaml)
- alert thresholds:
  - [configs/alert_rules.yaml](/Users/jack/market-briefing-bot/configs/alert_rules.yaml)
- holdings:
  - [configs/holdings.example.yaml](/Users/jack/market-briefing-bot/configs/holdings.example.yaml)

## Product architecture

```text
Providers
  Finnhub / NewsAPI / FRED / SEC / yfinance

Data services
  market_data / news_data / macro_data

Processing pipeline
  cleaners -> ticker resolution -> sector enrichment -> dedupe
  -> credibility -> personal relevance -> clustering -> scoring

Briefing generation
  morning / intraday / breaking

Formatting + delivery
  TelegramFormatter -> Telegram / Email

Persistence
  SQLite for sent messages, provider health, events, market snapshots, holdings
```

Key code areas:
- [app/main.py](/Users/jack/market-briefing-bot/app/main.py)
- [app/processing/pipeline.py](/Users/jack/market-briefing-bot/app/processing/pipeline.py)
- [app/processing/relevance_scoring.py](/Users/jack/market-briefing-bot/app/processing/relevance_scoring.py)
- [app/briefing/formatter.py](/Users/jack/market-briefing-bot/app/briefing/formatter.py)
- [app/portfolio/importer.py](/Users/jack/market-briefing-bot/app/portfolio/importer.py)
- [app/portfolio/service.py](/Users/jack/market-briefing-bot/app/portfolio/service.py)

## Testing

Run the full suite:

```bash
make test
```

Useful focused commands:

```bash
python -m pytest app/tests/test_portfolio_phase3.py -q
python -m pytest app/tests/test_market_data.py -q
python -m pytest app/tests/test_show_output.py -q
python -m pytest app/tests/test_circuit_breaker.py -q
```

The test suite currently covers:
- scoring and ranking
- formatting and weekend presentation
- scheduler window behavior
- holdings import/persistence/scoring
- manual `--show-output` mode
- market-data fallback behavior
- provider circuit breaker behavior

## Typical workflows

### 1. Preview a morning briefing locally

```bash
python -m app.cli --show-output --dry-run morning
```

### 2. Send a real morning briefing and still inspect terminal output

```bash
python -m app.cli --show-output morning
```

### 3. Import holdings, then preview the portfolio-aware morning brief

```bash
python -m app.cli import-holdings --file configs/holdings.example.yaml --profile default_user
python -m app.cli --show-output --dry-run morning
```

### 4. Start the live scheduler

```bash
python -m app.cli scheduler
```

The scheduler only runs while that process is alive. `Ctrl+C` stops automatic sends.

## Current phase

The repo is now beyond basic plumbing:

- **Phase 2** is materially complete
  - better formatting
  - ticker/entity trust improvements
  - weekend mode
  - breaking schedule precision
  - cleaner alert presentation
- **Phase 3 backend foundation** is now in place
  - holdings import and persistence
  - portfolio-aware relevance
  - `PORTFOLIO FOCUS`
  - intraday prioritization
  - operational debugging tools like `--show-output`

The biggest remaining quality gap is still editorial/source quality in some surfaced headlines, not the core plumbing.

Recent improvement note:
- weekend side-angle headlines are now less likely to surface in `TOP THEMES` / `SECTOR SCAN`
- repeated stories are de-duplicated across morning sections in a deterministic order

## License

MIT
