# Briefly: Technical Development History

This document records the phase-by-phase development history of Briefly. For the current product overview, architecture, and usage guide, see the [README](../README.md).

---

## Phase status summary

| Phase | Status | Description |
|---|---|---|
| 2 | Complete | Formatting, trust, weekend mode, breaking schedule |
| 3 | Complete | Holdings import, portfolio-aware briefings, operational tools |
| 4 | Complete | LLM email rendering, web control centre, cadence engine, global intelligence |
| 5.1 | Complete | Policy, allocation, and benchmark foundation |
| 5.2 | Complete | Risk and benchmark analytics |
| 5.3 | Complete | CMA builder and expected portfolio analytics |
| 5.4 | Complete | Rebalancing and implementation engine |
| 5.5 | Complete | Attribution: Brinson-Hood-Beebower model, CMA-based allocation effect, waterfall decomposition |
| 5.6A | Complete | Workflow architecture split: Market Briefing vs Portfolio Workbench vs Audit |
| 5.6B | Complete | Visual productisation: workspace home route, route-based module entry points |
| 5.6C | Complete | Personalised home workspace: what-matters-now hero, deterministic home summaries |
| 5.6D | Complete | Navigation discipline: portfolio root as dashboard-only, route-level section gating |
| 5.7A | Complete | Portfolio validation harness: canonical presets, golden checks, parameter sweeps, fuzz tests |
| 5.7B | Planned | Historical selection and interaction effects using holding-level daily return series |
| 5.8 | Complete | Simulation Lab: multi-method engine, macro overrides, interactive charts, persisted runs/presets |
| 5.9 | Complete | Easy Setup onboarding wizard: 3-step novice flow |
| 6.0A | Complete | Beginner comprehension layer: centralised glossary and help tooltips |
| 6.0B | Complete | Regional Intelligence Board: deterministic region statuses and portfolio-impact lens |
| 6.1 | Complete | Briefing clarity and delivery trust: deterministic setup-read, humanised labels, channel outcomes |
| 6.2 | Complete | Briefing signal upgrade: richer setup-read, grouped earnings calendar, watchlist summary |
| 6.3 | Complete | Regional narrative and portfolio impact: regional lens and portfolio-impact sections |
| 6.4 | Complete | Deterministic Morning Visuals Engine: chart contract, regime-to-chart-stack selection |
| 6.5 | Complete | Signal hygiene upgrade: dominant tape driver, clickbait filter, microcap earnings suppression |
| 7A | Complete | Fixed Income Analytics: bond duration, YTM, credit quality, rate sensitivity, manual overrides |
| 7B | Complete | PDF Portfolio Reports: fpdf2 renderer, user-selectable sections, DB-tracked history, download |
| 7C | Complete | ESG/SRI Scoring: yfinance sustainability, exclusion screens, alignment labels |
| 7D | Complete | Multi-Currency: ticker-suffix inference, FX rate cache, exposure breakdown, hedge recommendations |
| 8.7 | Complete | Session delivery hardening: per-session idempotency, `session-send`, `catch-up`, `schedule-status` |
| 8.8 | Complete | Historical backfill labelling: `backfill` command, banners, future-date rejection |
| 8.9 Lite | Complete | Automatic live session snapshot archive: retention-controlled SQLite storage |
| 9.1 | Complete | Startup catch-up: automatic delivery of elapsed sessions on scheduler start |
| 9.2 | Complete | Delivery failure alerting: Telegram self-alert with resend command on failed delivery |
| 9.3 | Complete | Retry/backoff on transient delivery failures: per-channel independent retry with configurable policy |
| 9.4 | Complete | Briefing history UI: HTMX archive viewer with date picker, session cards, and content tabs |
| 9.5 | Complete | LLM cost tracking: `llm_usage_logs` SQLite table, `llm-usage summary/list` CLI commands |
| 9.6 | Complete | LLM cost controls: monthly spend budget, automatic deterministic fallback on budget exhaustion |
| 9.7 | Complete | Always-on deployment profile: Docker, systemd, launchd guides in `docs/deployment.md` |
| 9.8 | Complete | Exact snapshot replay: `snapshots replay` re-delivers stored content without provider calls |
| 9.9 | Complete | Reliability hardening: duplicate-send race condition fix, single `session_cadence_check` job |
| 9.X | Complete | MoveContextEngine: deterministic per-instrument move, range, and 1Y level context |

---

## Detailed phase notes

### Phase 2

Formatting, trust scoring, weekend-mode presentation, and the breaking alert schedule. Established the core pipeline structure.

### Phase 3

Holdings import (YAML and CSV), portfolio-aware briefing influence, and the first operational CLI tools (quote, news, status).

### Phase 4

LLM email rendering with shadow mode and strict validation against the deterministic payload. Web control centre (FastAPI/HTMX). CadenceEngine for per-day, per-session, per-channel rate limiting. Global news expansion with credibility scoring and personal relevance. Phase 4.5 added optional providers: GDELT, Alpha Vantage News, FMP, Mediastack.

### Phase 5.1

IPS-style Investor Policy persisted in SQLite: return target, volatility cap, drawdown cap, single-name limit, liquidity minimum, equity maximum, governance cadence. Policy breach detection across all active dimensions. Strategic Asset Allocation with per-asset-class min/max bands and drift comparison. First-class benchmark configuration (market index, policy blend, or custom blend).

### Phase 5.2

Live benchmark-relative risk metrics using yfinance price history: Sharpe, Sortino, annualised volatility, maximum drawdown, active return, tracking error, information ratio. DB-cached return series and metric snapshots. Volatility and drawdown policy breach integration.

### Phase 5.3

Per-asset-class capital market assumptions (expected return and volatility) and NxN correlation matrix configuration. Expected portfolio return, volatility, and Sharpe from actual weights. SAA expected metrics for side-by-side comparison. Policy gap and SAA gap summaries. Expected return below policy target added as a policy-fit breach.

### Phase 5.4

Drift-triggered, calendar, and hybrid rebalancing. Per-asset-class buy/sell/hold trade list with magnitude, priority ranking, and holding-level drill-down. One-way turnover and estimated transaction costs in basis points. Proposal history log. Tax-aware flag (v1: informational only).

### Phase 5.5

Brinson-Hood-Beebower attribution using CMA expected returns as the return proxy. Per-asset-class decomposition into allocation effect, selection effect (v1: zero), and interaction effect (v1: zero). Waterfall decomposition and top contributor/detractor identification. Attribution history log.

### Phase 5.6A

Top-level UI split into Market Briefing, Portfolio Workbench, and Audit. Coverage, Delivery, and Morning Composition grouped under Market Briefing. Portfolio workbench presented as separated areas: overview, builder, diagnostics, risk, scenarios, implementation, attribution. Constrained dropdowns for semantic controls.

### Phase 5.6B

New workspace gateway route at `/ui` with module entry cards. Route-based workflow entry points: `/ui/briefing`, `/ui/portfolio`, `/ui/audit`. Portfolio task routes for focused work screens. Settings shell accepts route-provided initial module/section.

### Phase 5.6C

Personalised home state (`analysis.ui_home`) summarises what matters now, next action bias, and status chips. Action-forward hero with primary CTAs. Portfolio Workbench intentionally dominant; Market Briefing secondary; Audit visually demoted to advanced controls.

### Phase 5.6D

Portfolio root route (`/ui/portfolio`) behaves as a summary dashboard only. Route-level section gating is server-side. Navigation hierarchy is strict: Home is the only workspace switcher; workspace roots route into focused subpages; subpages expose only back and home actions. Briefing split into root and subpages. Audit becomes a full workspace with subpages.

### Phase 5.7A

Canonical test preset library for 10 deterministic portfolio scenarios. Validation runner with pass/fail checks across holdings, policy, allocation, CMA, rebalancing, and attribution readiness. Parameter sweep runner for directional monotonic checks. Randomised fuzz harness for invariant testing. Load Test Preset action in the Builder UI.

### Phase 5.8 (Simulation Lab)

Multi-method forward simulation: Monte Carlo (multivariate), historical, filtered historical, block bootstrap. Macro override controls (growth, inflation, rates, volatility regime, correlation stress). Horizon and frequency controls with presets. Interactive chart outputs: fan chart percentile cone, terminal value histogram, drawdown histogram, sensitivity growth chart. Persisted simulation runs and saved presets. Route: `/ui/portfolio/simulation`.

### Phase 5.9 (Easy Setup)

Three-step guided onboarding flow: profile basics, portfolio shape, review and apply. Deterministic mapping engine auto-fills Investor Policy, Strategic Allocation, Benchmark config, CMA entries, risk preferences, and rebalancing configuration. Home detects unconfigured profiles and surfaces a Quick Setup CTA. Advanced subpages remain fully editable.

### Phase 6.0A (Beginner Comprehension Layer)

Central glossary registry for complex financial concepts. Reusable question-mark help badge with hover/focus tooltip behaviour. Applied to high-jargon portfolio routes. Tooltip copy is short and keyboard/mobile accessible.

### Phase 6.0B (Regional Intelligence Board)

Briefing workspace root includes a deterministic Regional Intelligence Board with structured region blocks: US, Europe, China, Rest of Asia, Middle East, Russia/Ukraine, Latin America, Cross-Asset Spillovers. Each region card shows emphasis share, status label, focus thesis, and portfolio lens text.

### Phase 6.1 (Briefing Clarity and Delivery Trust)

Symbol normalisation for user-facing market setup copy. Deterministic market-setup interpretation engine in both Telegram and email outputs. Improved global-news relevance notes. Audit logs include recent delivery outcomes by message type and channel. Email failure troubleshooting surfaced in morning-run logs.

### Phase 6.2 (Briefing Signal Upgrade)

Setup-read v2 explicitly calls out US/Europe/Asia direction, volatility regime, rates/curve impulse, and commodity impulse. Net takeaway sentence links market setup to likely session driver. Earnings Calendar grouped by Today/Tomorrow/This Week with `Company (TICKER)` labels and portfolio/watchlist relevance tags. Top Themes ranking favours holdings/watchlist overlap and de-emphasises low-signal filings.

### Phase 6.3 (Regional Narrative and Portfolio Impact)

Morning briefing includes a deterministic Regional Lens section (US, Europe, Asia, plus conditional geopolitical spillover regions) and a Portfolio Impact Today section with posture and top impact bullets. Regime Context and Positioning Alignment lines added. Briefing workspace root includes a portfolio-impact preview strip.

### Phase 6.4 (Deterministic Morning Visuals Engine)

Deterministic chart contract: `chart_key`, `variant`, `available`, `priority`, `reason_if_hidden`, `series`, `annotations`, `meta`, `email_dimensions`. Rule-based regime tags and chart promotion policy (hero/support/optional portfolio/event/microcards). Core chart families: global relative performance, cross-asset impulse strip, holdings excess performance, sector exposure quadrant, event-linked annotated trend, breadth leadership panel, rates curve micro panel, volatility regime card, portfolio concentration risk card, earnings relevance strip. Outlook-safe dark navy desk-note email shell. LLM boundary tightened: LLM receives chart summaries for prose only.

### Phase 6.5 (Signal Hygiene Upgrade)

Deterministic dominant tape driver detection (geo-energy shock, rates repricing, mega-cap earnings). Global-news hard-filter for low-quality preview/listicle/SEO headlines. Watchlist catalyst line prefers dominant-driver context. Earnings section suppresses low-value microcap blocks when there is no portfolio/watchlist overlap.

### Phase 7A (Fixed Income Analytics)

Bond analytics for holdings with a `fixed_income` bucket or recognised bond ETF symbol. Weighted modified duration, portfolio YTM, credit quality distribution (govt/IG/HY/EM), maturity ladder, rate sensitivity (+100 bps parallel shift). Reference data for 26 known bond ETFs with manual override capability. Append-only `BondPortfolioSnapshot` log. Route: `/ui/portfolio/bonds`.

### Phase 7B (PDF Portfolio Reports)

On-demand A4 PDF generation using fpdf2 (pure Python). User-selectable sections: Cover, Holdings, Risk, Attribution, Bonds, Scenarios, CMA. Reports persisted in `data/reports/{profile}/` with `GeneratedReport` DB record. Soft-delete with file removal; download via streaming `FileResponse`. Route: `/ui/portfolio/reports`.

### Phase 7C (ESG/SRI Scoring)

Weighted portfolio ESG score via yfinance `.sustainability`. Sector-based fallback scores for 10 GICS sectors. Hardcoded SRI exclusion screens: tobacco, weapons, thermal coal, gambling, adult content, fossil fuels. SRI alignment labels: Strong/Partial/Weak/Insufficient Data. Coverage percentage. ESG scores cached daily. Append-only `PortfolioESGSnapshot` log. Route: `/ui/portfolio/esg`.

### Phase 7D (Multi-Currency)

Automatic listing-currency inference from ticker suffix. Per-currency exposure breakdown with home/foreign classification. FX rates fetched from yfinance and cached daily in `FXRate` table. Hedge recommendations under partial or full hedge policy. Per-profile `FXConfig`. Route: `/ui/portfolio/fx`.

### Phase 8.7 (Session Delivery Hardening)

Per-session, per-channel idempotency via `SessionSendState`. `session-send` command bypasses clock routing for any specific session. `catch-up` command sends all missed sessions for today. `schedule-status` command shows lock state, current/next session, preferences, and per-channel send history. `session_mode` and `suppress_low_materiality` preference keys. 10 new idempotency and catch-up tests.

### Phase 8.8 (Historical Backfill Labelling)

`backfill` command alias always requires `--date` and always applies the `HISTORICAL BACKFILL - NOT LIVE` banner. Banner timestamps reflect the latest plausible send time within each session window. Future dates rejected. Idempotency keyed to the original session date. 20 new backfill unit tests.

### Phase 8.9 Lite (Automatic Live Session Snapshots)

Scheduler-triggered sessions are archived in SQLite (one row per session per day). Each row stores Telegram text, email subject and body, compact market/macro/portfolio/chart summaries, delivery status, and timestamp. CLI commands: `snapshots list`, `snapshots show`, `snapshots prune`. Non-blocking capture. Default 30-day retention with automatic pruning. 45 new tests.

### Phase 9.1 (Startup Catch-up)

On `start_scheduler()`, after acquiring the process lock, Briefly automatically sends any sessions that elapsed today before the scheduler started. Uses the same idempotency infrastructure as `catch-up`. Skipped on weekends, dry-run mode, and at startup before any session window has opened.

### Phase 9.2 (Delivery Failure Alerting)

When the scheduler fails to deliver a session to any configured channel, a Telegram self-alert is sent immediately. The alert names the session, date, failed channel, and error, and includes a ready-to-paste CLI resend command. A 90-minute per-session-per-day cooldown via `SentMessage` prevents alert spam. Alerts are non-blocking and skipped for backfills, dry runs, and non-scheduler sends.

### Phase 9.3 (Retry/Backoff on Transient Delivery Failures)

Telegram and email channels retry independently before marking a send as failed. Default policy: up to 3 attempts, waiting 10 seconds after attempt 1 and 30 seconds after attempt 2. The MIME message is built once; only the SMTP connection is retried for email. Telegram retries at the per-message level so earlier messages in a multi-part brief are never duplicated. Phase 9.2 failure alerts fire only after all retries are exhausted. Retry logic lives in `app/messaging/retry.py`.

### Phase 9.4 (Briefing History UI)

`/ui/briefing/history` is a standalone page for browsing the session archive. Date picker and prev/next weekday navigation. Six session cards (per day) show delivery status chips and channel sub-chips. Clicking a card loads an HTMX detail pane with four tabs: Telegram text, email plain text, email HTML (sandboxed iframe), and metadata. Data read directly from SQLite; no provider calls made.

### Phase 9.5 (LLM Cost Tracking)

Every live LLM API call is persisted to a `llm_usage_logs` SQLite table with prompt tokens, completion tokens, model name, render mode (live/shadow/fallback), session key, local date, and estimated cost in USD. Two CLI commands: `llm-usage summary [--days N]` and `llm-usage list [--days N] [--limit N]`. All logging is non-blocking. Cost rates default to zero and are configured via `llm_email_input_cost_per_1m_tokens` and `llm_email_output_cost_per_1m_tokens`.

### Phase 9.6 (LLM Cost Controls)

Monthly spend budget (`llm_monthly_budget_usd`, default 0 = no limit). `query_monthly_spend()` aggregates estimated cost for the current calendar month. When a budget is set and spend meets or exceeds it, `render_morning()` returns `mode="budget_exceeded"` and falls back to deterministic email without making an API call. `llm-usage summary` extended to show current month's spend, budget, percentage used, and remaining headroom.

### Phase 9.7 (Always-on Deployment Profile)

Full deployment guide at `docs/deployment.md` covering Docker (recommended for VPS), Linux systemd, and macOS launchd. Includes tiered environment variable checklist, persistent volume layout, SQLite hot-backup strategy with cron template, restart policy comparison table, and Mac sleep caveat. New files: `docs/deployment.md`, `scripts/briefly.service`. Dockerfile gains layer-cache ordering and a `HEALTHCHECK`. `docker-compose.yml` gains a `briefly-web` profile service and a `backups/` volume mount.

### Phase 9.8 (Exact Snapshot Replay)

`python -m app.cli snapshots replay --date <date> --session <key>` re-delivers a stored snapshot through live messenger channels without provider or LLM calls. A `SNAPSHOT REPLAY` banner is prepended to all content; `--no-banner` suppresses it. `--channel telegram|email|all` routes to one or both channels. No idempotency keys are written and no new snapshot is created. Implemented in `app/briefing/snapshot_replay.py`. 17 tests.

### Phase 9.9 (Reliability Hardening and Session Rendering Audit)

Eliminates the duplicate-send race condition caused by two separate polling jobs (`morning_cadence_check` and `intraday_cadence_check`). Replaced with a single `session_cadence_check` job (`max_instances=1`) covering all six session windows. `app/briefing/session_metadata.py` is the canonical source of truth for session labels, focus lines, and windows. Channel routing fixed: all six sessions resolve delivery channels via session key directly. `run_daily_summary()` function and `daily-summary` CLI command added. `schedule-status` upgraded to show session label, focus, and window for current and next sessions, plus a full session schedule table. Signal quality improvements: energy/blockade relevance templates now require genuine energy-core keywords; clickbait filter extended. 32 new tests.

### Phase 9.X (MoveContextEngine)

Deterministic per-instrument move, range, and level context for all Telegram and email outputs. `app/briefing/move_context.py` answers four questions for every market number: direction, move magnitude (static thresholds or 1Y percentile when history is available), current level vs 1Y range, and where the current print sits within today's intraday range. Treasury yields always shown in basis points. VIX carries a regime label (Calm/Watchful/Stressed/Panic). Session-mode-aware range labels. `format_session_change()` generates plain-English session-to-session "what changed" lines, suppressing zero-change outputs. Integrated into `TelegramFormatter._format_market_setup()` and `_format_watchlist()`. 101 tests.
