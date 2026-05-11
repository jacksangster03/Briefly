# Briefly UX Simplification Plan (Summary-First, Progressive Disclosure)

## Design Goal
A first-time user should answer, in under 60 seconds:
1. What happened in markets?
2. What matters to my portfolio?
3. What is sending, and where?
4. What needs attention now?

## Principles
- Summary-first, details on demand.
- Default to business labels; keep technical jargon behind “Advanced”.
- Preserve all existing functionality; reduce visual and conceptual clutter.
- Consistent status semantics (`ok`, `partial`, `unavailable`, `live`, `preview`, `dry-run`).

---

## Proposed Information Architecture
1. **Home / Command Centre**
2. **Briefings**
3. **Portfolio**
4. **Macro**
5. **News Intelligence**
6. **Verticals**
7. **Alerts & Delivery**
8. **Diagnostics**
9. **Settings**

---

## Section-level Intent
## 1) Home / Command Centre
Answers: “What changed? what matters now? what should I do next?”
- Today status strip: session state, next eligible send, active delivery channels.
- Market snapshot card (top 3 items).
- Portfolio impact card.
- Top stories card (3 items).
- Macro policy watch chip card.
- System health card (providers + delivery reliability).
- Quick actions:
  - Preview next briefing
  - Open macro dashboard
  - Open delivery log
  - Edit alerts

## 2) Briefings
Answers: “What was sent and what will be sent?”
- Session timeline (today).
- Latest sent content preview.
- Next session preview (explicit `QA preview`, `not live`).
- Channel status and per-session delivery outcome.
- Session module toggles (advanced).

## 3) Portfolio
Layered experience:
- Beginner: concentration, risk level, top exposures, next actions.
- Intermediate: attribution, scenarios, risk decomposition.
- Advanced: CMA, simulation lab, benchmark internals.

## 4) Macro
- Signal strip: Fed/ECB + inflation/labour/rates.
- Region cards: US, Eurozone, UK, Spain, Japan, China.
- Catalyst calendar + portfolio lens.
- Toggle: Simple / Expert.

## 5) News Intelligence
- Included stories + suppressed queue.
- Dedupe clusters.
- Breaking candidates and rejection reasons.
- Label quality and unlabelled review queue.

## 6) Verticals
- Card grid per vertical with mode/status/source health.
- “Why this vertical is active” explanation.
- Healthcare live card + planned placeholders.

## 7) Alerts & Delivery
- Delivery channel matrix by message type.
- Failure-alert controls (including Telegram suppression).
- Quiet hours and weekend mode.
- Last send / last failure / cooldown.

## 8) Diagnostics
- Provider health logs.
- Session audit.
- Delivery history.
- Vertical history.
- Macro source status.
- Debug/CLI helpers.

## 9) Settings
- Basic vs Advanced tabs.
- Keep power controls but default users to minimal-impact set.

---

## Interaction Improvements
- Collapsible cards and “Show advanced” drawers.
- Route-level simple/expert mode toggle.
- Copy-to-clipboard CLI command buttons.
- Inline tooltips with deterministic safety notes.
- Empty-state guidance with one recommended next action.
- Status badges with consistent color semantics:
  - Green: `ok/sent/live`
  - Amber: `partial/degraded/stale`
  - Red: `failed/error`
  - Grey: `disabled/unavailable/preview`

---

## Route / Template Touch Targets (UX-only)
- `app/web/app.py` (navigation context + page grouping only)
- `app/web/templates/ui_home.html`
- `app/web/templates/settings.html`
- `app/web/templates/partials/settings_root.html`
- `app/web/templates/macro_dashboard.html`
- `app/web/templates/watchlist_chart_explorer.html`
- `static/css/ui-shell.css`

No runtime scheduler/delivery/provider logic changes required for UX phases.

---

## Phase Roadmap
## UX-1: Navigation + Home simplification
- Introduce Command Centre layout.
- Add status summary cards.
- Keep existing links reachable.

## UX-2: Briefings + Alerts & Delivery control centre
- Status: implemented.
- `/ui/briefing` is now a dedicated control centre with:
  - template-aware timeline
  - channel matrix
  - alert controls summary
  - latest records
  - safe session-preview command panel
  - diagnostics links

## UX-3: Macro simplification
- Status: implemented.
- `/ui/briefing/macro` now defaults to **Simple** mode with:
  - macro signal hero
  - region card grid (US, Eurozone, UK, Spain, Japan, China)
  - inflation/labour/rates summary cards
  - compact catalyst calendar
  - compact portfolio lens
  - collapsed data quality section
- `mode=expert` expands drivers, missing-field caveats, and advanced details using the same payload.

## UX-4: Portfolio progressive disclosure
- Status: implemented.
- `/ui/portfolio` now defaults to Overview with progressive disclosure:
  - `view=overview`
  - `view=intermediate`
  - `view=scenarios`
  - `view=advanced`
- Existing advanced portfolio routes remain unchanged and linked from the advanced view.

## UX-5: News Intelligence review UI
- Status: implemented.
- `/ui/news` now provides a visual review console with tabs for:
  - overview
  - included stories
  - suppressed stories
  - breaking candidates
  - label review queue
  - classifier health
  - sources/provider contribution
- Empty-state flow includes copyable CLI commands when local review data is not yet populated.

## UX-6: Verticals polish
- Card-based vertical lifecycle and diagnostics.

## UX-7: Diagnostics consolidation
- Move noisy internals to one bounded area.

## UX-8: Docs finalization
- Align README + docs with final UX flows.
