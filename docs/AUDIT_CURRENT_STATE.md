# Briefly Current-State Audit (May 10, 2026)

## Executive Summary
Briefly is now a serious local-first intelligence platform with strong deterministic controls and unusually good operational safeguards for a solo/local product (idempotency, session routing, replay labeling, failure-alert dedupe, vertical diagnostics, macro signal caveats). The core engine is strong; the main weakness is product surface complexity and fragmented discoverability across CLI, web pages, and preferences.

Key conclusion: **the highest-value next work is UX simplification and information architecture cleanup**, not more feature breadth.

---

## A. Product Overview
### What Briefly currently does
- Deterministic multi-session market briefing generation and delivery (Telegram + email).
- Session-aware incremental updates (morning baseline, later deltas).
- Breaking alert pipeline with freshness gating and storyline controls.
- Portfolio analytics workbench (policy, allocation, risk, scenarios, attribution, simulation).
- Macro Policy Dashboard + deterministic policy signal layer.
- Vertical intelligence framework with healthcare as first plugin.
- Audit/ops tooling (session-audit, schedule-status, delivery-log, daily-summary, vertical diagnostics).
- ML data capture foundation (shadow only; deterministic classifier remains authoritative).

### Value proposition
- “Bloomberg-lite workflow” locally: daily market + portfolio context + operationally safe delivery.
- Deterministic auditability over opaque AI behavior.
- No cloud control plane dependency for core operation.

### Strong today
- Deterministic explainability in macro/news/session layers.
- Delivery/idempotency hardening depth.
- Breadth of portfolio modules and testing density.

### Confusing / overbuilt today
- UI surface spread across many pages/partials with mixed conceptual hierarchy.
- Preference keys are powerful but difficult for basic users to reason about.
- Multiple “status-like” views (daily-summary, delivery-log, schedule-status, session-audit) with different scopes can confuse non-technical users.

---

## B. Architecture Overview
## Runtime entrypoints
- CLI: `app/cli.py`
- Scheduler loop: `app/scheduler.py`
- Core orchestration: `app/main.py`
- Web app: `app/web/app.py`

## Core flow (weekday scheduler)
1. APScheduler job calls `run_session_brief(..., respect_cadence=True, command_source="scheduler")`.
2. Session routing selects canonical session key by template/time window/weekend mode.
3. Generator builds briefing payload (market/news/macro/portfolio/sections/charts).
4. Delivery pipeline sends per-channel with claim/finalize idempotency guards.
5. Snapshot persisted for scheduler live runs.
6. Failure alert path invoked if unresolved channel failure and alert controls allow.

## Main subsystems
- Briefing generation: `app/briefing/*` (`morning_generator.py`, `intraday_generator.py`, formatters, chart stack).
- News pipeline: `app/processing/*` + `app/briefing/news_classifier.py`.
- Market/macro providers: `app/data_sources/*`, provider adapters in `app/data_sources/providers/*`.
- Portfolio stack: `app/portfolio`, `app/risk`, `app/cma`, `app/simulation`, `app/rebalancing`, `app/attribution`.
- Verticals: `app/verticals/*` with healthcare plugin wrapping `app/healthcare/*`.
- Macro dashboard/signals: `app/briefing/macro_policy_service.py`, `macro_policy_signals.py`, `macro_policy_calendar.py`, `macro_policy_lens.py`.
- Messaging: `app/messaging/telegram.py`, `app/messaging/email.py`.
- Persistence: SQLAlchemy models in `app/db/models.py`.

## Web/control centre
- Routes in `app/web/app.py` (briefing, portfolio, macro, verticals, watchlist explorer, history, diagnostics).
- Templates in `app/web/templates/*` (currently section-heavy and broad).

## Database highlights
- Delivery/state: `sent_messages`, `session_send_state`, `delivery_failure_alert_state`.
- Audit/meta: `provider_health`, `regime_snapshots`, `vertical_run_diagnostics`.
- ML label foundation: `news_classifier_labels`, `news_classifier_shadow_runs`.
- Portfolio analytics tables across policy/allocation/risk/simulation/attribution.

---

## C. Delivery/Scheduler State Audit
## What is safe now
- Single unified session cadence job with `max_instances=1` reduces overlap races.
- Session idempotency claim/finalize uses durable DB records.
- Weekend routing guard suppresses weekday sessions on Sat/Sun.
- Startup catch-up path exists for missed elapsed sessions.
- Failure-alert stale-date guard and cooldown dedupe are present.
- Manual/test modes are visibly labeled (`day-replay`, backfill banners, snapshot replay labels).

## Remaining fragility
- Operator confusion when interpreting multiple status surfaces with different scopes.
- Failure alerts are still operationally sensitive if channels misconfigured (now controllable; defaults improved).
- Historic/test rows in shared DB can still confuse user-facing summaries unless scope labels are clear.

## Priority improvements
1. Unified “Delivery & Alerts” control centre panel with clear scope labels.
2. Explain “what each status page counts” in UI and README.
3. Add quick health triage card (“live sends”, “alerts suppressed”, “last failure reason”).

---

## D. News Classifier Pipeline Audit
## Current state
- Deterministic taxonomy with story type/freshness/update/suppress reason/breaking eligibility metadata.
- Late discovery and stale suppression gates are in place.
- ML classifier foundation is shadow/data-only; non-authoritative.
- Review tools: `news-review`, `news-label-set`, `news-dataset-export`, `news-label-quality`.

## Strengths
- Strong safety separation between classification metadata and final send authority.
- Good auditability through session-audit + label tables.

## Risks
- Heuristic drift as categories expand can increase false positives/negatives.
- Quality depends on source reliability and ticker/entity mapping quality.
- Human review workflow exists but not yet optimized in UI (CLI-first).

## Next improvements
- Build News Intelligence UI queue for unlabelled/high-disagreement items.
- Add confidence calibration diagnostics by source and story type.

---

## E. Macro Policy Dashboard Audit
## Current state
- Routes:
  - UI: `/ui/briefing/macro`
  - API: `/api/v1/profile/{profile}/briefing/macro`
- Payload includes:
  - `central_bank_policy`, `inflation_tracker`, `labour_tracker`, `rates_yield_curve_panel`,
  - `macro_catalyst_calendar`, `portfolio_lens`, `policy_signals`, `data_basis`.
- Regional signal scaffolding exists (US, Eurozone, UK, Spain, Japan, China) with explicit partial/unavailable handling.

## Strengths
- Deterministic methodology notes and caveat language present.
- Dashboard hardened against provider/missing-field 500 failures.
- Explicit transformation metadata for inflation metrics.

## Gaps
- Regional completeness varies; UK/Spain improved but still partial for many fields.
- Placeholder regions can be noisy if surfaced without progressive disclosure.
- Needs simple/expert mode split for usability.

---

## F. Portfolio System Audit
## Current state
- Deep stack: policy, allocation, benchmark, risk, CMA, rebalancing, attribution, simulation, reports.
- Broad table coverage and good test depth.

## Strengths
- Institutional feature breadth.
- Local-first persisted state.

## UX challenge
- Basic users can be overwhelmed by depth and terminology.
- Needs progressive disclosure and guided “next action” framing.

---

## G. Vertical Intelligence Audit
## Current state
- Generic plugin framework exists (`app/verticals/base.py`, `engine.py`, `registry.py`, `config.py`).
- Healthcare plugin integrated with diagnostics and source status reporting.
- Official sources integration is controlled/flagged and diagnostics-first.

## Strengths
- Reusable architecture started correctly (plugin model + diagnostics persistence).
- Non-blocking behavior and mode controls.

## Gaps
- UX still feels framework-internal rather than end-user narrative.
- Future vertical placeholders need consistent product-level positioning.

---

## H. Web UI / Control Centre Audit
## Key routes observed
- Home shell: `/ui`, `/ui/settings`
- Briefing: `/ui/briefing`, `/ui/briefing/morning`, `/ui/briefing/delivery`, `/ui/briefing/verticals`, `/ui/briefing/macro`, `/ui/briefing/charts/watchlist`, `/ui/briefing/history`
- Portfolio workspaces and diagnostics routes

## Strengths
- Functionality coverage is broad.
- Watchlist explorer and macro pages provide differentiated workflows.

## Friction
- Navigation model is deep and concept-heavy.
- Some pages surface implementation detail before user intent.
- Diagnostics and operational controls are not clearly tiered (basic vs advanced).

---

## I. Preferences/Profile Audit
## Current state
- Strong preference normalization/validation layer (`preferences_service.py`).
- Overrides loaded into profile at runtime.
- Many keys (delivery, sections, verticals, macro watch, healthcare, snapshots).

## Friction
- Too many keys exposed conceptually for basic users.
- Some overlap between legacy keys and newer vertical/mode keys requires clearer canonical path.

## Recommendation
- Introduce “Basic Settings” (high-impact 10-15 controls) and “Advanced Settings” (everything else).

---

## J. Tests and Reliability
## Current state
- Very high test count and broad coverage across delivery/session/macro/verticals/news.
- Full suite currently green in recent runs.

## Reliability observations
- Strong regression posture.
- Warnings remain (template deprecation, simulation runtime warnings) but non-blocking.

## Suggested additions
- UI contract tests for simplified nav and mode toggles.
- Cross-page scope-consistency tests for counts/status labels.

---

## Top Product Risks / Frictions
1. Discoverability and cognitive load (not core engine correctness).
2. Status/count interpretation confusion for non-technical users.
3. Feature breadth outpacing opinionated workflows.

## Highest-value next moves
1. UX simplification and IA cleanup.
2. Delivery/alerts control centre clarity.
3. README restructuring into product-quality reference with stable vs experimental boundaries.

