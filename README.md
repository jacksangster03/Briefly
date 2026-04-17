# Briefly

Briefly is a portfolio-intelligence platform with three coordinated modules:
- **Market Briefing** for morning, intraday, and breaking intelligence delivery
- **Portfolio Control** for holdings snapshots and profile-level personalization
- **Trading Lab (planned)** for future execution/sentiment workflows

It currently generates morning briefings, curated intraday updates, and breaking alerts, then delivers through Telegram/email with SQLite-backed state, YAML configuration, and resilient provider fallback logic.

The product is no longer just a headline feed:
- it deduplicates and clusters overlapping stories
- it formats messages for mobile reading
- it handles weekends differently from live weekday sessions
- it supports watchlists, region/sector focus, and persisted portfolio holdings
- it includes operational safeguards for flaky providers

## Product modules

- **Market Briefing**
  - event processing pipeline, ranking, sectioned rendering, Telegram/email delivery
- **Portfolio Control**
  - holdings import/persistence, preference overrides, local web control center
- **Trading Lab (planned)**
  - future trading/sentiment workflows on top of deterministic intelligence core

See [docs/product_modules.md](/Users/jack/briefly/docs/product_modules.md) for the module map.

## Current capabilities

- **Morning briefing**
  - market setup, macro context, global geopolitics section, top themes, sector scan, watchlist, and a portfolio-first section
  - expanded market setup panel now includes US, Europe, Asia, VIX, 10Y UST, WTI, and gold levels
- **Intraday updates**
  - only new, material developments above threshold
  - snapshot now shows exact index/asset levels with percentage moves (not percent-only)
  - compact `GLOBAL RISK UPDATE` block when high-trust market-linked geopolitical/macro items are present
  - weekend-aware formatting when cash equity markets are closed
- **Breaking alerts**
  - high-threshold event checks with clearer market context
  - deterministic breaking classifier (`breaking` / `high_priority` / `regular` / `ignore`) with market-link gates
  - storyline-key cooldown to suppress near-duplicate geopolitical headline churn
  - event-driven send logic (polling is for detection only; no fixed send cadence)
  - optional one-shot follow-up state machine (`initial_sent -> followup_due -> followup_sent|closed`) with market-reaction confirmation checks
  - strict one-shot suppression: continuation/material-update repeats are filtered out
  - one alert per cycle (highest-priority only) to prevent notification spam
  - flood control via rolling-hour cap + storyline cooldown
- **Portfolio-aware intelligence**
  - holdings import from YAML or CSV
  - holdings persistence in SQLite
  - holdings-aware relevance scoring and intraday tie-breaks
- **Phase 3.5 rich delivery**
  - historical price retrieval for chart rendering
  - static PNG chart cards for morning/weekend briefs
  - chart pack now includes:
    - Market Snapshot
    - Macro Risk Strip
    - Top Holdings Performance
    - Sector Exposure vs Performance
    - Event-Linked Trend
  - HTML email rendering with inline charts
  - optional Telegram hero-chart delivery (text-first by default)
- **Phase 3.6 editorial trust**
  - section-level trust gating for `TOP THEMES` and `SECTOR SCAN`
  - weekend-specific source/title penalties under thin source mixes
  - cross-section event de-duplication to reduce repeated headlines
- **Phase 3.6b quote transparency + density control**
  - quote freshness lines (`as of <local time>`, provider source mix) in watchlist/sector output
  - explicit close-reference wording on weekend quote lines
  - compact handling for empty sector sections to avoid long blank runs
- **Phase 4 email render layer (preflight + fallback safe)**
  - deterministic selection stays upstream (no LLM ranking)
  - optional LLM render for morning/weekend email prose only
  - strict guardrails: ticker/number/link validation against deterministic payload
  - citation floor guard (`LLM_EMAIL_MIN_SOURCE_URLS`) for rendered output
  - OpenAI-compatible base URL support (`LLM_API_BASE_URL`)
  - shadow mode for safe rollout before enabling live LLM email rendering
  - deterministic formatter fallback on any LLM request/validation failure
- **Phase 4.2 control plane (persisted profile overrides)**
  - per-profile, DB-backed overrides for channels, watchlists, sector weights, and morning sections
  - runtime profile load merges YAML defaults with persisted overrides
  - per-message routing (`morning` / `intraday`) can be customized by profile
- **Phase 4.3 web control center (FastAPI + HTMX)**
  - local-first settings UI for holdings, watchlists, sector weights, delivery routing, and morning section visibility
  - searchable watchlist builders (chip-based add/remove) instead of long multiselect lists
  - region-focus controls (`home_region` + `coverage_weights`) alongside sector weights
  - terminal-style professional UI with persistent light/dark mode toggle
  - sticky section navigation + contextual helper text for faster configuration
  - validation warnings (delivery gaps, weight imbalance) and save timestamps
  - server-rendered panel with partial HTMX updates (no frontend build step)
  - programmatic control-plane API for state, preference updates, followables search, and holdings upload
- **Phase 4.4 global market/geopolitics intelligence**
  - deterministic high-trust selector for market-linked global stories (no broad non-market world-news feed)
  - lightweight region enrichment (`US`, `Europe`, `Middle East`, `Asia`, `LATAM`, `Global Macro`)
  - `GLOBAL NEWS & GEOPOLITICS` section in morning briefing
  - `GLOBAL RISK UPDATE` block in intraday (toggleable from preferences/control panel)
  - cross-section dedupe keeps global stories from repeating in other morning sections
- **Phase 4.5 global coverage engine (foundation)**
  - `GlobalNewsHubService` merges Finnhub + NewsAPI with optional adapters for GDELT, Alpha Vantage, FMP, and Mediastack
  - per-event canonical metadata (`canonical_url`, `domain`, `source_name`, `provider_event_id`)
  - cross-provider story fingerprinting and dedupe before scoring pipeline
  - process-level per-provider daily call budgets to stay within free-tier limits
- **Cross-type anti-repeat policy**
  - events already sent in one channel/type are suppressed from later morning/intraday/breaking surfacing
  - intraday and breaking now surface only `new` catalysts (not continuation repeats)
  - breaking delivery is hard-routed to Telegram (no breaking email fan-out)
  - breaking checks use a cross-process run lock + sent-ID guard to prevent repeat bursts
- **Phase 4.6 cadence engine**
  - `CadenceEngine` + `DecisionEngine` enforce:
    - one `Morning Briefing` per local day in local morning window
    - one `Intraday Update` per local day in local pre–US-open window
    - BREAKING only when classification thresholds pass
  - US cash open is computed from `09:30 America/New_York` converted into user local timezone via IANA zones (DST-safe)
  - NYSE holiday + half-day awareness added for pre-open gating
  - daily idempotency markers persisted in SQLite (`morning:{local_date}`, `intraday:{local_date}`)
- **Phase 4.7B portfolio analyzer intelligence**
  - analyzer logic extracted into a dedicated deterministic analytics module
  - live portfolio page now surfaces:
    - coverage alignment findings
    - tomorrow's briefing influence map
    - health checks for coverage/watchlist/region/delivery fit
    - holdings data-quality diagnostics
  - analyzer stays local-first and explanation-driven, with no forecasting or trading logic mixed in
- **Phase 4.7C scenario stress tests**
  - portfolio analyzer now adds deterministic stress checks for:
    - semis down 10%
    - rates +50 bps
    - oil shock
    - dollar spike
    - small-cap risk-off
  - each scenario estimates current portfolio sensitivity, highlights exposed holdings/sectors, and stays explicit that it is a static shock test rather than a forecast
- **Phase 4.8 portfolio UX + control-plane upgrade**
  - top-level Portfolio Analyzer / Market Briefing modules now behave like distinct page modes
  - holdings editor now supports faster rebalancing with inline sector labels, +/- steppers, equal-weight and rebalance helpers
  - overview/analyzer surface now includes snapshot confidence, friendlier briefing summaries, and a cleaner History & Advanced area instead of exposing raw override internals by default
- **Operational resilience**
  - quote fallback to `yfinance`
  - fail-fast behavior for degraded quote/news paths
  - provider circuit breaker to prevent repeated timeout stalls in one process
- **Manual inspection mode**
  - `--show-output` prints the rendered Telegram payload to the terminal even on live runs

## Quick start

```bash
# preferred after GitHub repo rename
git clone https://github.com/jacksangster03/briefly.git
cd briefly

# if the repository has not been renamed yet, keep using:
# git clone https://github.com/jacksangster03/market-briefing-bot.git
# cd market-briefing-bot

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
python -m app.cli web --host 127.0.0.1 --port 8080
python -m app.cli init-db
python -m app.cli status
python -m app.cli preflight
python -m app.cli prefs-show --profile default_user
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["email"]'
python -m app.cli prefs-unset --profile default_user --key delivery.morning_channels
python -m app.cli prefs-reset --profile default_user
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

Recommended per-profile routing for this setup:

```bash
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.intraday_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.breaking_channels --value '["telegram"]'
python -m app.cli prefs-show --profile default_user
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

## Expanding global news coverage (free/low-cost options)

If the current Finnhub + NewsAPI mix is thin during specific windows, Phase 4.5 can be enabled incrementally:
- **GDELT DOC API** (`ENABLE_GDELT=true`): broad global event/article recall.
- **Alpha Vantage NEWS_SENTIMENT** (`ENABLE_ALPHA_VANTAGE_NEWS=true`): topic-filtered market linkage.
- **FMP news** (`ENABLE_FMP_NEWS=true`): supplemental finance headlines.
- **Mediastack** (`ENABLE_MEDIASTACK_NEWS=true`): low-volume backup feed.

Keep the same trust-gating and market-link filters before surfacing any new-source stories.

Quick API setup (paste into `.env`):

```bash
# Phase 4.5 API keys
ALPHA_VANTAGE_API_KEY=
FMP_API_KEY=
MEDIASTACK_API_KEY=

# Provider toggles
ENABLE_GDELT=true
ENABLE_ALPHA_VANTAGE_NEWS=true
ENABLE_FMP_NEWS=false
ENABLE_MEDIASTACK_NEWS=false

# Free-tier budgets
GDELT_DAILY_CALL_BUDGET=250
ALPHA_VANTAGE_NEWS_DAILY_CALL_BUDGET=20
FMP_NEWS_DAILY_CALL_BUDGET=120
MEDIASTACK_NEWS_DAILY_CALL_BUDGET=3

# Query/topic shaping
GLOBAL_NEWS_MAX_RECORDS=50
ALPHA_VANTAGE_TOPICS=economy_macro,economy_monetary,energy_transportation,financial_markets
GDELT_GLOBAL_QUERY=(inflation OR sanctions OR tariffs OR oil OR shipping OR blockade OR war OR ceasefire OR central bank OR rates OR treasury OR dollar OR fx OR supply chain)
FMP_NEWS_LIMIT=50
MEDIASTACK_NEWS_LIMIT=25
```

## Portfolio holdings workflow

Example holdings config:

- [configs/holdings.example.yaml](/Users/jack/briefly/configs/holdings.example.yaml)

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

Briefly now includes several protections to keep runs responsive:

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
- `ALPHA_VANTAGE_API_KEY` (optional, Phase 4.5)
- `FMP_API_KEY` (optional, Phase 4.5)
- `MEDIASTACK_API_KEY` (optional, Phase 4.5)
- `FRED_API_KEY`
- `OPENAI_API_KEY` (required only if enabling Phase 4 LLM email render)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `PROVIDER_TIMEOUT`
- `PROVIDER_MAX_RETRIES`
- `ENABLE_CHARTS`
- `TELEGRAM_SEND_CHARTS`
- `ENABLE_LLM_EMAIL_RENDER`
- `LLM_RENDER_SHADOW_MODE`
- `LLM_EMAIL_MODEL`
- `LLM_EMAIL_INPUT_COST_PER_1M_TOKENS` (optional per-run cost estimate)
- `LLM_EMAIL_OUTPUT_COST_PER_1M_TOKENS` (optional per-run cost estimate)
- `LLM_API_BASE_URL`
- `LLM_EMAIL_TIMEOUT_SECONDS`
- `LLM_EMAIL_MAX_CHARS`
- `LLM_EMAIL_MIN_SOURCE_URLS`
- `DELIVERY_CHANNEL` (`all`, `telegram`, `email`)
- `WEB_HOST`, `WEB_PORT` (Phase 4.3 local control-center bind)
- `ENABLE_GDELT`, `ENABLE_ALPHA_VANTAGE_NEWS`, `ENABLE_FMP_NEWS`, `ENABLE_MEDIASTACK_NEWS`
- `GDELT_DAILY_CALL_BUDGET`, `ALPHA_VANTAGE_NEWS_DAILY_CALL_BUDGET`, `FMP_NEWS_DAILY_CALL_BUDGET`, `MEDIASTACK_NEWS_DAILY_CALL_BUDGET`
- `GDELT_GLOBAL_QUERY`, `ALPHA_VANTAGE_TOPICS`, `GLOBAL_NEWS_MAX_RECORDS`

Recommended delivery defaults:
- keep Telegram text-first: `TELEGRAM_SEND_CHARTS=false`
- use rich charts in email: `ENABLE_CHARTS=true`
- leave channel routing at `DELIVERY_CHANNEL=all` and override per-run with CLI flags
- for Phase 4 rollout, start with `ENABLE_LLM_EMAIL_RENDER=true` + `LLM_RENDER_SHADOW_MODE=true`
  and inspect `morning_email_llm_shadow` in terminal output before switching shadow off

### Phase 4 preflight checklist

Run this sequence before enabling live LLM email output:

```bash
# 0) sanity-check config and rollout mode
python -m app.cli preflight

# 1) baseline deterministic output
python -m app.cli --dry-run --show-output --email-only morning

# 2) turn on render layer in shadow mode
#    .env:
#    ENABLE_LLM_EMAIL_RENDER=true
#    LLM_RENDER_SHADOW_MODE=true
#    OPENAI_API_KEY=...
python -m app.cli --dry-run --show-output --email-only morning

# 3) review [EMAIL OUTPUT] morning_email_llm_shadow vs morning_email_preview
#    for 3-5 runs; only then move to live mode:
#    LLM_RENDER_SHADOW_MODE=false
python -m app.cli --show-output --email-only morning
```

General runtime settings are defined in:
- [app/settings.py](/Users/jack/briefly/app/settings.py)

### YAML config

- user profile:
  - [configs/user_profile.example.yaml](/Users/jack/briefly/configs/user_profile.example.yaml)
- watchlists:
  - [configs/watchlists.example.yaml](/Users/jack/briefly/configs/watchlists.example.yaml)
- schedules:
  - [configs/schedules.yaml](/Users/jack/briefly/configs/schedules.yaml)
- sectors:
  - [configs/sectors.yaml](/Users/jack/briefly/configs/sectors.yaml)
- alert thresholds:
  - [configs/alert_rules.yaml](/Users/jack/briefly/configs/alert_rules.yaml)
- holdings:
  - [configs/holdings.example.yaml](/Users/jack/briefly/configs/holdings.example.yaml)

### Phase 4.2 control-plane usage

Phase 4.2 keeps config defaults in YAML while allowing live profile overrides in SQLite.

Inspect active overrides:

```bash
python -m app.cli prefs-show --profile default_user
```

Set overrides:

```bash
# your target routing:
# - morning + intraday on email
# - morning + intraday + breaking on telegram
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.intraday_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.breaking_channels --value '["telegram"]'

# LLM controls (morning/weekend email only)
python -m app.cli prefs-set --profile default_user --key delivery.llm_email_morning --value true
python -m app.cli prefs-set --profile default_user --key delivery.llm_shadow_mode --value true

# hide one morning section
python -m app.cli prefs-set --profile default_user --key sections.morning.watchlist --value false

# override watchlist + sector weights without editing YAML
python -m app.cli prefs-set --profile default_user --key watchlist.primary --value '["NVDA","MSFT","TSLA"]'
python -m app.cli prefs-set --profile default_user --key sector.weights --value '{"technology": 1.3, "energy": 1.1}'
python -m app.cli prefs-set --profile default_user --key coverage.home_region --value "us"
python -m app.cli prefs-set --profile default_user --key coverage.weights --value '{"us": 1.2, "europe": 0.7, "asia": 0.5}'
```

Unset one key or clear all overrides:

```bash
python -m app.cli prefs-unset --profile default_user --key sections.morning.watchlist
python -m app.cli prefs-reset --profile default_user
```

Supported keys:
- `watchlist.primary`, `watchlist.secondary`, `watchlist.monitor`
- `sector.weights`
- `coverage.home_region`, `coverage.weights`
- `delivery.morning_channels`, `delivery.intraday_channels`, `delivery.breaking_channels`
- `delivery.morning_brief_time`, `delivery.hourly_updates`, `delivery.breaking_alerts`
- `delivery.llm_email_morning`, `delivery.llm_shadow_mode`
- `delivery.quiet_hours_start`, `delivery.quiet_hours_end`
- `sections.morning.market_setup`, `sections.morning.macro_context`, `sections.morning.top_themes`
- `sections.morning.portfolio_focus`, `sections.morning.sector_scan`, `sections.morning.watchlist`

### Phase 4.3 web control center usage

Start the local panel:

```bash
python -m app.cli web --host 127.0.0.1 --port 8080
```

Open:
- `http://127.0.0.1:8080/ui/settings?profile=default_user`
- API docs: `http://127.0.0.1:8080/api/docs`

Phase 4.3 HTTP surface:
- `GET /ui/settings`
- `GET /api/v1/profile/{profile}/state`
- `PUT /api/v1/profile/{profile}/preferences`
- `DELETE /api/v1/profile/{profile}/preferences/{pref_key}`
- `POST /api/v1/profile/{profile}/holdings/import`
- `GET /api/v1/followables/search`

Example bulk preference update:

```bash
curl -X PUT "http://127.0.0.1:8080/api/v1/profile/default_user/preferences" \
  -H "Content-Type: application/json" \
  -d '{"updates":{"delivery.morning_channels":["email"],"sections.morning.watchlist":false}}'
```

## Product architecture

```text
Briefly modules
  Market Briefing / Portfolio Control / Trading Lab (planned)

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
  TelegramFormatter -> deterministic EmailFormatter
  -> optional LLMEmailRenderer (shadow/live) -> Telegram / Email

Portfolio control plane
  CLI prefs + FastAPI/HTMX local panel -> preferences + holdings services

Persistence
  SQLite for sent messages, provider health, events, market snapshots,
  holdings, and user preference overrides
```

Key code areas:
- [app/main.py](/Users/jack/briefly/app/main.py)
- [app/processing/pipeline.py](/Users/jack/briefly/app/processing/pipeline.py)
- [app/processing/relevance_scoring.py](/Users/jack/briefly/app/processing/relevance_scoring.py)
- [app/briefing/formatter.py](/Users/jack/briefly/app/briefing/formatter.py)
- [app/portfolio/importer.py](/Users/jack/briefly/app/portfolio/importer.py)
- [app/portfolio/service.py](/Users/jack/briefly/app/portfolio/service.py)
- [app/personalization/preferences_service.py](/Users/jack/briefly/app/personalization/preferences_service.py)
- [app/web/app.py](/Users/jack/briefly/app/web/app.py)
- [app/web/control_plane_service.py](/Users/jack/briefly/app/web/control_plane_service.py)

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
python -m pytest app/tests/test_phase42_control_plane.py -q
python -m pytest app/tests/test_phase43_web_control_plane.py -q
```

The test suite currently covers:
- scoring and ranking
- formatting and weekend presentation
- scheduler window behavior
- holdings import/persistence/scoring
- profile control-plane overrides (Phase 4.2)
- web control center + control-plane API (Phase 4.3)
- manual `--show-output` mode
- market-data fallback behavior
- provider circuit breaker behavior
- LLM email render guardrails + shadow/live fallback behavior

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

### 5. Route channels by profile without editing `.env`

```bash
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["email"]'
python -m app.cli prefs-set --profile default_user --key delivery.intraday_channels --value '["telegram"]'
python -m app.cli prefs-set --profile default_user --key delivery.breaking_channels --value '["telegram"]'
python -m app.cli --show-output morning
```

### 6. Run the local control center

```bash
python -m app.cli web --host 127.0.0.1 --port 8080
```

Then open `http://127.0.0.1:8080/ui/settings?profile=default_user`.

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
- **Phase 4 preflight + render layer** is now in place
  - deterministic selection + optional LLM render for morning/weekend email
  - shadow mode rollout controls
  - strict validation against deterministic payload and safe fallback
- **Phase 4.2 control plane** is now in place
  - persisted profile-level overrides for channels, watchlists, sector weights, and section visibility
  - runtime merge of YAML defaults + DB overrides
  - CLI surface for inspect/set/unset/reset workflows
- **Phase 4.3 web control center** is now in place
  - FastAPI + HTMX local UI for holdings imports and profile preference management
  - control-plane API endpoints for state, updates, search, and upload flows
  - single-user localhost-first operations without a separate frontend build

The biggest remaining quality gap is still editorial/source quality in some surfaced headlines, not the core plumbing.

Recent improvement note:
- weekend side-angle headlines are now less likely to surface in `TOP THEMES` / `SECTOR SCAN`
- repeated stories are de-duplicated across morning sections in a deterministic order

## License

MIT
