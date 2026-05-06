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

- Session-based briefing system (Europe/Madrid): Morning, Europe Midday, US Pre-Open, US Intraday Risk Check, Into Close, Closing Wrap
- `python -m app.cli brief` auto-routes to the correct current session
- `python -m app.cli morning` outside morning window auto-routes by default; `--force-morning` keeps true morning format
- Morning briefing with market setup, macro context, geopolitics, top themes, sector scan, watchlist, and portfolio focus sections
- Expanded market setup panel: US, Europe, Asia, VIX, 10Y UST, WTI, gold levels with percentage moves
- Deterministic **Setup read** paragraph after Market Setup to explain the session regime using breadth, volatility, rates, and commodity impulses
- Intraday updates: only new, material developments above threshold, compact global risk block when relevant
- Session-aware materiality gating (`suppress` / `hold` / `send` / `breaking`) with reasons and next-eligible-session logging
- Snapshot memory for **What Changed** across compatible sessions
- Required regime-tag chart coverage checks with selected/suppressed chart logging
- Breaking alerts: deterministic classifier (breaking / high_priority / regular / ignore), storyline-key cooldown, one-shot follow-up state machine, flood control via rolling-hour cap
- Weekend-aware formatting when cash equity markets are closed
- Story deduplication and clustering across sections
- Cross-type anti-repeat policy: events already sent in one channel are suppressed from later surfaces
- Manual QA harness: `day-replay` command simulates session cadence for a day without touching live idempotency markers
- Replay safety + clarity: replay/test messages are explicitly labeled `REPLAY MODE` and `TEST DAY REPLAY - NOT LIVE`, with simulated session time vs data-as-of timestamp
- Replay provider caching: per-run memoization reduces duplicate provider calls across replayed session slots

### Delivery and rendering

- Telegram and email delivery with per-channel, per-profile routing
- Explicit channel outcome reporting in morning runs (`sent / skipped / failed`) with skip/failure reasons in logs
- Optional LLM email renderer (OpenAI-compatible) with shadow mode for safe rollout, strict validation against deterministic payload, and automatic fallback
- Deterministic morning visuals engine with chart contract + promotion policy (LLM does not choose chart types/series/scales)
- Email PNG cards generated from deterministic specs (Plotly web preview + Matplotlib email rendering)
- HTML email with inline charts
- Optional Healthcare / Biotech Intelligence vertical section (default off; adds high-signal pharma/biotech/regulatory/manufacturing catalysts when enabled)
- Healthcare classifier hard-anchor gating (regulatory/clinical/company/ticker anchors) suppresses generic AI/power/radiology commentary from healthcare output

### Latest completed phases

- **Phase 6/7:** session-aware decision layer, regime-to-chart-stack selection, desk/full density modes, and new cards (yield curve, VIX risk, geo confirmation, regional divergence, watchlist movers, setup confirmation, what changed)
- **Phase 8:** routing/materiality/snapshot hardening, session-aware logs and metadata, required chart coverage enforcement, and provider-note cleanup
- **Phase 8.5 polish:** day-replay banner semantics, replay action/status summary, snapshot missing-value safety (no fake 0.00s), session-shaped output tightening, canonical Brent propagation, and chart-copy consistency fixes
- **Phase 8.6 QA polish:** deterministic watchlist move color scaling (magnitude-aware red/green intensity), stricter healthcare hard-anchor suppression (blocks generic AI/power overmatch), canonical commodity consistency lints across chart specs, improved all-negative P&L wording, and replay end-summary counters (generated/sent/suppressed/held/future + provider calls made/avoided + healthcare include/suppress counts)
- **Healthcare vertical (optional):** deterministic healthcare taxonomy/classifier/scorer/section-builder, Telegram+email rendering, breaking biotech label path, and configurable preferences in `configs/healthcare.yaml`
- **Phase 8.8 (historical backfill labelling):** every session sent for a past date carries a prominent `HISTORICAL BACKFILL - NOT LIVE` banner in Telegram and an amber HTML div + `[BACKFILL]` email subject prefix. Banner timestamps reflect the latest plausible send time within each session window (window end minus one minute), not the actual run time. Future dates are rejected. Idempotency keys to the original session date so re-runs never double-send.
- **Phase 8.9 Lite (automatic live session snapshots):** scheduler-triggered sessions are archived in SQLite (one row per session per day). Each row stores Telegram text, email subject and body, compact market/macro/portfolio/chart summaries, delivery status, and timestamp. CLI commands: `snapshots list`, `snapshots show`, `snapshots prune`. Backfills, dry runs, manual sends, and replay are excluded from storage. Default 30-day retention with automatic pruning after each scheduler run.
- **Phase 9.1 (startup catch-up):** on `start_scheduler()`, after acquiring the process lock, Briefly automatically sends any sessions that elapsed today before the scheduler started. Uses the same idempotency infrastructure as `catch-up` so already-sent sessions are never re-delivered. Skipped on weekends, dry-run mode, and at startup before any session window has opened.
- **Phase 9.2 (delivery failure alerting):** when the scheduler fails to deliver a session to any configured channel, a Telegram self-alert is sent immediately. The alert names the session, date, failed channel, and error, and includes a ready-to-paste CLI resend command. A 90-minute per-session-per-day cooldown (tracked via `SentMessage`) prevents alert spam during repeated scheduler retries on a broken channel. Alerts are non-blocking and fully skipped for backfills, dry runs, and non-scheduler sends.
- **Phase 9.3 (retry/backoff on transient delivery failures):** Telegram and email channels each retry independently before marking a send as failed. Default policy: up to 3 attempts, waiting 10 seconds after attempt 1 and 30 seconds after attempt 2. The MIME message is built once; only the SMTP connection is retried for email. Telegram retries at the per-message level so earlier messages in a multi-part brief are never duplicated. Dry-run and unconfigured channels bypass retry entirely. Phase 9.2 failure alerts fire only after all retries are exhausted. Retry logic lives in `app/messaging/retry.py` and is wired into `BaseMessenger.send_messages` and `EmailMessenger.send_rich`.
- **Phase 9.5 (LLM cost tracking):** every live LLM API call is persisted to a `llm_usage_logs` SQLite table with prompt tokens, completion tokens, model name, render mode (live/shadow/fallback), session key, local date, and estimated cost in USD (when per-1M token rates are configured in settings). The `LLMEmailRenderer.render_morning()` call is instrumented at all three outcome paths: live, shadow, and validation-fallback. API errors that prevent any response are not logged. Two CLI commands are added: `python -m app.cli llm-usage summary [--days N]` aggregates by date and model; `python -m app.cli llm-usage list [--days N] [--limit N]` shows individual calls. All logging is non-blocking — a DB write failure never interrupts the briefing pipeline. Cost rates default to zero (no cost estimation) and are configured via `llm_email_input_cost_per_1m_tokens` and `llm_email_output_cost_per_1m_tokens`.
- **Phase 9.6 (LLM cost controls):** adds a monthly spend budget (`llm_monthly_budget_usd`, default 0 = no limit) and a `query_monthly_spend()` function that aggregates estimated cost for the current calendar month from `llm_usage_logs`. When a budget is set and the current month's spend meets or exceeds it, `render_morning()` returns `mode="budget_exceeded"` and falls back to the deterministic email without making an API call. The budget gate is skipped when cost rates are not configured (spend would be None) or when the budget is zero. The `llm-usage summary` CLI command is extended to show the current month's spend, budget, percentage used, and remaining headroom.
- **Phase 9.4 (briefing history UI):** `/ui/briefing/history` is a standalone page in the web control center for browsing the session archive. A date picker and prev/next weekday navigation select the day. Six session cards (Morning, Europe Midday, US Pre-Open, US Intraday Risk Check, Into Close, Closing Wrap) show delivery status chips (sent/failed/skipped/missing) and channel sub-chips. Clicking a card loads an HTMX detail pane with four tabs: Telegram text, email plain text, email HTML (rendered in a sandboxed iframe), and metadata (delivery channels, channel status, content counts, timestamps). All data is read directly from SQLite; no provider calls are made. A "Briefing History" nav card is added to the briefing home section.
- **Phase 9.7 (always-on deployment profile):** full deployment guide at [`docs/deployment.md`](docs/deployment.md) covering Docker (recommended for VPS), Linux systemd, and macOS launchd. Includes a tiered environment variable checklist (required, recommended, optional providers), persistent volume layout for SQLite and configs, SQLite hot-backup strategy with a cron template, restart policy comparison table, and a clear caveat that a local Mac cannot send briefings while asleep or powered off. New files: `docs/deployment.md`, `scripts/briefly.service` (systemd unit template), `backups/` directory. Dockerfile gains layer-cache ordering and a `HEALTHCHECK`. `docker-compose.yml` gains a `briefly-web` profile service and a `backups/` volume mount.

### Automatic live session snapshots

Briefly stores a lightweight archive of every session briefing that the scheduler sends automatically. Each snapshot captures exactly what was generated and delivered: Telegram text, email subject and body, compact market/macro/portfolio summaries, chart selection, delivery status per channel, and timestamp.

- Only live scheduler sessions are stored. Backfills, dry runs, day-replay, manual `session-send`, and forced resends are excluded.
- One row per session per day in SQLite. No cloud backend, no external writes.
- Default retention: 30 days. Old snapshots are deleted automatically after each scheduler run.
- Snapshots are not historical reconstruction: they record what was generated at send time using the data available then.
- The archive starts accumulating from the moment you enable the scheduler.

```bash
# List what was stored for a date
python -m app.cli snapshots list --date yesterday
python -m app.cli snapshots list --date 2026-05-05

# Read the exact content of a stored session
python -m app.cli snapshots show --date yesterday --session morning
python -m app.cli snapshots show --date yesterday --session us_pre_open --format email-text
python -m app.cli snapshots show --date yesterday --session closing_wrap --format summary

# Available formats: telegram | email-text | email-html | summary
# Manual prune
python -m app.cli snapshots prune --retention-days 30
python -m app.cli snapshots prune --retention-days 14
```

Snapshot preferences (override via `prefs-set`):

```bash
python -m app.cli prefs-set --key snapshots.enabled --value true
python -m app.cli prefs-set --key snapshots.retention_days --value 30
python -m app.cli prefs-set --key snapshots.store_email_html --value true
python -m app.cli prefs-set --key snapshots.store_failed_attempts --value true
```

### Day Replay quickstart (manual QA)

- Dry run summary only:
  - `python -m app.cli day-replay --date today`
- Print full rendered session outputs:
  - `python -m app.cli --show-output day-replay --date today --force-all --ignore-materiality`
- Send test-labeled replay messages to channels:
  - `python -m app.cli day-replay --date today --send-test telegram,email --force-all --ignore-materiality`

Notes:
- `day-replay` is manual only (never scheduler-driven).
- Replay sends are test-labeled and do **not** update live sent-message idempotency markers.

### Catch-up vs backfill

| Scenario | Command | Banner |
|---|---|---|
| Sessions missed earlier today | `catch-up` (no `--date` or `--date today`) | None |
| Sessions missed yesterday, run before midnight | `catch-up --date yesterday` | HISTORICAL BACKFILL - NOT LIVE |
| Sessions missed from any past date | `backfill --date YYYY-MM-DD` | HISTORICAL BACKFILL - NOT LIVE |

`catch-up` without a past date sends today's missed sessions cleanly, with no label. Targeting a past date (via `catch-up --date yesterday` or the explicit `backfill` alias) automatically prepends a prominent banner to every Telegram message and email so recipients can see the content was generated after the fact using the latest available provider data, not the original session-time snapshot.

Idempotency uses the original session date so you can safely re-run without double-sending. Future dates are rejected with an error.

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
- Navigation hierarchy is strict and minimal:
  - Home is the only workspace switcher
  - Workspace roots route into focused subpages
  - Subpages expose only `← Workspace` and `⌂ Home`
  - No cross-workspace tab bars on subpages
- Briefing is fully split into root + subpages:
  - `/ui/briefing` (summary root)
  - `/ui/briefing/watchlists`
  - `/ui/briefing/delivery`
  - `/ui/briefing/morning`
- Portfolio remains root + focused subpages, including:
  - `/ui/portfolio/history`
  - `/ui/portfolio/rebalancing` (alias of implementation route)
- Audit is now a full workspace with root + subpages:
  - `/ui/audit` (summary root)
  - `/ui/audit/history`
  - `/ui/audit/overrides`
  - `/ui/audit/logs`
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
  - Fan chart percentile cone with shaded 5–95 and 25–75 bands
  - Terminal value distribution histogram with summary reference lines
  - Drawdown distribution histogram (max drawdown %)
  - Sensitivity growth chart (median terminal + probability of loss)
- Chart rendering contract now uses `chart_data` with explicit states per panel:
  - `available: true` -> render Plotly chart
  - `available: false` + `reason` -> show explanatory unavailable state
  - runtime render failure -> non-crashing error state while numeric summary remains visible
- Charts auto-render on:
  - initial page load
  - HTMX simulation result swaps
  - HTMX settle events
- Simulation chart containers:
  - `simulation-fan-chart`
  - `simulation-terminal-distribution`
  - `simulation-drawdown-distribution`
  - `simulation-sensitivity-growth`
- Deterministic scenario pack summary:
  recession, soft landing, inflation re-acceleration, rates up/down, oil shock, USD spike, AI capex boom
- Persisted simulation runs and saved presets in SQLite for repeatable comparisons

**Fixed Income Analytics (Phase 7A)**
- Bond analytics for all holdings with `fixed_income` bucket or known bond ETF symbols (BND, AGG, TLT, LQD, HYG, and 20+ more)
- Weighted modified duration, portfolio YTM, credit quality distribution (govt / IG / HY / EM), maturity ladder, and rate sensitivity (estimated P&L for +100 bps parallel shift)
- Reference data for 26 known bond ETFs with manual per-holding override capability via `BondHoldingOverride` table
- Quality fallbacks by credit bucket when ETF-specific data is unavailable
- Append-only `BondPortfolioSnapshot` log for trend tracking
- Dedicated route: `/ui/portfolio/bonds`
- Full HTMX override form: symbol, maturity date, coupon, YTM override, credit quality; active overrides displayed with delete actions

**PDF Portfolio Reports (Phase 7B)**
- On-demand A4 PDF generation using fpdf2 (pure Python, no system dependencies)
- User-selectable sections: Cover, Holdings, Risk, Attribution, Bonds, Scenarios, CMA
- Cover page with profile, date, benchmark, and period; section pages with KPI rows and data tables
- Reports persisted in `data/reports/{profile}/` with `GeneratedReport` DB record (title, sections, file size, timestamp)
- Soft-delete with file removal; download via streaming `FileResponse`
- Dedicated route: `/ui/portfolio/reports`
- HTMX generate form with section checkboxes and title input; recent reports list with download links

**ESG / SRI Scoring (Phase 7C)**
- Weighted portfolio ESG score (Overall, Environment, Social, Governance) via yfinance `.sustainability`
- Sector-based fallback scores for 10 GICS sectors when yfinance returns no data
- Hardcoded SRI exclusion screens: tobacco, weapons, thermal coal, gambling, adult content, fossil fuels
- Per-holding exclusion flag detection by symbol and sector; flagged rows highlighted in the UI
- SRI alignment labels: Strong / Partial / Weak / Insufficient Data
- Coverage percentage: share of portfolio weight with actual ESG data
- ESG scores cached daily in `ESGScore` table with upsert-on-refresh
- Append-only `PortfolioESGSnapshot` log; per-profile `ESGConfig` for active screen selection
- Dedicated route: `/ui/portfolio/esg`
- HTMX screening config form with per-screen checkboxes; Refresh button triggers force-refresh with live yfinance pull

**Multi-Currency Portfolio Support (Phase 7D)**
- Automatic listing-currency inference from ticker suffix (`.L` = GBP, `.PA`/`.DE` = EUR, `.TO` = CAD, `.T` = JPY, etc.)
- Per-currency exposure breakdown with home/foreign classification
- FX rates fetched from yfinance and cached daily in `FXRate` table; stale fallback to DB if live fetch fails
- Hedge recommendations under partial or full hedge policy: per-currency exposure, recommended hedge percentage, and suggested instrument
- Per-profile `FXConfig` (home currency + hedge policy); `CurrencyExposure` rows updated on each refresh
- Supported home currencies: USD, EUR, GBP, CHF, CAD, AUD, JPY, HKD, CNY
- Dedicated route: `/ui/portfolio/fx`
- HTMX config form with home currency and hedge policy dropdowns; Refresh FX Rates button triggers force-refresh

**Easy Setup Onboarding (Phase 5.9)**
- New novice-first portfolio onboarding route: `/ui/portfolio/easy-setup?profile=...`
- Three-step guided flow:
  - Step 1: Profile basics (investor type, horizon, risk comfort, currency, region, optional holdings upload)
  - Step 2: Rough portfolio shape (mix preset or infer-from-holdings + simple risk preference toggles)
  - Step 3: Review and apply defaults (rebalancing mode and deterministic preview)
- Deterministic mapping engine auto-fills:
  - Investor Policy
  - Strategic Allocation targets/bands
  - Benchmark config
  - CMA entries + correlation pairs
  - Risk preferences (`risk.lookback_days`, `risk.risk_free_rate_pct`)
  - Rebalancing configuration
- Home (`/ui`) now detects unconfigured portfolio profiles and surfaces a prominent `Quick portfolio setup (recommended)` CTA
- Advanced subpages remain fully editable; easy setup is optional and re-runnable

**Beginner Comprehension Layer (Phase 6.0A)**
- Central glossary registry (`metadata.ui_glossary`) for complex financial concepts
- Reusable question-mark help badge component with hover/focus tooltip behavior
- Applied to high-jargon portfolio routes: Overview, Risk, CMA, Rebalancing, Attribution, and Simulation Lab
- Tooltip copy is intentionally short (definition + why-it-matters) and keyboard/mobile accessible through `aria-describedby` + focus states

**Regional Intelligence Board (Phase 6.0B)**
- Briefing workspace root now includes a deterministic **Regional Intelligence Board** designed for market-first geographic orientation
- Structured region blocks: US, Europe, China, Rest of Asia, Middle East, Russia/Ukraine, Latin America, and Cross-Asset Spillovers
- Each region card includes:
  - share of configured region emphasis
  - status label (Lead / Active / Monitor / Underweight / Needs Attention)
  - concise focus thesis
  - portfolio lens text linking regional developments back to likely holdings/sector implications
- Region board is derived from saved coverage weights + home-region context, preserving deterministic behavior and local-first operation

**Briefing Clarity + Delivery Trust (Phase 6.1)**
- Symbol normalization for user-facing market setup copy (provider symbols like `^GSPC` stay internal by default)
- Deterministic market-setup interpretation engine wired into both Telegram and Email briefing outputs
- Improved global-news relevance notes with event-class-specific phrasing and in-brief deduplication
- Audit logs now include recent delivery outcomes by message type + channel using persisted `SentMessage` records
- Email failure troubleshooting is surfaced directly in morning-run logs with concrete next-step commands

**Briefing Signal Upgrade (Phase 6.2)**
- Setup-read v2 now explicitly calls out US/Europe/Asia direction, volatility regime (VIX level + direction), rates/curve impulse, and commodity impulse
- Net takeaway sentence links market setup to likely session driver (including geopolitics/energy when relevant)
- Earnings Calendar is now grouped by **Today / Tomorrow / This Week** with friendlier `Company (TICKER)` labels
- Earnings entries include deterministic relevance tags (`[portfolio]`, `[watchlist]`) when symbols overlap with the active profile
- Top Themes ranking now favors holdings/watchlist relevance and de-emphasizes low-signal filing stubs when not portfolio-linked
- Watchlist section adds a compact mover/catalyst summary line before the detailed list

**Regional Narrative + Portfolio Impact (Phase 6.3)**
- Morning briefing now includes a deterministic **Regional Lens** section (US, Europe, Asia, plus conditional geopolitical spillover regions)
- Added **Portfolio Impact Today** section with posture + top impact bullets derived from setup tags, holdings overlap, and briefing drivers
- Added **Regime Context** + **Positioning Alignment** lines to frame continuation/divergence versus recent risk snapshots
- Regional and impact blocks are rendered in both Telegram and Email outputs using concise, scan-friendly formatting
- Briefing workspace root now includes a portfolio-impact preview strip beneath the regional board
- Global-news “why market-relevant” now includes FX-stress templates to reduce repetition and improve channel specificity

**Deterministic Morning Visuals Engine (Phase 6.4)**
- Added deterministic morning chart subsystem and contract:
  - `chart_key`, `variant`, `available`, `priority`, `reason_if_hidden`, `series`, `annotations`, `meta`, `email_dimensions`
- Added rule-based regime tags + chart promotion policy (`hero`, `support`, optional portfolio/event, plus always-on microcards when available)
- Replaced bar-first core cards with deterministic specs:
  - `global_relative_performance` (rebased multi-line leadership panel)
  - `cross_asset_impulse_strip` (centered impulse strip)
  - `holdings_excess_performance` (ranked excess-move panel)
  - `sector_exposure_quadrant` (exposure vs move scatter)
  - `event_linked_annotated_trend` (annotated trend view)
- Added expanded finance-style chart families:
  - `breadth_leadership_panel`
  - `rates_curve_micro_panel`
  - `volatility_regime_card`
  - `portfolio_concentration_risk_card`
  - `earnings_relevance_strip`
- `MorningBriefing` now carries:
  - `morning_chart_bundle`
  - `morning_chart_selection`
- New chart preview surfaces:
  - `GET /ui/briefing/morning/charts?profile=...`
  - `GET /api/v1/profile/{profile}/briefing/morning/charts`
- Outlook-focused email shell was tightened to a denser desk-note style:
  - continuous dark navy briefing canvas (`#071629` / `#0B1D30`) with a hard-edged orange top rule and thin section dividers
  - Outlook-safe table structure, inline styles, system fonts only, and no reliance on rounded cards, shadows, flexbox, grid, or web fonts
  - compact 680px desk-note width, dense 10-20px typography, 1.3-1.4 line heights, and a top metadata strip for profile/freshness/regime state
  - top desk-read block with `Dominant driver` and `Setup read` before charts; mixed days receive an explicit no-single-driver fallback
  - role-aware chart hierarchy (hero/support/micro) with stable inline-image CID assets and a `READ` line above every chart image
- Morning email chart renderer now uses a stricter terminal palette and finance-style annotations:
  - dark integrated plot backgrounds, 2x Matplotlib rendering, tighter tick density, endpoint/value boxes, and semantic orange/teal/blue/red moves
  - global leadership is capped to 7 series for readability, cross-asset impulses are asset-class ordered, and chart labels are collision-aware
  - upgraded global leadership, cross-asset impulse, holdings dumbbell, sector quadrant, event trend, and terminal-style microcard visuals
- Chart promotion keeps a richer deterministic stack when data is available:
  - hero chart + support charts + portfolio/event charts + microcards, targeting 6+ visuals without changing the LLM boundary
- LLM boundary tightened: renderer receives deterministic chart summaries/tags for prose only and cannot control chart design decisions

**Signal Hygiene Upgrade (Phase 6.5)**
- Added deterministic **dominant tape driver** detection in market setup interpretation (geo-energy shock, rates repricing, and mega-cap earnings cluster paths)
- Global-news selector now hard-filters low-quality preview/listicle/SEO headlines when they lack a real catalyst
- Watchlist catalyst line now prefers dominant-driver context over low-signal single-note headlines
- Earnings section now suppresses low-value microcap blocks when there is no portfolio/watchlist overlap (`No portfolio-relevant earnings this week.`)
- Morning chart renderer moved to a darker desk-note palette to reduce “template dashboard” feel in email chart cards

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
- Per-session, per-channel idempotency via `SessionSendState` table: duplicate sends blocked, different sessions never block each other
- Session delivery claims are process-safe with 30-minute stale takeover for crash recovery
- Three delivery modes: `quiet` (morning only), `default` (morning + US pre-open), `active` (all six sessions)
- `suppress_low_materiality` gates non-morning sessions by materiality score; disable for full cadence delivery
- `schedule-status` command shows lock state, current/next session, preferences, and per-channel send history at a glance
- Manual catch-up command sends all missed sessions for today without resending already-delivered ones
- `session-send` command bypasses clock routing for any specific session; `--force` ignores idempotency for a true resend

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
python -m app.cli brief
python -m app.cli intraday
python -m app.cli midday
python -m app.cli preopen
python -m app.cli close
python -m app.cli breaking
python -m app.cli scheduler

# Session delivery (bypass clock routing, use idempotency)
python -m app.cli session-send --session europe_midday --send telegram,email
python -m app.cli session-send --session us_pre_open --send telegram,email
python -m app.cli session-send --session us_intraday_risk --send telegram,email
python -m app.cli session-send --session into_close --send telegram,email
python -m app.cli session-send --session closing_wrap --send telegram,email
python -m app.cli session-send --session morning --force          # resend even if already sent

# Catch-up: send all missed sessions for today (no backfill label)
python -m app.cli catch-up --send telegram,email
python -m app.cli catch-up --active-mode --ignore-materiality --send telegram,email
python -m app.cli catch-up --force-all --send telegram,email

# Catch-up for a past date: sends all sessions with a HISTORICAL BACKFILL banner
python -m app.cli catch-up --date yesterday --send telegram,email
python -m app.cli catch-up --date 2026-05-05 --active-mode --ignore-materiality --send telegram,email

# Backfill (alias): always requires --date, always applies the backfill banner
python -m app.cli backfill --date yesterday --send telegram,email
python -m app.cli backfill --date 2026-05-05 --active-mode --ignore-materiality

# Scheduler diagnostics
python -m app.cli schedule-status

# Live session snapshots (what was actually sent by the scheduler)
python -m app.cli snapshots list --date yesterday
python -m app.cli snapshots list --date 2026-05-05
python -m app.cli snapshots show --date yesterday --session morning
python -m app.cli snapshots show --date yesterday --session us_pre_open --format email-text
python -m app.cli snapshots show --date yesterday --session closing_wrap --format summary
python -m app.cli snapshots prune --retention-days 30

# Manual day/session replay QA
python -m app.cli day-replay --date today --show-output
python -m app.cli day-replay --date today --send-test telegram,email
python -m app.cli day-replay --date today --show-output --healthcare-enabled
python -m app.cli day-replay --date today --force-all --ignore-materiality
python -m app.cli day-replay --date 2026-05-04 --until close --send-test telegram,email

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
python -m app.cli --dry-run --show-output brief
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

GET    /api/v1/profile/{profile}/bonds
POST   /api/v1/profile/{profile}/bonds/refresh
PUT    /api/v1/profile/{profile}/bonds/overrides/{symbol}
DELETE /api/v1/profile/{profile}/bonds/overrides/{symbol}
GET    /api/v1/profile/{profile}/bonds/history

POST   /api/v1/profile/{profile}/reports/generate
GET    /api/v1/profile/{profile}/reports
GET    /api/v1/profile/{profile}/reports/{id}/download
DELETE /api/v1/profile/{profile}/reports/{id}

GET    /api/v1/profile/{profile}/esg
POST   /api/v1/profile/{profile}/esg/refresh
PUT    /api/v1/profile/{profile}/esg/config

GET    /api/v1/profile/{profile}/fx
POST   /api/v1/profile/{profile}/fx/refresh
PUT    /api/v1/profile/{profile}/fx/config
GET    /api/v1/profile/{profile}/fx/rates
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
delivery.session_mode              # quiet | default | active
delivery.always_send_sessions      # JSON array of session keys, e.g. '["us_intraday_risk"]'
delivery.suppress_low_materiality  # true | false
delivery.email_density_mode
delivery.quiet_hours_start / delivery.quiet_hours_end
sections.morning.market_setup / sections.morning.macro_context / sections.morning.top_themes
sections.morning.portfolio_focus / sections.morning.sector_scan / sections.morning.watchlist
healthcare.enabled / healthcare.max_items_morning / healthcare.max_items_intraday / healthcare.breaking_alerts
healthcare.themes / healthcare.tickers / healthcare.assets
healthcare.minimum_severity_morning / healthcare.minimum_severity_intraday / healthcare.minimum_severity_breaking
risk.lookback_days / risk.risk_free_rate_pct
```

#### Session mode quick reference

| Mode | Sessions sent |
|---|---|
| `quiet` | Morning only |
| `default` | Morning + US Pre-Open |
| `active` | Morning, Europe Midday, US Pre-Open, US Intraday Risk, Into Close, Closing Wrap |

Enable active mode:
```bash
python -m app.cli prefs-set --key delivery.session_mode --value active
python -m app.cli prefs-set --key delivery.suppress_low_materiality --value false
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
configs/healthcare.example.yaml     — healthcare vertical defaults
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
  Home
    -> Portfolio workspace
    -> Briefing workspace
    -> Audit workspace
    -> Trading workspace (planned)

Workspace navigation contract
  Subpage header actions: <- Workspace | Home
  No cross-workspace tabs on subpages

Workspace route map
  Briefing:
    /ui/briefing
    /ui/briefing/watchlists
    /ui/briefing/delivery
    /ui/briefing/morning
  Portfolio:
    /ui/portfolio
    /ui/portfolio/easy-setup
    /ui/portfolio/holdings
    /ui/portfolio/diagnostics
    /ui/portfolio/policy
    /ui/portfolio/allocation
    /ui/portfolio/risk
    /ui/portfolio/cma
    /ui/portfolio/scenarios
    /ui/portfolio/simulation
    /ui/portfolio/rebalancing
    /ui/portfolio/attribution
    /ui/portfolio/benchmark
    /ui/portfolio/history
    /ui/portfolio/bonds
    /ui/portfolio/reports
    /ui/portfolio/esg
    /ui/portfolio/fx
  Audit:
    /ui/audit
    /ui/audit/history
    /ui/audit/overrides
    /ui/audit/logs

Providers
  Finnhub | NewsAPI | FRED | SEC EDGAR | yfinance
  Optional: GDELT | Alpha Vantage News | FMP | Mediastack

Intelligence pipeline
  cleaners -> ticker resolution -> sector enrichment -> dedupe
  -> credibility -> personal relevance -> clustering -> scoring

Briefing generation
  brief (session auto-route) | morning | midday | preopen | intraday | close | breaking
  + manual day-replay/session tester

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
  -> Fixed Income Analytics (Phase 7A)
  -> PDF Reports (Phase 7B)
  -> ESG / SRI Scoring (Phase 7C)
  -> Multi-Currency / FX (Phase 7D)

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
  BondHoldingOverride | BondPortfolioSnapshot
  GeneratedReport
  ESGScore | PortfolioESGSnapshot | ESGConfig
  FXRate | CurrencyExposure | FXConfig
```

---

## Testing

```bash
make test
# or
python -m pytest app/tests -q
```

Current suite: **see latest `pytest` output** (regularly updated; includes session redesign, chart-stack QA, and healthcare-intelligence coverage).

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
python -m pytest app/tests/test_bonds.py -q
python -m pytest app/tests/test_reports.py -q
python -m pytest app/tests/test_esg.py -q
python -m pytest app/tests/test_fx.py -q
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
- Fixed income analytics: bond ETF reference data, weighted duration/YTM/quality, rate sensitivity, manual overrides, snapshot history
- PDF report generation: fpdf2 renderer, section assembly, DB persistence, download and soft-delete
- ESG/SRI scoring: yfinance sustainability fetch, sector fallback, exclusion taxonomy, coverage tracking, alignment labels, config persistence
- Multi-currency FX: ticker-suffix currency inference, yfinance FX rate caching, exposure breakdown, hedge recommendations
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

### Always-on deployment

For Docker, Linux systemd, and macOS launchd setup, environment variable configuration,
persistent volume layout, backup strategy, and restart policy: see **[`docs/deployment.md`](docs/deployment.md)**.

Quick paths:

```bash
# Docker (recommended for VPS or home server)
make docker-up
docker compose logs -f

# macOS launchd (always-on Mac)
./scripts/service.sh install && ./scripts/service.sh start
./scripts/service.sh status

# Linux systemd (VPS)
sudo cp scripts/briefly.service /etc/systemd/system/briefly-scheduler.service
# edit User=, WorkingDirectory=, EnvironmentFile= in the unit file
sudo systemctl enable --now briefly-scheduler
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
| 5.9 | Complete | Easy Setup onboarding wizard: 3-step novice flow that auto-fills policy, allocation, benchmark, CMA, risk preferences, and rebalancing defaults |
| 6.0A | Complete | Beginner comprehension layer: centralized glossary + reusable question-mark help tooltips across complex portfolio analytics surfaces |
| 6.0B | Complete | Regional Intelligence Board: briefing-root regional structure with deterministic region statuses, emphasis shares, and portfolio-impact lens copy |
| 6.1 | Complete | Briefing clarity + delivery trust: deterministic setup-read paragraph, humanized setup symbol labels, deduped relevance notes, and explicit channel delivery outcomes in logs + Audit |
| 6.2 | Complete | Briefing signal upgrade: richer setup-read regime analysis, grouped + relevance-tagged earnings calendar, watchlist summary line, and stronger theme relevance prioritization |
| 6.3 | Complete | Regional narrative + portfolio impact: new regional lens and portfolio-impact sections in briefs, regime context/alignment framing, and briefing-home impact preview |
| 7A | Complete | Fixed Income Analytics: bond duration, YTM, credit quality, rate sensitivity, manual overrides |
| 7B | Complete | PDF Portfolio Reports: fpdf2 renderer, user-selectable sections, DB-tracked report history, download |
| 7C | Complete | ESG/SRI Scoring: yfinance sustainability, exclusion screens, alignment labels, per-profile config |
| 7D | Complete | Multi-Currency: ticker-suffix currency inference, FX rate cache, exposure breakdown, hedge recommendations |
| 8.7 | Complete | Session delivery hardening: per-session idempotency, `session-send`, `catch-up`, `schedule-status` commands, `session_mode`/`suppress_low_materiality` preference keys, 10 new idempotency and catch-up tests |
| 8.8 | Complete | Historical backfill labelling: `backfill` command alias, `HISTORICAL BACKFILL - NOT LIVE` banners on all channels, `[BACKFILL]` email subject prefix, future-date rejection, idempotency keyed to original session date, 20 new backfill unit tests |
| 8.9 Lite | Complete | Automatic live session snapshot archive: retention-controlled local SQLite storage for all six scheduler-generated sessions, `snapshots list/show/prune` CLI, non-blocking capture, secrets-clean, 45 new tests |
| 5.7B | Planned | Historical selection and interaction effects using holding-level daily return series |

---

## License

MIT
