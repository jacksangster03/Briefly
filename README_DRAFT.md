# Briefly (Draft Restructured README)

Briefly is a local-first deterministic market intelligence platform that generates session-aware market briefings, portfolio-aware context, and operational delivery diagnostics across Telegram, email, CLI, and a local web control centre.

---

## 1) What Briefly is
Briefly combines three systems:
- **Market Briefing Engine**: multi-session market updates with deterministic freshness and delta logic.
- **Portfolio Intelligence Workbench**: holdings-aware risk, attribution, simulation, and policy tooling.
- **Operations Control Layer**: scheduler safety, delivery idempotency, audit logs, and replay/preview tooling.

All core state is local (SQLite). External APIs provide market/news/macro data.

---

## 2) Core value proposition
- Deterministic output for auditable operations.
- Portfolio relevance layered onto market/news context.
- Local-first deployment without cloud orchestration dependency.
- Rich diagnostics to understand exactly what was sent, skipped, or suppressed.

---

## 3) Stable vs experimental boundaries
## Stable (authoritative)
- Scheduler/session routing and idempotent delivery.
- Deterministic market/news/macro formatting and scoring.
- Telegram/email session briefing delivery.
- Session audits, delivery logs, daily summary, schedule status.

## Shadow / non-authoritative
- LLM news classifier (`ENABLE_LLM_NEWS_CLASSIFIER`) is shadow-only by default.
- ML news classifier foundation (`ENABLE_ML_NEWS_CLASSIFIER`) is data/audit-only.

Rule: deterministic rules remain source of truth unless explicitly documented otherwise.

---

## 4) Feature map
- Session briefings: morning, Europe midday, US pre-open, US intraday risk, into close, closing wrap.
- Breaking alerts with freshness and repeat suppression gates.
- Macro Policy Dashboard (`/ui/briefing/macro`) and policy signals.
- Watchlist chart explorer (`/ui/briefing/charts/watchlist`).
- Vertical framework with healthcare plugin and diagnostics.
- Portfolio stack: policy, allocation, risk, attribution, simulation, rebalancing.
- Operational diagnostics: `session-audit`, `delivery-log`, `daily-summary`, `schedule-status`, `verticals-status`.

---

## 5) Architecture (high-level)
1. `app/scheduler.py` polls session cadence + breaking checks.
2. `app/main.py` resolves session routing and orchestrates generation + delivery.
3. Generators in `app/briefing/*` build deterministic session payloads.
4. Providers in `app/data_sources/*` fetch market/news/macro data.
5. Delivery wrappers in `app/messaging/*` send via Telegram/email.
6. Persistence in `app/db/models.py` stores send state, logs, snapshots, preferences, analytics.
7. Web control centre in `app/web/app.py` renders operational and analytics pages.

---

## 6) Quick start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.cli init-db
python -m app.cli web
```

Web UI: <http://127.0.0.1:8080/ui>

---

## 7) Required vs optional API keys
## Required for practical operation
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (if using Telegram delivery)
- `EMAIL_*` credentials (if using email delivery)
- At least one market/news data provider key set for meaningful live output

## Optional / feature-specific
- `FRED_API_KEY` for broader macro coverage
- healthcare source flags/URLs for vertical ingestion diagnostics
- LLM/ML classifier env vars for shadow workflows only

See `.env.example` for full list and comments.

---

## 8) Delivery, idempotency, and weekend behavior
## Idempotency
- Session sends are keyed by profile/date/session/channel (`session_send_state`).
- Scheduler restarts do not duplicate already successful sends.

## Weekend routing
- Weekend mode determines Saturday/Sunday eligibility.
- Weekday session keys are suppressed on weekends by default routing logic.

---

## 9) Delivery failure alerts (and anti-spam controls)
Failure alerts are separate from normal brief delivery and can be configured independently.

Preferences:
- `delivery.failure_alerts_enabled`
- `delivery.failure_alert_channels`
- `delivery.failure_alert_cooldown_minutes`

Recommended local setting to avoid Telegram noise:
```bash
python -m app.cli prefs-set --key delivery.failure_alerts_enabled --value true
python -m app.cli prefs-set --key delivery.failure_alert_channels --value '["email"]'
```

Disable all user-facing failure alerts:
```bash
python -m app.cli prefs-set --key delivery.failure_alerts_enabled --value false
```

---

## 10) Preview, replay, and backfill modes
## QA preview (no live writes)
```bash
python -m app.cli session-preview --session morning --show-output
```

## Catch-up (today elapsed sessions)
```bash
python -m app.cli catch-up
```

## Historical backfill (labeled not live)
```bash
python -m app.cli backfill --date 2026-05-09 --send telegram,email
```

## Snapshot replay
```bash
python -m app.cli snapshots replay --date yesterday --session morning
```

---

## 11) Key operational commands
```bash
python -m app.cli schedule-status
python -m app.cli daily-summary --date today
python -m app.cli delivery-log --date today
python -m app.cli session-audit --date today
python -m app.cli session-audit --date today --live-check --classifier-details --vertical-details
python -m app.cli verticals-status --verbose
```

---

## 11.1) Briefings & Delivery control centre (`/ui/briefing`)
Use `/ui/briefing` for a summary-first operational view of scheduled briefings and alert routing.

What it shows:
- Template-aware session timeline for the active profile (weekday or weekend-aware routing).
- Current/next session and scheduler state.
- Delivery channel matrix (Telegram vs email) for morning, intraday, breaking, and delivery-failure alerts.
- Clear separation between:
  - normal briefing delivery
  - breaking market alerts
  - delivery failure alerts (operational notifications)
- Latest delivery records with compact sent/failed badges.
- Safe QA preview commands.

Failure-alert quick controls:
```bash
# Disable all user-facing delivery failure alerts
python -m app.cli prefs-set --key delivery.failure_alerts_enabled --value false

# Keep failure alerts, but email only
python -m app.cli prefs-set --key delivery.failure_alerts_enabled --value true
python -m app.cli prefs-set --key delivery.failure_alert_channels --value '["email"]'
```

Safe preview commands:
```bash
python -m app.cli session-preview --session morning --show-output
python -m app.cli session-preview --session us_pre_open --show-output
```

---

## 12) Macro Policy Dashboard
UI: `/ui/briefing/macro`  
API: `/api/v1/profile/{profile}/briefing/macro`

View modes:
- `simple` (default): summary-first, region-aware cards for US/Fed, Eurozone/ECB, UK/BoE, Spain (ECB-linked country lens), Japan/BoJ, China/PBoC.
- `expert`: full drivers, missing fields, risks, and deeper source/freshness context from the same payload.

Safety wording is explicit throughout: deterministic signal, not a forecast, not market-implied probability.

Includes:
- central bank panel
- inflation tracker
- labour tracker
- rates/yield curve panel
- catalyst calendar
- portfolio lens
- deterministic policy signal layer with regional structure

Signal outputs are deterministic policy-bias labels, not probabilities, not forecasts.

---

## 13) Macro Policy Watch (briefing block)
Disabled by default.

Enable:
```bash
python -m app.cli prefs-set --key briefing.include_macro_policy_watch --value true
python -m app.cli prefs-set --key briefing.macro_policy_watch_sessions --value '["morning"]'
```

Optional US pre-open inclusion:
```bash
python -m app.cli prefs-set --key briefing.macro_policy_watch_sessions --value '["morning","us_pre_open"]'
```

---

## 14) News intelligence and classifier
- Deterministic taxonomy + freshness/update state + suppression reasons.
- Breaking eligibility requires strict deterministic gates.
- ML/LLM layers are shadow-only by default.
- Labeling/exports:
  - `news-review`
  - `news-label-set`
  - `news-dataset-export`
  - `news-label-quality`

---

## 15) Vertical intelligence and healthcare
- Generic vertical framework under `app/verticals`.
- Healthcare plugin under `app/verticals/plugins/healthcare.py`.
- Diagnostics:
  - `verticals-status`
  - `verticals-history`
  - `session-audit --vertical-details`

Healthcare official sources are feature-flagged and diagnostics-first.

---

## 16) Testing
Full suite:
```bash
python -m pytest app/tests -q
```

Focused delivery safety tests:
```bash
python -m pytest app/tests/test_delivery_failure_alert.py app/tests/test_scheduler.py app/tests/test_daily_summary.py -q
```

---

## 17) Troubleshooting (quick)
- Unexpected send behavior: `schedule-status`, `daily-summary`, `delivery-log`.
- Missing session detail: `session-audit --live-check`.
- Alert noise: verify `delivery.failure_alert_*` prefs.
- Provider uncertainty: check provider health sections and logs.

---

## 18) Security and secret hygiene
- Never commit `.env`.
- Provider URLs/headers should be redacted in logs.
- If any key appears in logs/chat, rotate immediately.

---

## 19) Known limitations
- Some regional macro signals are partial/unavailable depending source coverage.
- Vertical official-source ingestion varies by enabled flags and source reliability.
- UI currently exposes advanced controls that can overwhelm basic users.

---

## 20) Near-term roadmap
1. UX simplification and navigation cleanup.
2. Briefings + Alerts control centre clarity.
3. News intelligence review UI.
4. Vertical UX and diagnostics polish.

For full plans, see:
- `docs/AUDIT_CURRENT_STATE.md`
- `docs/UX_SIMPLIFICATION_PLAN.md`
- `docs/README_RESTRUCTURE_PLAN.md`
