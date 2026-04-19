# Briefly

Briefly is a local-first portfolio intelligence platform combining morning market briefings, intraday alerts, and a full institutional-grade portfolio workbench. It runs entirely on your machine with SQLite persistence and a FastAPI/HTMX control center, with no cloud dependency beyond market data APIs.

---

## What it does

**Market Briefing** — generates and delivers structured morning briefings, intraday updates, and breaking alerts via Telegram and email.

**Portfolio Workbench** — a local web control center for managing holdings, defining investor policy, setting strategic asset allocation, configuring benchmarks, computing live risk analytics, building forward-looking capital market assumptions, and decomposing active return with Brinson-Hood-Beebower attribution.

**Trading Lab** — planned. Future execution and sentiment workflow layer on top of the existing deterministic intelligence core.

---

## Feature overview

### Market briefing

- Morning briefing with market setup, macro context, geopolitics, top themes, sector scan, watchlist, and portfolio focus sections
- Expanded market setup panel: US, Europe, Asia, VIX, 10Y UST, WTI, gold levels with percentage moves
- Intraday updates: only new, material developments above threshold, compact global risk block when relevant
- Breaking alerts: deterministic classifier (breaking / high_priority / regular / ignore), storyline-key cooldown, one-shot follow-up state machine, flood control via rolling-hour cap
- Weekend-aware formatting when cash equity markets are closed
- Story deduplication and clustering across sections
- Cross-type anti-repeat policy: events already sent in one channel are suppressed from later surfaces

### Delivery and rendering

- Telegram and email delivery with per-channel, per-profile routing
- Optional LLM email renderer (OpenAI-compatible) with shadow mode for safe rollout, strict validation against deterministic payload, and automatic fallback
- Static PNG chart cards: Market Snapshot, Macro Risk Strip, Top Holdings Performance, Sector Exposure vs Performance, Event-Linked Trend
- HTML email with inline charts

### Portfolio workbench (Phases 4.7–5.8)

The web control center at `http://127.0.0.1:8080/ui/settings` exposes a full portfolio workbench:

**Holdings**
- Import from YAML or CSV, persist in SQLite, edit inline with +/- steppers and rebalance helpers
- Holdings-aware relevance scoring and briefing influence

**Portfolio Diagnostics**
- Coverage alignment findings, briefing influence map, health checks
- Holdings data quality diagnostics, snapshot confidence rating
- Deterministic stress scenarios: semis down 10%, rates +50 bps, oil shock, dollar spike, small-cap risk-off

**Policy (Phase 5.1)**
- IPS-style investor policy persisted in SQLite: return target, volatility cap, drawdown cap, single-name limit, liquidity minimum, equity maximum, governance cadence
- Policy breach detection: single-name cap, max-equity, liquidity-minimum, allocation-band violations

**Allocation (Phase 5.1)**
- Strategic asset allocation targets with min/max bands per asset class
- Actual vs target drift comparison, residual cash/liquidity handling, rebalance status per row

**Benchmark (Phase 5.1)**
- First-class benchmark configuration: market index, policy blend, or custom blend
- Stable reference layer for downstream risk and attribution

**Risk Analytics (Phase 5.2)**
- Live benchmark-relative risk metrics using yfinance price history
- Sharpe ratio, Sortino ratio, annualised volatility, maximum drawdown (portfolio and benchmark)
- Active return, tracking error, information ratio
- 30-day and 90-day rolling return series for sparkline display
- Configurable lookback (3 months to 3 years) and risk-free rate
- DB-cached return series and metric snapshots (BenchmarkPriceCache, PortfolioReturnSeries, RiskMetricsSnapshot): repeated page loads skip re-fetching
- Volatility and drawdown policy breach integration

**CMA Builder (Phase 5.3)**
- Per-asset-class capital market assumptions: expected return and volatility, stored in SQLite
- NxN correlation matrix configuration for asset-class pairs
- Expected portfolio return, volatility, and Sharpe computed from actual weights and CMA inputs
- SAA expected metrics computed from target weights for side-by-side comparison
- Policy gap: expected return vs policy target, expected volatility vs policy limit
- SAA gap: actual expected metrics vs SAA expected metrics, return shortfall, vol difference, Sharpe difference
- Expected return below policy target added as a policy-fit breach

**Rebalancing Engine (Phase 5.4)**
- Rebalancing configuration: drift-threshold, calendar, and hybrid trigger methods
- Drift-triggered trade list: per-asset-class buy/sell/hold direction with magnitude in percentage points, trimmed to the nearest band edge
- Priority ranking: high (outside band), medium (approaching threshold), low (minor drift)
- Holding-level drill-down: which symbols to trim or add within each asset class, surfaced as collapsible rows
- One-way turnover calculation and estimated transaction costs in basis points, with optional portfolio-value input for currency amounts
- Minimum trade size filter to suppress noise trades below a configurable threshold
- Proposal history log: append-only record of status, turnover, trigger type, and trade count
- Generate Proposal button for on-demand persistence; auto-computed fresh on every page load
- Tax-aware flag (v1: informational only, does not change trade mathematics)

**Attribution (Phase 5.5)**
- Brinson-Hood-Beebower attribution model using CMA expected returns as the return proxy
- Per-asset-class decomposition into allocation effect, selection effect (v1: zero, requires historical returns), and interaction effect (v1: zero)
- Allocation effect measures whether the portfolio was over/underweight in the right asset classes relative to the SAA benchmark
- Waterfall decomposition: Benchmark Return, Allocation Effect, Selection Effect, Interaction Effect, Portfolio Return
- Top contributor and top detractor identification by allocation effect magnitude
- Attribution history log: append-only record of active return and allocation effect per snapshot
- Qualitative summary identifying the main drivers of active return
- Explicit CMA-based method labelling and Phase 5.7 roadmap note for historical selection and interaction effects
- AttributionSnapshot model persists results in SQLite for trend analysis

**Workflow IA Refactor (Phase 5.6A)**
- Top-level UI split into `Market Briefing`, `Portfolio Workbench`, and `Audit`
- `Coverage`, `Delivery`, and `Morning Composition` grouped under Market Briefing rather than mixed into portfolio analytics
- Portfolio workbench presented as separated areas: overview, builder (holdings/policy/allocation/benchmark), diagnostics, risk, scenarios, implementation, and attribution
- User-facing copy removes primary-screen implementation plumbing language (advanced/raw details remain in Audit)
- Key semantic controls now use constrained dropdowns: investor type, base currency, rebalancing policy, governance frequency, and allocation role

**Visual Productization (Phase 5.6B)**
- New workspace gateway route at `/ui` with module entry cards for Market Briefing, Portfolio Workbench, and Audit
- Route-based workflow entry points: `/ui/briefing`, `/ui/portfolio`, `/ui/audit`
- Portfolio task routes for focused work screens: builder, diagnostics, risk, CMA, scenarios, implementation, attribution, policy, allocation, benchmark
- Settings shell accepts route-provided initial module/section so each deep link opens directly in the intended workspace area
- Workspace links are persistent in the shell for quick context switching without returning to one mixed scroll flow

**Personal Workspace Home (Phase 5.6C)**
- Home page now leads with personalized context instead of neutral routing copy
- Deterministic home state (`analysis.ui_home`) summarizes what matters now, next action bias, and status chips
- Action-forward hero includes primary CTAs: Open Portfolio, Edit Briefing, Review Risk
- Portfolio Workbench is intentionally dominant, Market Briefing remains secondary, and Audit is visually demoted to advanced controls
- Insight row now emphasizes momentum and continuation (recommended workspace and delivery health) over raw metadata

**Navigation Discipline & Page Focus (Phase 5.6D)**
- Portfolio root route (`/ui/portfolio`) now behaves as a summary dashboard only, instead of rendering all builder/risk/CMA/implementation sections inline
- Route-level section gating is server-side: each portfolio task route renders only its own focused surface
- Navigation hierarchy is disciplined: compact global nav + one workspace nav, with builder sub-tabs shown only inside Builder routes
- Briefing controls remain isolated to briefing routes; portfolio routes only show briefing impact outputs
- CMA assumptions now use canonical asset-class dropdowns in the default UI (no free-text asset-class key entry)
- UX readiness language now distinguishes configured vs partial/unavailable for policy/allocation presentation states

**Simulation & Validation Harness (Phase 5.7A)**
- Canonical test preset library for deterministic portfolio scenarios:
  concentrated AI growth, balanced 60/40, global multi-asset, healthcare concentration, Europe tilt, cash-heavy tactical, allocation drift, single-name breach, policy-incomplete, benchmark mismatch
- Validation runner (`python -m app.cli validation run --preset ...`) applies a preset, rebuilds state, and produces pass/fail checks across holdings, policy, allocation, CMA, rebalancing, and attribution readiness
- Parameter sweep runner (`validation sweep`) supports directional monotonic checks for top-holding concentration, cash-weight changes, and equity CMA expected-return sensitivity
- Randomized fuzz harness (`validation fuzz`) generates constrained random portfolios and checks state invariants for robustness
- Builder UI includes a **Load Test Preset** action for rapid scenario iteration from `/ui/portfolio/holdings`

**Simulation Lab (Phase 5.8)**
- Dedicated portfolio route: `/ui/portfolio/simulation?profile=default_user`
- Supports `portfolio` and `single stock` simulation modes
- Multi-method engine stack:
  - Monte Carlo (multivariate)
  - Historical simulation
  - Filtered historical simulation
  - Block bootstrap simulation
- Structured macro override controls: growth shock, inflation shock, rates shock, volatility regime, correlation stress
- Horizon/frequency controls with presets and custom periods
- Simulation run presets:
  - Fast: 500
  - Standard: 2,500
  - Deep: 10,000
- Interactive chart outputs:
  - Fan chart percentile cone (P05/P25/P50/P75/P95)
  - Terminal value distribution histogram
  - Drawdown distribution histogram
  - Sensitivity bar chart (growth-shock sweep)
- Deterministic scenario pack summary:
  recession, soft landing, inflation re-acceleration, rates up/down, oil shock, USD spike, AI capex boom
- Persisted simulation runs and saved presets in SQLite for repeatable comparisons

### Intelligence pipeline

- Finnhub + NewsAPI event processing with credibility scoring, personal relevance, and clustering
- Global geopolitics section with market-link gating (no broad world-news feed)
- Phase 4.5 optional sources: GDELT, Alpha Vantage News, FMP, Mediastack
- Per-provider daily call budgets, circuit breaker, and quote fallback to yfinance
- Section-level trust gating for Top Themes and Sector Scan

### Cadence and scheduling

- CadenceEngine enforces: one morning briefing per local day, one intraday update per pre-US-open window, breaking only above threshold
- US cash open computed from `09:30 America/New_York` with DST-safe IANA zone conversion
- NYSE holiday and half-day awareness
- Daily idempotency markers in SQLite prevent duplicate sends across process restarts

---

## Quick start

```bash
git clone https://github.com/jacksangster03/Briefly.git
cd Briefly

pip install -e ".[dev]"

cp .env.example .env
cp configs/user_profile.example.yaml configs/user_profile.yaml
cp configs/watchlists.example.yaml configs/watchlists.yaml

make setup
make test
```

Minimum required environment variables:

```bash
FINNHUB_API_KEY=
FRED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## Core commands

```bash
# Briefing runs
python -m app.cli morning
python -m app.cli intraday
python -m app.cli breaking
python -m app.cli scheduler

# Web control center
python -m app.cli web --host 127.0.0.1 --port 8080

# Database
python -m app.cli init-db
python -m app.cli status
python -m app.cli preflight

# Preferences
python -m app.cli prefs-show --profile default_user
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["telegram","email"]'
python -m app.cli prefs-unset --profile default_user --key delivery.morning_channels
python -m app.cli prefs-reset --profile default_user

# Data inspection
python -m app.cli quote NVDA
python -m app.cli news

# Phase 5.7A validation harness
python -m app.cli validation presets
python -m app.cli validation run --preset allocation_drift_case --profile default_user
python -m app.cli validation sweep --preset balanced_60_40 --dimension top_holding_pct --values 10,15,22,30
python -m app.cli validation fuzz --profile default_user --cases 25 --seed 42

# Phase 5.8 simulation lab
python -m app.cli simulation run --profile default_user --methods monte_carlo,historical --frequency monthly --horizon-periods 60 --simulation-count 2500
python -m app.cli simulation runs --profile default_user
```

### Dry-run and output inspection

```bash
python -m app.cli --dry-run morning
python -m app.cli --dry-run --show-output morning
python -m app.cli --dry-run --show-output --email-only morning
python -m app.cli --dry-run --show-output --telegram-only morning
```

### Holdings import

```bash
python -m app.cli import-holdings --file configs/holdings.example.yaml --profile default_user
```

Supported formats: YAML and CSV.

### Channel routing

```bash
# Route by run flag
python -m app.cli --email-only morning
python -m app.cli --telegram-only morning

# Route by profile preference
python -m app.cli prefs-set --profile default_user --key delivery.morning_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.intraday_channels --value '["telegram","email"]'
python -m app.cli prefs-set --profile default_user --key delivery.breaking_channels --value '["telegram"]'
```

---

## Web control center

Start:

```bash
python -m app.cli web --host 127.0.0.1 --port 8080
```

Open home: `http://127.0.0.1:8080/ui?profile=default_user`

Direct workspaces:
- `http://127.0.0.1:8080/ui/briefing?profile=default_user`
- `http://127.0.0.1:8080/ui/portfolio?profile=default_user`
- `http://127.0.0.1:8080/ui/audit?profile=default_user`

API docs: `http://127.0.0.1:8080/api/docs`

### API surface

```
GET    /ui
GET    /ui/settings
GET    /ui/briefing
GET    /ui/briefing/watchlists
GET    /ui/briefing/delivery
GET    /ui/briefing/morning
GET    /ui/portfolio
GET    /ui/portfolio/{view}
GET    /ui/audit
GET    /api/v1/profile/{profile}/state
PUT    /api/v1/profile/{profile}/preferences
DELETE /api/v1/profile/{profile}/preferences/{pref_key}
POST   /api/v1/profile/{profile}/holdings/import
GET    /api/v1/followables/search

PUT    /api/v1/profile/{profile}/policy
PUT    /api/v1/profile/{profile}/allocation
PUT    /api/v1/profile/{profile}/benchmark

GET    /api/v1/profile/{profile}/risk
POST   /api/v1/profile/{profile}/risk/refresh

GET    /api/v1/profile/{profile}/cma
PUT    /api/v1/profile/{profile}/cma
PUT    /api/v1/profile/{profile}/cma/correlations

GET    /api/v1/profile/{profile}/rebalancing
POST   /api/v1/profile/{profile}/rebalancing/generate
GET    /api/v1/profile/{profile}/rebalancing/history

GET    /api/v1/profile/{profile}/attribution
POST   /api/v1/profile/{profile}/attribution/generate
GET    /api/v1/profile/{profile}/attribution/history

POST   /api/v1/profile/{profile}/simulation/run
GET    /api/v1/profile/{profile}/simulation/runs
GET    /api/v1/profile/{profile}/simulation/runs/{run_id}
GET    /api/v1/profile/{profile}/simulation/presets
POST   /api/v1/profile/{profile}/simulation/presets
```

### Preferences reference

Supported preference keys:

```bash
watchlist.primary / watchlist.secondary / watchlist.monitor
sector.weights
coverage.home_region / coverage.weights
delivery.morning_channels / delivery.intraday_channels / delivery.breaking_channels
delivery.morning_brief_time / delivery.hourly_updates / delivery.breaking_alerts
delivery.llm_email_morning / delivery.llm_shadow_mode
delivery.quiet_hours_start / delivery.quiet_hours_end
sections.morning.market_setup / sections.morning.macro_context / sections.morning.top_themes
sections.morning.portfolio_focus / sections.morning.sector_scan / sections.morning.watchlist
risk.lookback_days / risk.risk_free_rate_pct
```

---

## Configuration

### Environment variables (`.env`)

```bash
# Required
FINNHUB_API_KEY=
FRED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional: news enrichment
NEWSAPI_KEY=
ALPHA_VANTAGE_API_KEY=
FMP_API_KEY=
MEDIASTACK_API_KEY=

# Optional: LLM email rendering
OPENAI_API_KEY=
ENABLE_LLM_EMAIL_RENDER=false
LLM_RENDER_SHADOW_MODE=true
LLM_EMAIL_MODEL=gpt-4o-mini
LLM_API_BASE_URL=
LLM_EMAIL_TIMEOUT_SECONDS=30
LLM_EMAIL_MAX_CHARS=8000
LLM_EMAIL_MIN_SOURCE_URLS=2

# Charts
ENABLE_CHARTS=true
TELEGRAM_SEND_CHARTS=false

# Delivery
DELIVERY_CHANNEL=all

# Web control center
WEB_HOST=127.0.0.1
WEB_PORT=8080

# Provider resilience
PROVIDER_TIMEOUT=10
PROVIDER_MAX_RETRIES=2

# Phase 4.5 optional sources
ENABLE_GDELT=false
ENABLE_ALPHA_VANTAGE_NEWS=false
ENABLE_FMP_NEWS=false
ENABLE_MEDIASTACK_NEWS=false
GDELT_DAILY_CALL_BUDGET=250
ALPHA_VANTAGE_NEWS_DAILY_CALL_BUDGET=20
FMP_NEWS_DAILY_CALL_BUDGET=120
MEDIASTACK_NEWS_DAILY_CALL_BUDGET=3
GLOBAL_NEWS_MAX_RECORDS=50
```

### YAML config files

```
configs/user_profile.example.yaml   — profile defaults
configs/watchlists.example.yaml     — watchlist definitions
configs/schedules.yaml              — cadence schedule
configs/sectors.yaml                — sector taxonomy
configs/alert_rules.yaml            — breaking alert thresholds
configs/holdings.example.yaml       — example holdings file
```

### LLM email rollout checklist

```bash
# 1. Baseline check
python -m app.cli --dry-run --show-output --email-only morning

# 2. Enable shadow mode (render but do not send LLM output)
#    Set in .env: ENABLE_LLM_EMAIL_RENDER=true, LLM_RENDER_SHADOW_MODE=true
python -m app.cli --dry-run --show-output --email-only morning

# 3. Review [EMAIL OUTPUT] morning_email_llm_shadow vs morning_email_preview
#    Run 3-5 times, then switch shadow off
#    Set: LLM_RENDER_SHADOW_MODE=false
python -m app.cli --show-output --email-only morning
```

---

## Architecture

```
Briefly
  Home | Market Briefing | Portfolio Workbench | Trading Lab (planned) | Audit

Providers
  Finnhub | NewsAPI | FRED | SEC EDGAR | yfinance
  Optional: GDELT | Alpha Vantage News | FMP | Mediastack

Intelligence pipeline
  cleaners -> ticker resolution -> sector enrichment -> dedupe
  -> credibility -> personal relevance -> clustering -> scoring

Briefing generation
  morning | intraday | breaking

Formatting and delivery
  TelegramFormatter
  -> deterministic EmailFormatter
  -> optional LLMEmailRenderer (shadow/live)
  -> Telegram | Email

Portfolio workbench layers
  Holdings -> Policy -> Allocation -> Benchmark
  -> Risk Analytics (Phase 5.2)
  -> CMA Builder (Phase 5.3)
  -> Rebalancing Engine (Phase 5.4)
  -> Attribution (Phase 5.5)
  -> Simulation Lab (Phase 5.8)

Portfolio analyzer
  build_portfolio_analysis()
    policy_fit | allocation_drift | benchmark_summary
    risk_analytics | cma_analytics | rebalance_proposal | attribution
    scenario_stress | briefing_influence | health_checks

Persistence (SQLite)
  Events | SentMessages | MarketSnapshots | Holdings
  UserPreferences | InvestorPolicy | StrategicAllocationTarget
  BenchmarkConfig | PortfolioReturnSeries | BenchmarkPriceCache
  RiskMetricsSnapshot | CMAEntry | CMACorrelation
  RebalancingConfig | RebalanceProposal | AttributionSnapshot
  SimulationRun | SimulationResult | SimulationPreset
```

---

## Testing

```bash
make test
# or
python -m pytest app/tests -q
```

Current count: **283 tests, 0 failures.**

Focused test runs:

```bash
python -m pytest app/tests/test_phase43_web_control_plane.py -q
python -m pytest app/tests/test_phase52_risk_analytics.py -q
python -m pytest app/tests/test_phase53_cma_analytics.py -q
python -m pytest app/tests/test_phase54_rebalancing.py -q
python -m pytest app/tests/test_phase55_attribution.py -q
python -m pytest app/tests/test_phase57_validation_runner.py -q
python -m pytest app/tests/test_phase57_sweeps.py -q
python -m pytest app/tests/test_phase57_fuzz.py -q
python -m pytest app/tests/test_phase58_simulation_service.py -q
python -m pytest app/tests/test_phase58_simulation_api.py -q
python -m pytest app/tests/test_portfolio_phase3.py -q
python -m pytest app/tests/test_market_data.py -q
python -m pytest app/tests/test_circuit_breaker.py -q
```

Test coverage includes:

- Scoring and ranking
- Formatting and weekend presentation
- Scheduler window and cadence behavior
- Holdings import, persistence, and scoring
- Profile control-plane overrides
- Web control center and API endpoints
- Policy, allocation, and benchmark persistence and breach logic
- Risk analytics: Sharpe, Sortino, max drawdown, tracking error, information ratio, cache behaviour
- CMA service: expected return/vol/Sharpe computation, correlation matrix, policy gap, SAA gap
- Rebalancing engine: drift detection, trade list generation, turnover, cost estimation, proposal persistence
- Attribution: Brinson-Hood-Beebower allocation effect, selection/interaction effect (zero in v1), waterfall, persistence
- Validation harness: canonical presets, golden checks, parameter sweeps, and randomized invariant testing
- Simulation lab: multi-method forward/risk engine, macro override controls, persisted runs/presets, and interactive charts
- LLM email render guardrails, shadow mode, and fallback behaviour
- Provider circuit breaker and quote fallback

---

## Typical workflows

### Preview a morning briefing locally

```bash
python -m app.cli --show-output --dry-run morning
```

### Import holdings and check portfolio-aware briefing

```bash
python -m app.cli import-holdings --file configs/holdings.example.yaml --profile default_user
python -m app.cli --show-output --dry-run morning
```

### Open the portfolio workbench

```bash
python -m app.cli web --host 127.0.0.1 --port 8080
# then open http://127.0.0.1:8080/ui?profile=default_user
```

Top-level modules: Market Briefing, Portfolio Workbench, Audit.
Phases 5.6B–5.6D add route-based workspace entry, personalized home context, and disciplined page-level separation.

### Run the live scheduler

```bash
python -m app.cli scheduler
```

### Update risk analytics configuration

In the Risk tab, set lookback period and risk-free rate, then click Refresh Metrics. Or via API:

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/profile/default_user/risk/refresh"
```

### Configure capital market assumptions

In the CMA tab, enter expected return and volatility for each asset class, configure the correlation matrix, then save. The Expected Portfolio Analytics section updates immediately.

### Run attribution analysis

In the Attribution tab, click Generate and Save Attribution Snapshot. Or via API:

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/profile/default_user/attribution/generate"
```

The attribution block is also auto-computed (without persistence) on every page load alongside the rebalance proposal.

### Run 5.7A validation workflows

```bash
# List canonical test cases
python -m app.cli validation presets

# Run a deterministic golden-case validation
python -m app.cli validation run --preset single_name_breach_case --profile default_user

# Run a parameter sensitivity sweep
python -m app.cli validation sweep \
  --preset balanced_60_40 \
  --dimension top_holding_pct \
  --values 10,15,22,30

# Run constrained randomized robustness checks
python -m app.cli validation fuzz --profile default_user --cases 50 --seed 7
```

### Run 5.8 simulation workflows

```bash
# Run a standard 5-year monthly simulation on current profile holdings
python -m app.cli simulation run \
  --profile default_user \
  --methods monte_carlo,historical,bootstrap \
  --frequency monthly \
  --horizon-periods 60 \
  --simulation-count 2500 \
  --assumption-source historical \
  --benchmark ACWI

# List recent persisted simulation runs
python -m app.cli simulation runs --profile default_user
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| 2 | Complete | Formatting, trust, weekend mode, breaking schedule |
| 3 | Complete | Holdings import, portfolio-aware briefings, operational tools |
| 4 | Complete | LLM email rendering, web control center, cadence engine, global intelligence |
| 5.1 | Complete | Policy, allocation, and benchmark foundation |
| 5.2 | Complete | Risk and benchmark analytics |
| 5.3 | Complete | CMA builder and expected portfolio analytics |
| 5.4 | Complete | Rebalancing and implementation engine |
| 5.5 | Complete | Attribution: Brinson-Hood-Beebower model, CMA-based allocation effect, waterfall decomposition |
| 5.6A | Complete | Workflow architecture split: Market Briefing vs Portfolio Workbench vs Audit, with separated workbench areas and constrained semantic inputs |
| 5.6B | Complete | Visual productization pass: workspace home route, route-based module entry points, and focused portfolio/briefing deep links |
| 5.6C | Complete | Personalized home workspace: what-matters-now hero, deterministic home summaries, prioritized workflow actions, and secondary audit treatment |
| 5.6D | Complete | Navigation discipline and page focus: portfolio root as dashboard-only, builder isolation, route-level section gating, and canonical CMA asset-class UI controls |
| 5.7A | Complete | Portfolio simulation and validation harness: canonical presets, golden checks, parameter sweeps, fuzz tests, and Builder preset loading |
| 5.8 | Complete | Simulation Lab: multi-method simulation engine, macro overrides, interactive charts, persisted runs/presets, and route/API integration |
| 5.7B | Planned | Historical selection and interaction effects using holding-level daily return series |

---

## License

MIT
