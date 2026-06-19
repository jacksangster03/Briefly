# Briefly: Master Reference Document

Status: living reference, audited against the codebase on 2026-06-16.
Scope: the entire system end to end. This document does not replace `README.md`, `docs/architecture.md`, `docs/SESSION_DESIGN.md`, `docs/AUDIT_CURRENT_STATE.md`, or `docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md`. It sits alongside them as the single canonical map of what exists today, written directly from the code rather than from memory or prior docs.

Every claim below is grounded in a specific file path. Where a number (line count, field count, route count) is given, it was measured against the repository at audit time and will drift as the code changes; treat exact counts as approximate beyond the audit date.

---

## 1. What Briefly Is

Briefly is a deterministic-first, local-first market briefing and portfolio intelligence platform. It runs as a long-lived process (`python -m app.cli scheduler`) that, on a fixed daily cadence, pulls market data and news from free-tier providers, scores and deduplicates events, assembles session-specific briefings, and delivers them over Telegram and/or email. A second long-lived process (`python -m app.cli web`) serves a local control-centre web UI for configuration, diagnostics, and portfolio management.

The architectural premise, repeated throughout the codebase in docstrings and comments, is: **deterministic logic is authoritative, LLM output is optional and shadow-only**. Every score that decides what gets sent, when, and in what mode is a plain Python function over structured data. Where an LLM is used (`enable_llm_email_render`, shadow classifiers in `app/ml/`), it either renders prose over already-selected, already-scored content, or runs in parallel for comparison logging without affecting output. This is not a stylistic choice; it is enforced structurally: `SendDecision`, `NewsClassification`, and `final_score` are all plain dataclasses/Pydantic models produced by pure functions that never call an LLM.

Three product surfaces share this deterministic core:

1. **Market Briefing**: the six-session daily cadence (Morning, Europe Midday, US Pre-Open, US Intraday Risk Check, Into Close, Closing Wrap) plus ad-hoc breaking alerts.
2. **Portfolio Control**: holdings, allocation targets, rebalancing, risk/attribution analytics, ESG, bonds, FX, and PDF reporting, all reachable via CLI and the web UI.
3. **Vertical Intelligence** (healthcare most mature, geopolitics and ai_tech earlier-stage): domain-specific event collection and scoring layered on top of the same news pipeline, gated `off|watch|active|portfolio_linked` per profile.

---

## 2. Repository Map

```
app/
  cli.py                 Click-based CLI entry point (~48 commands across 5 groups)
  scheduler.py            APScheduler process: session cadence checks, breaking-alert cron, startup catch-up
  settings.py             Pydantic BaseSettings: ~150+ env-driven config fields
  logger.py               Structured logging setup
  db/
    models.py             SQLAlchemy ORM, ~45 tables (986 lines)
    session.py             Engine/session factory, init_db()
  schemas/
    events.py              NormalisedEvent, QuoteData, MacroDataPoint, EarningsEvent, SectorSnapshot, MarketBreadth
    briefings.py            MorningBriefing (~80 fields), IntradayUpdate, BreakingAlert/BreakingClassification
    delivery.py             ChartAsset and delivery-layer schemas
  data_sources/
    news_data.py            NewsDataService facade (Finnhub/NewsAPI/SEC + GlobalNewsHubService)
    global_news_hub.py      Multi-provider news orchestration, budget tracking, canonicalisation, dedupe
    (provider adapters: finnhub, fred, ecb, eurostat, sec_edgar, yfinance, gdelt, alpha_vantage, fmp, mediastack, marketaux, ...)
  processing/
    pipeline.py              Shared event pipeline orchestration (557 lines)
    dedupe.py                 Content-hash + cluster dedupe (243 lines)
    event_clustering.py       Deterministic headline clustering (376 lines)
    relevance_scoring.py      SOURCE_CREDIBILITY, EVENT_TYPE_WEIGHTS, final_score composition (363 lines)
    source_credibility.py     DEFAULT_TRUST_TIERS, PROVIDER_TIERS (114 lines)
    personal_relevance.py     Watchlist/sector/theme-weighted personal scoring (136 lines)
    article_quality.py        Clickbait/low-signal filtering (394 lines)
    sentiment.py               FinBERT-backed sentiment scoring hook (79 lines)
    cleaners.py                 Text normalisation (122 lines)
    event_store.py              Sent-history tracking (56 lines)
  briefing/
    send_decision.py            Freshness-aware send/suppress gate (230 lines)
    news_classifier.py          Deterministic story_type/freshness/breaking taxonomy (516 lines)
    trust_contract.py           Canonical price resolution, pre-send contradiction lints
  verticals/
    engine.py                   Facade: registered_verticals(), build_vertical_section(), breaking candidates (457 lines)
    base.py, scoring.py, source_store.py, shadow_summary.py, config.py, events.py
    plugins/                    healthcare.py, geopolitics.py, ai_tech.py
    sources/                    ai_tech.py and other vertical-specific source adapters
  healthcare/
    schemas.py                   HealthcareBriefingSection and related models
  portfolio/, allocation/, attribution/, benchmark/, risk/, cma/, rebalancing/,
  simulation/, bonds/, esg/, fx/, reports/, analytics/, policy/
                                Portfolio analytics stack (~11,000 lines combined), detailed in Section 6
  personalization/
    user_profile.py, preferences_service.py, delivery_rules.py
                                User profile model, preference persistence, YAML-driven delivery rules
  ml/
    finbert_scorer.py, garch_var.py, anomaly_detector.py, news_classifier_shadow.py, news_dataset.py
                                Optional ML add-ons, all shadow/off by default
  messaging/
    base.py, telegram.py, email.py, retry.py
                                Delivery channel implementations (461 lines combined)
  markets/
    calendar.py                  Deterministic exchange holiday/session calendar
  web/
    app.py                       Flask/Starlette app, ~90 routes
    templates/                   12 Jinja templates + 2 partials (control centre, dashboards, settings)
configs/
  sources.yaml, trust_tiers, schedules.yaml, alert_rules.yaml, delivery_preferences.yaml,
  regions.yaml, sectors.yaml, interest_weights.yaml, macro_calendar.yaml,
  healthcare.example.yaml, holdings.example.yaml, user_profile.example.yaml, watchlists.example.yaml
docs/
  architecture.md, SESSION_DESIGN.md, AUDIT_CURRENT_STATE.md, NEWS_TREND_RADAR_PLAN.md,
  LLM_VERTICAL_SHADOW_PLAN.md, VERTICAL_INTELLIGENCE_API_PLAN.md, FUTURE_ENHANCEMENTS.md,
  BRIEFLY_RESEARCH_AGENT_STRATEGY.md, BRIEFLY.md (this file)
Dockerfile, docker-compose.yml, Makefile, pyproject.toml, requirements.txt
```

---

## 3. End-to-End Data Flow

### 3.1 Scheduling (`app/scheduler.py`)

A single APScheduler process drives everything. Two job families exist:

- **Unified session cadence check** (`_run_session_cadence_check`), `max_instances=1`, polled at a fixed interval (per `configs/schedules.yaml`, e.g. morning at 08:45 with a 5-minute `check_interval_minutes`). This job decides, each tick, whether any of the six sessions is due, and if so runs the full briefing pipeline for it.
- **Breaking-alert cron jobs**, running continuously through market hours (per `breaking_alerts` block in `configs/schedules.yaml`), checking for high-score, high-novelty events that justify an out-of-cadence alert.
- **Startup catch-up** (`_run_startup_catchup`): on process boot, checks whether a session that should have fired while the process was down was missed, and fires it once.
- A file lock at `data/state/scheduler.lock` prevents two scheduler processes from running concurrently (e.g. accidental double-start in Docker).

### 3.2 Fetch (`app/data_sources/`)

`NewsDataService` (`app/data_sources/news_data.py`) is the facade callers use: `fetch_market_news`, `fetch_company_news`, `fetch_filings`, `fetch_insider_trades`, `fetch_all`. Internally it delegates to `GlobalNewsHubService` (`app/data_sources/global_news_hub.py`), which:

- Pulls from each configured provider (Finnhub, NewsAPI when enabled, SEC EDGAR, GDELT, AlphaVantage, FMP, Mediastack, Marketaux: see `configs/sources.yaml` for which are enabled).
- Tracks a daily call budget per provider via `_consume_budget()`, so a misbehaving provider cannot exhaust free-tier quota mid-day.
- Canonicalises each raw item into a `NormalisedEvent` (`_canonicalize_event()`), normalising URLs and stripping tracking parameters.
- Computes a `_story_fingerprint()` per item and removes duplicates across providers (`_dedupe_story_fingerprints()`) using a `_quality_tuple()` to decide which duplicate survives (prefers higher source priority, defined in `_SOURCE_PRIORITY`).

Market data follows a parallel fallback chain documented in `docs/SESSION_DESIGN.md`: live provider quote → alternate provider → delayed quote (15-60 min old) → `stale_snapshot` (prior session's archived `market_summary_json`) → `prior_close` (yfinance history fallback) → `unavailable`. Every value that isn't `live` is labelled as such; stale and live values are never silently merged.

### 3.3 Processing (`app/processing/pipeline.py` and siblings)

The shared pipeline, used by every session and by breaking-alert evaluation, runs roughly:

1. **Low-signal filtering**: `LOW_SIGNAL_PATTERNS` in `pipeline.py` is a large tuple of clickbait substrings ("3 reasons to buy", "bull and bear of the day", "millionaire maker", named-personality bait like "jim cramer" / "cathie wood" / "warren buffett had to say"). Matching items are dropped before scoring, not after, so they never consume a `final_score` computation.
2. **Deduplication** (`dedupe.py`): `deduplicate_events(events, time_window_hours=12, similarity_threshold=0.85, apply_ticker_clustering=True, mark_sent_history=True)`. Stage 1 is a content-hash dedup; subsequent stages cluster near-duplicate headlines about the same underlying story (`event_clustering.py`, which uses a `STOPWORDS` set and `HEADLINE_NOISE_PATTERNS` to normalise headlines before comparing).
3. **Credibility weighting** (`source_credibility.py`, `relevance_scoring.py`): `SOURCE_CREDIBILITY` assigns a trust weight per provider (sec_edgar 0.95, fred 0.90, finnhub/polygon 0.75, newsapi 0.55, yfinance 0.50, x_twitter 0.25), separately from `EVENT_TYPE_WEIGHTS` which weights by what kind of event it is (fda_decision/fed_decision 0.95, earnings/guidance/m_and_a 0.90, macro_release 0.85, geopolitical/regulatory/current_report 0.80, annual_report 0.75, insider_transaction/quarterly_report 0.70, analyst_action/ownership_disclosure 0.65, company_news 0.55, market_news 0.50).
4. **Personal relevance** (`personal_relevance.py`): boosts score for watchlist tickers, sector matches, and theme matches against `configs/interest_weights.yaml` (e.g. `ai_infrastructure: 1.0`, `drug_discovery: 0.90`, `fintech: 0.85`).
5. **Sentiment** (`sentiment.py`): optional FinBERT scoring hook, batchable via `app/ml/finbert_scorer.py::enrich_events_sentiment()` for ~10x throughput over per-event calls.
6. **Deterministic classification** (`app/briefing/news_classifier.py`): assigns `story_type`, `freshness_state`, `breaking_label`, `suppress_reason`, `market_relevance_score`, `portfolio_relevance_score`, `confidence` via `classify_news_event()`; `should_suppress_low_signal()` and `is_stale_breaking_candidate()` gate output further; `is_breaking_eligible_event()` decides breaking-alert eligibility.

A note on duplicated trust concepts: `configs/sources.yaml` defines `trust_tiers` (highest/high/medium/low/experimental) and `app/processing/source_credibility.py::DEFAULT_TRUST_TIERS` mirrors this, while `app/verticals/scoring.py::_SOURCE_TIER_WEIGHT` defines a separate scheme (official/primary/trusted_media/broad_media/social_optional) for vertical scoring. These are two systems answering the same question (how much do we trust this source) with different vocabularies, and they have not been unified: flagged previously in `docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md` as a consolidation target.

### 3.4 Send Decision (`app/briefing/send_decision.py`)

Before any briefing is delivered, `classify_briefing_statuses()` populates `market_data_status` (live/partial/stale_snapshot/unavailable) and `news_status` (fresh/stale/empty/provider_outage) on the `MorningBriefing` object, then `make_send_decision()` applies the matrix:

| Market Data | News Status | Scheduled | Mode | Action |
|---|---|---|---|---|
| live / partial | fresh | yes | normal | send |
| live / partial | stale/empty | yes | market_only | send |
| stale_snapshot | fresh + material | yes | degraded_context | send with stale caveat |
| unavailable | fresh + material | yes | news_only | send news only |
| stale_snapshot / unavailable | no fresh news | yes | suppressed | do not send, log `skipped_degraded_no_fresh_data` |
| any | any | dry-run | degraded_context | always render, never suppress |

"Material" news requires both a minimum fresh count (`_NEWS_FRESH_COUNT_MIN = 1`) and a minimum materiality score (`_NEWS_FRESH_MATERIALITY_THRESHOLD = 2.0`, the sum of `final_score` across deduplicated fresh events). When the decision is `news_only`, `market_setup_analysis` is explicitly cleared on the briefing object so a stale market read is never presented as current commentary. Suppressed sessions are logged to `SessionSendState` with `channel="suppressed"`; this is a distinct, intentional outcome from a failed delivery (`success=False` with a real error) and the two must not be conflated when reading delivery logs.

### 3.5 Delivery (`app/messaging/`)

`BaseMessenger` defines the interface; `TelegramMessenger` and `EmailMessenger` implement it.

- **Telegram** (`telegram.py`, 183 lines): raw HTTP against the Bot API (no async library dependency). Supports plain `send()`, `send_photo()` for single charts, `send_chart_album()` for up to 10 images as one media group, and an inline feedback keyboard (✓ Useful / ✗ Not relevant / ⚠ Wrong data) wired to `answer_callback_query()` for the feedback loop. In dry-run mode without `show_output`, it prints an HTML-stripped preview to the console rather than sending.
- **Email** (`email.py`, 159 lines): stdlib `smtplib`, no external dependency. `send()` is a simple plain/HTML multipart; `send_rich()` builds a `multipart/related` MIME message with inline PNG charts (`MIMEImage` with `Content-ID` headers referenced from the HTML body) and sends through `call_with_retry()`.
- **Retry** (`retry.py`, 69 lines): `call_with_retry(fn, channel, max_attempts=3, delay_seconds=(10, 30))`: fixed-gap backoff (10s after first failure, 30s after second), exceptions from `fn()` are caught and treated as transient failures, not re-raised.

Delivery channel selection and per-channel formatting rules live in `configs/delivery_preferences.yaml` (Telegram: HTML parse mode, 4096-char limit, sparse emoji, compact numbers; email: priority 2, disabled by default).

### 3.6 Persistence (`app/db/models.py`)

SQLite via SQLAlchemy, default path `sqlite:///{PROJECT_ROOT}/data/state/market_briefing.db` (`app/settings.py`). Roughly 45 tables; the ones structurally load-bearing for correctness (idempotency, audit trail) are:

- `SessionSendState`, `CadenceMarker`: idempotency: a session is claimed before rendering and finalized after sending, so a crash mid-render cannot result in a duplicate send on restart.
- `DeliveryFailureAlertState`: tracks when a delivery failure itself needs to be escalated (e.g. repeated SMTP failures).
- `ProviderHealthLog`: per-provider success/failure/latency history, feeding the `market_data_status`/`news_status` classification.
- `BreakingStoryState`: storyline cooldown tracking so the same breaking story doesn't re-fire every poll.
- `NewsClassifierLabel`, `NewsClassifierShadowRun`: ground truth and shadow-model comparison logs for the optional ML classifier.
- `VerticalRunDiagnostics`, `HealthcareSourceEventRecord`, `VerticalSourceEventRecord`: vertical-intelligence audit trail.

The remaining ~30 tables back the portfolio/risk/analytics stack (Section 6) and are mostly straightforward config/snapshot/result tables scoped by `UniqueConstraint` for idempotent upserts.

---

## 4. Configuration Surface (`app/settings.py`)

`Settings(BaseSettings)` is the single source of runtime configuration, env-driven (`.env`), roughly 150+ fields. Notable groupings:

- **Provider credentials and toggles**: API keys/URLs for Finnhub, FRED, ECB, Eurostat, BLS (disabled), BEA (disabled), SEC EDGAR, NewsAPI (disabled), yfinance, Polygon (disabled), Alpaca (disabled), GDELT, AlphaVantage, FMP, Mediastack, Marketaux, X/Twitter (disabled). Each enabled provider also gets request-shaping fields (e.g. `gdelt_global_query`, `alpha_vantage_topics`) and a daily budget.
- **Healthcare vertical**: per-source feature flags and daily budgets, mirroring the general provider pattern.
- **Delivery**: Telegram bot token/chat ID, SMTP host/port/user/password/to-address, `delivery_channel`, failure-alert thresholds.
- **LLM email rendering**: `enable_llm_email_render` (off by default), `llm_render_shadow_mode`, `llm_email_model="gpt-4o-mini"`, cost-per-1M-token fields, `llm_monthly_budget_usd`: an explicit hard cap so an LLM rendering feature cannot run away on cost.
- **Breaking-alert tuning**: `breaking_followup_delay_minutes`, `breaking_storyline_cooldown_minutes`, `breaking_max_alerts_per_hour`, `breaking_followup_min_asset_move_pct`.
- **Optional ML**: `enable_finbert` / FinBERT-related flags and GARCH flags: both **False by default**, confirming sentiment/volatility ML is opt-in, not load-bearing for the default pipeline.
- **Phase 6/7 chart feature flags**: `feature_yield_curve_card`, `feature_vix_risk_card`, `feature_geo_confirmation_ladder`, `feature_regional_divergence_score`, `feature_oil_transmission_card`, `feature_dynamic_chart_stack`, `feature_email_density_mode`: all **True by default**, i.e. these have graduated from experimental to standard.
- **News intelligence classifiers**: both an LLM-based and a local-ML-based shadow classifier flag exist, both shadow-mode by default: confirming the deterministic classifier in `news_classifier.py` remains authoritative.
- Convenience `@property` accessors (`finnhub_configured`, `alpaca_configured`, `fred_configured`, `telegram_configured`, `email_configured`, `normalized_delivery_channel`, etc.) so callers check "is this provider usable" without re-deriving the boolean logic at every call site.
- `dry_run=True` is the default: the system will not actually deliver anything until explicitly turned off, a meaningful safety default for a project that started as a personal tool.
- `timezone="Europe/Madrid"` is hardcoded as the default operating timezone (matches the user's location), driving session-mode derivation in `app/schemas/briefings.py::session_mode_for()`.

Static YAML configs (`configs/*.yaml`) carry everything that's data rather than behaviour-toggle:

- `sources.yaml`: provider tier/trust/capability declarations.
- `schedules.yaml`: per-session fire time and check interval (e.g. morning at 08:45, 5-minute poll; hourly intraday 14:30-21:30, 60-minute interval, 2-minute poll).
- `alert_rules.yaml`: breaking-alert thresholds (`min_final_score: 0.80`, `material_update_min_final_score: 0.88`, `max_per_hour: 3`, `min_factual_confidence: 0.65`) and a `high_priority_types` list that gets a lower bar (earnings, guidance, fda_decision, m_and_a, fed_decision, geopolitical, macro_release).
- `delivery_preferences.yaml`: per-channel formatting rules.
- `regions.yaml`: market-hours and keyword sets per geographic region (us, europe, etc.), used for geographic relevance scoring.
- `sectors.yaml`: sector → representative ETF → constituent ticker mapping (e.g. technology → XLK → AAPL/MSFT/NVDA/...).
- `interest_weights.yaml`: fine-grained theme boost weights on top of sector/watchlist matches.
- `macro_calendar.yaml`: deterministic, manually-maintained seed data for known macro events (e.g. an FOMC decision dated 2026-06-17 with `hawkish_if`/`dovish_if`/`portfolio_lens` fields): explicitly **not** live-fetched, a deliberate static fallback.
- `*.example.yaml` files (healthcare, holdings, user_profile, watchlists): templates a user copies and edits; the real files are gitignored personal data.

---

## 5. Schemas

### 5.1 Event schema (`app/schemas/events.py`)

`NormalisedEvent` is the canonical unit flowing through the entire pipeline: every provider adapter, dedup stage, scorer, and briefing section consumes or produces this type. It carries a `compute_hash()` method used for stable identity across dedup passes. Supporting types: `QuoteData`, `PricePoint`, `MacroDataPoint`, `MarketBreadth`, `EarningsEvent`, `SectorSnapshot`.

### 5.2 Briefing schema (`app/schemas/briefings.py`, 177 lines)

`MorningBriefing` is the largest model in the codebase (~80 fields) and the contract every session formatter renders against. It is organised in clear bands:

- **Market read**: `market_setup`, `market_setup_analysis`, `dominant_tape_driver`, `market_setup_analysis_confidence`, `market_setup_signal_tags`.
- **Macro/cross-asset**: `macro_context`, `commodity_strip`, `regional_lens`, `regional_skew_summary`, `regime_snapshot`, `regime_shift`, `regime_context`, `positioning_alignment`.
- **Geo risk**: `geo_risk_level`, `geo_risk_raw_level`, `geo_risk_summary`.
- **News**: `global_news`, `top_themes`, `portfolio_focus`, `applied_news_stack`, `news_pipeline_status`, `news_raw_fetched`, `news_after_fingerprint_dedup`.
- **Portfolio**: `portfolio_impact_bullets`, `portfolio_action_posture`, `portfolio_quotes`.
- **Structure/quality**: `session_quality_score/bucket/color_hex/label`, `section_confidence`, `data_freshness`, `session_diagnosis`, `trigger_board`, `what_changed_header/lines`.
- **Vertical/valuation overlays**: `healthcare_intelligence` (typed `HealthcareBriefingSection | None`), `vertical_shadow_lines`, `valuation_lens_lines`.
- **Trust/contract**: `contract_warnings`, `canonical_prices`, `quote_freshness`, `data_basis_lines`.
- **Charts**: `morning_chart_bundle`, `morning_chart_selection`, `chart_assets`.
- **Freshness/send-decision contract** (consumed by `send_decision.py`): `market_data_status`, `news_status`, `briefing_mode`, `suppress_reason`, `last_valid_market_snapshot_session/time`, `fresh_news_count`, `fresh_news_materiality_score`, `stale_snapshot_used/session/time`.

`session_mode_for(dt)` derives `weekday | saturday | sunday` from the local datetime's weekday, switching the formatter into weekend framing (no live cash session, stale index quotes, "Friday's close" rather than "yesterday").

`IntradayUpdate` is the lighter hourly model (market snapshot + new/global-risk events only: no full session-quality scaffolding). `BreakingClassification` carries the deterministic scoring breakdown for a breaking candidate (`impact_score`, `confidence_score`, `novelty_score`, `immediacy_score`, `breadth_score`, plus `why_markets_care`, `watch_assets/symbols`, `confirm_signals`, `invalidate_signals`, `storyline_key` for cooldown tracking). `BreakingAlert` wraps a classified event with market context and tracking IDs for follow-up correlation.

---

## 6. Portfolio & Analytics Stack

This is the largest single subsystem by line count (~11,000 lines across 16 modules) and the least documented elsewhere. Every module follows the same shape: a `service.py` with persistence helpers over a dedicated DB model, often a pure-function companion module for the actual math.

| Module | Purpose | Key file(s) |
|---|---|---|
| `app/portfolio/` | Holdings load/persist, the base every other analytics module reads from | `service.py` (`load_active_holdings`) |
| `app/allocation/` | Strategic asset allocation targets vs. actual; `ASSET_CLASS_CATALOG` (equities/high_quality_bonds/credit/...) | `service.py` |
| `app/attribution/` | Brinson-Hood-Beebower performance attribution ("Phase 5.5") | `service.py` |
| `app/benchmark/` | Benchmark config persistence: market_index, policy_blend, custom_blend | `service.py` |
| `app/risk/` | Risk/benchmark analytics ("Phase 5.2"), advanced metrics, GARCH, tearsheets | `service.py`, `advanced_metrics.py`, `garch.py`, `tearsheet.py` |
| `app/cma/` | Capital Market Assumptions ("Phase 5.3"): expected return/vol/correlation inputs | `service.py` |
| `app/rebalancing/` | Rebalancing & implementation engine ("Phase 5.4") with HRP and min-CVaR optimisers | `service.py`, `optimiser.py` |
| `app/simulation/` | Monte Carlo simulation lab ("Phase 5.8") | `service.py`, `engines.py`, `assumptions.py`, `metrics.py`, `scenarios.py` |
| `app/bonds/` | Fixed-income analytics ("Phase 7A"): bond ETF reference data (modified duration, YTM, credit quality) | `service.py` |
| `app/esg/` | ESG/SRI scoring overlay ("Phase 7C") with a hardcoded exclusion taxonomy | `service.py`, `exclusion_taxonomy.py`, `providers.py` |
| `app/fx/` | Multi-currency support ("Phase 7D"): rates, profile-aware FX basket, deterministic threshold-based signals | `service.py`, `basket.py`, `panel.py`, `rates.py`, `signals.py` |
| `app/reports/` | PDF report generation ("Phase 7B") via `fpdf2`, navy-themed to match email branding | `service.py`, `renderer.py` |
| `app/analytics/` | Deterministic portfolio analyzer payloads for the local control centre | `portfolio_analyzer.py` |
| `app/policy/` | Investor policy statement persistence | `service.py` |

Notable details worth preserving:

- **Risk metrics are pure and unit-testable by design**: `app/risk/advanced_metrics.py`'s docstring states explicitly "Pure functions... This module deliberately has no DB or web dependencies so it stays unit-testable." Annualisation uses 252 trading days; monthly aggregation uses non-overlapping 21-day blocks.
- **GARCH appears twice**: `app/risk/garch.py` (`compute_garch_metrics`, min 60 observations) and `app/ml/garch_var.py` (GARCH(1,1) + 1-day VaR via the `arch` library, same 60-obs minimum, graceful fallback on non-convergence). These are not the same code path; one is plain risk reporting, the other is the ML/VaR estimation hook gated by the (off-by-default) GARCH settings flag. Worth confirming during any future refactor whether this duplication is intentional (different consumers) or drift.
- **ESG exclusions are hardcoded, not provider-driven**: `app/esg/exclusion_taxonomy.py` ships static symbol lists per excluded industry (tobacco: MO/PM/BTI/LO/..., weapons: LMT/RTX/NOC/GD/BA/..., thermal_coal: BTU/ARCH/CEIX/...). `providers.py` exists for live ESG score fetch but the exclusion screen itself is a static list, meaning it requires manual maintenance as company classifications change.
- **FX signals are explicitly non-predictive**: both `basket.py` and `signals.py` docstrings state "No forecasts, no LLM calls. All signals are derived from threshold rules applied to observed price changes": consistent with the deterministic-first principle stated for the whole system.
- **Rebalancing optimisers**: `optimiser.py` supports `hrp` (Hierarchical Risk Parity) and `min_cvar` (Minimum CVaR at 95%) over a pandas DataFrame of returns: both standard, explainable allocation methods rather than a black-box optimiser.
- **Bond reference data is also hardcoded**: `app/bonds/service.py` carries known bond ETF reference data (modified duration, YTM, credit quality) as static module-level data, same pattern as the ESG exclusion lists: a recurring pattern in this stack where "reference data without a good free live source" gets hardcoded and presumably needs periodic manual refresh.

All of this analytics is reachable from both the CLI (`app/cli.py` groups) and the web control centre (`portfolio_home.html`, `command_centre.html`).

---

## 7. Vertical Intelligence Framework

A plugin architecture (`app/verticals/engine.py`, 457 lines) lets domain-specific intelligence layer onto the shared news pipeline without forking it. Each vertical plugin implements `resolve_mode()`, `activation_state()`, `_collect()`, `build_section()`, `breaking_candidates()`, and `audit_metrics()`. Modes are `off | watch | active | portfolio_linked`, set per user profile.

- **Healthcare** (`app/verticals/plugins/healthcare.py` + `app/healthcare/schemas.py`) is the most mature: it has its own typed `HealthcareBriefingSection` embedded directly in `MorningBriefing`, its own settings block (per-source flags and daily budgets in `app/settings.py`), and its own DB audit table (`HealthcareSourceEventRecord`).
- **Geopolitics** (`app/verticals/plugins/geopolitics.py`, fully read) is GDELT-driven and density-heavy: `build_section()` currently returns `None` (no dedicated briefing section yet: it only feeds `breaking_candidates()`, filtered at `score >= 0.8`). It is shadow/off by default.
- **AI/Tech** (`app/verticals/plugins/ai_tech.py`, `app/verticals/sources/ai_tech.py`) is an SEC + arXiv + GitHub scaffold, earliest-stage of the three.

Scoring is centralised in `app/verticals/scoring.py`: `_SOURCE_TIER_WEIGHT` (official/primary/trusted_media/broad_media/social_optional), `VerticalScoreWeights` dataclass, `deterministic_vertical_score()`, `rank_vertical_events()`: i.e. the same "deterministic score decides, LLM never decides" rule applies inside verticals too. Diagnostics from every vertical run persist to `VerticalRunDiagnostics` for later audit (`verticals_status_for_profile()` surfaces this to the web UI's `verticals_dashboard.html`).

---

## 8. CLI Surface (`app/cli.py`)

Click-based, organised into command groups: `research`, `validation`, `simulation`, `snapshots_group`, `llm_usage`, plus top-level commands. Roughly 48 commands total. Operationally relevant ones (also reflected in `Makefile` targets):

- `scheduler`: start the long-lived cadence/breaking-alert process (Docker default command).
- `web --host --port`: start the control-centre web app.
- Session run commands (`run-morning`, `run-intraday`, etc. via Makefile wrapping CLI): manual one-shot triggers for testing a session outside the scheduler.
- `dry-run`: render without sending, useful for inspecting output before enabling delivery.

`Makefile` targets: `install`, `dev`, `setup`, `run-morning`, `run-intraday`, `run-alerts`, `test`, `lint`, `format`, `clean`, `docker-up`, `docker-down`, `dry-run`.

---

## 9. Web Control Centre (`app/web/app.py`, ~90 routes)

Templates (`app/web/templates/`): `command_centre.html` (home), `macro_dashboard.html`, `portfolio_home.html`, `briefing_history.html` + `partials/history_detail.html`, `watchlist_chart_explorer.html`, `diagnostics.html`, `news_intelligence.html`, `verticals_dashboard.html`, `briefings_delivery.html`, `settings.html` + `partials/settings_root.html`, `ui_home.html`.

This is the operator-facing surface for everything that isn't a scheduled send: inspecting diagnostics, tuning settings, managing portfolio holdings/allocation/rebalancing, reviewing delivery history, and viewing vertical-intelligence status. It runs as a second container in `docker-compose.yml` (profile `web`), bound to `127.0.0.1:8080` by default: explicitly not exposed beyond localhost without a reverse proxy in front, per the compose file's own comment.

---

## 10. Deployment

`Dockerfile`: `python:3.12-slim`, dependency layer cached ahead of source copy, creates `data/state`, `data/raw`, `data/processed`, `data/cache`, `logs`, `backups` directories, initialises the DB schema at build time (`python -c "from app.db.session import init_db; init_db()"`), and defines a `HEALTHCHECK` that simply verifies `app.cli` imports cleanly (a liveness check, not a deep health check: it would not catch a broken provider or a stuck scheduler).

`docker-compose.yml` defines three services:

- `briefly`: the always-on scheduler, `restart: unless-stopped`, the only service in the default profile (i.e. `docker compose up` without `--profile` starts only this).
- `briefly-web`: the control centre, profile `web`, must be explicitly requested.
- `briefly-manual`: profile `manual`, for one-shot commands via `docker compose run --rm briefly-manual <cmd>`.

All three mount `./data`, `./logs`, `./configs` from the host, so SQLite state, logs, and YAML config survive container recreation. `.env` is the single env-file source for all three services.

---

## 11. Testing & Quality Gates

- **Ruff** (`pyproject.toml`): `select = ["E","F","W","I","N","UP","B","SIM"]`, line-length 100.
- **pytest**: `testpaths = app/tests`.
- **mypy**: configured in `pyproject.toml` (type checking is part of the quality gate, not optional).
- No markdown/doc linter exists in the repo: documentation changes (including this file) have no automated check beyond manual review.
- `make test`, `make lint`, `make format` are the standard local entry points; CI configuration (if any) was not located in this audit and should be verified separately before relying on it as a gate.

---

## 12. Known Gaps and Risks (carried forward / newly observed)

These are observations, not action items: recorded here so future work doesn't have to rediscover them:

1. **Two unreconciled trust-tier vocabularies**: `configs/sources.yaml`/`source_credibility.py` (highest/high/medium/low/experimental) vs. `app/verticals/scoring.py` (official/primary/trusted_media/broad_media/social_optional). Same concept, different scale, no mapping between them.
2. **Duplicated GARCH implementations**: `app/risk/garch.py` and `app/ml/garch_var.py` both fit GARCH(1,1) with a 60-observation minimum. Confirm whether this is deliberate separation of concerns (reporting vs. VaR) before consolidating.
3. **Hardcoded reference data with no refresh mechanism**: ESG exclusion symbol lists (`exclusion_taxonomy.py`) and bond ETF reference data (`bonds/service.py`) are static Python dicts. Both will silently go stale as companies change classification or bond durations roll.
4. **Geopolitics vertical has no briefing section**: `build_section()` returns `None`; it only contributes to breaking-alert candidates. If the intent is a full geopolitics section eventually, that's unbuilt, not broken.
5. **Docker healthcheck is shallow**: it only confirms `app.cli` imports, not that the scheduler loop is actually ticking or that any provider is reachable. A hung scheduler with a clean import would still report healthy.
6. **Web control centre has no auth layer visible in this audit**: it binds to localhost by default and the compose file recommends a reverse proxy for remote access, but no authentication code was found in `app/web/app.py` during this pass: if remote access is ever enabled, that needs explicit attention first.
7. **`macro_calendar.yaml` is manually maintained seed data**: it is explicitly not live-fetched. Anyone relying on it for forward-looking macro events needs to keep it updated by hand or risk briefings citing stale or missing event dates.

---

## 13. Relationship to Other Docs

- `docs/architecture.md`: short, conceptual, three-product-module framing; still accurate at a high level, this document supersedes it on file-level detail.
- `docs/SESSION_DESIGN.md`: authoritative on per-session content rules and the send-decision matrix; this document summarises it (Section 3.4) but defers to it for exact session-by-session section lists.
- `docs/AUDIT_CURRENT_STATE.md` (2026-05-10): prior product-level audit, useful for what was true a month before this document; this document is the more current code-level ground truth.
- `docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md`: forward-looking strategy for evolving the news/research layer specifically; this document is the present-state map the strategy doc was written against.
- `docs/NEWS_TREND_RADAR_PLAN.md`, `docs/LLM_VERTICAL_SHADOW_PLAN.md`, `docs/VERTICAL_INTELLIGENCE_API_PLAN.md`, `docs/FUTURE_ENHANCEMENTS.md`: forward-looking plans for specific subsystems; not restated here since they describe what doesn't exist yet rather than what does.

---

## 14. IPO and Private-Company Intelligence

Added June 2026. Introduces a parallel intelligence layer for private companies and IPO events that operates entirely without LLM authority, no paid APIs required.

### Registry (`configs/private_companies.yaml`)

13 companies tracked at launch: OpenAI, Anthropic, Stripe, Databricks, Revolut, Canva, Discord, Anduril, xAI, SpaceX, Klarna (listed), SHEIN, Figma.

Each entry contains: `canonical_name`, `aliases`, `status`, `status_confidence`, `official_domains`, `newsroom_urls`, `sec_cik` (where public), `proposed_ticker`, `public_peers` (ticker to relationship + evidence), `suppliers`, `sector_etfs`, `themes`.

Status values: `private`, `confidential_filing`, `public_filing`, `roadshow`, `priced`, `listed`, `postponed`, `withdrawn`, `acquired`.

### Providers

| Provider | Source | Key required |
|---|---|---|
| `IpoEdgarProvider` | SEC EDGAR EFTS + Submissions API | No |
| `PrivateCompanyNewsroomProvider` | Official company newsrooms (allowlist only) | No |
| `IpoCalendarProvider` | Nasdaq IPO calendar + FMP (optional) | No (FMP optional) |

All are `BaseProvider` subclasses. Rate limits: EDGAR 0.13 s, newsrooms 2.0 s, calendar 1.5 s.

### State persistence (`app/sources/primary/ipo_store.py`)

JSON files at `data/cache/ipo/{company_id}.json`. Tracks: content fingerprints (change detection), last poll timestamps (4-hour minimum interval), known accession numbers (dedup), IPO status history, filing events log (capped at 100 entries per company).

### Event types and scores

| Event type | Importance | Description |
|---|---|---|
| `ipo_pricing` | 0.92 | 424B4 final prospectus |
| `ipo_filing` | 0.88 | S-1 / F-1 initial registration |
| `ipo_listing` | 0.85 | EFFECT / 8-A12B — company goes public |
| `ipo_withdrawal` | 0.82 | RW — offering withdrawn |
| `central_bank_statement` | 0.92 | Fed / BoE statement |
| `ipo_amendment` | 0.75 | S-1/A / F-1/A |
| `private_funding` | 0.78 | Official funding announcement |
| `ipo_readthrough` | 0.68 | Derived event for public peer |
| `ipo_calendar` | 0.55 | Calendar estimate (low confidence) |

### Read-through mapping

When a private-company event fires, `IpoIntelligenceService._generate_readthrough_events()` emits one `ipo_readthrough` NormalisedEvent per public peer in the registry. Speculative (`speculative_readthrough`) relationships are suppressed for low-confidence events. Watchlist filtering is respected.

### CLI

```
briefly ipo-status                   # show all companies
briefly ipo-status --upcoming        # filter to active IPO process
briefly ipo-status --company openai  # single company
briefly ipo-status --live            # fetch EDGAR + newsrooms before display
```

### Integration point

`NewsDataService.__init__` instantiates `IpoIntelligenceService`. `fetch_all(watchlist)` calls `ipo.fetch_all(watchlist)` after existing providers. No live briefing-format changes: IPO events flow through the existing scoring, dedup, and section-assignment pipeline unchanged.
