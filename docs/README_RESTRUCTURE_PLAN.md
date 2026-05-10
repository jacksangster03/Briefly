# README Restructure Plan

## Objective
Turn README into a product-quality, developer-usable reference that:
- Explains **what Briefly is** before implementation detail.
- Separates **stable/live** behavior from **experimental/shadow** features.
- Makes operational controls (delivery, weekends, failure alerts, previews) explicit.

---

## Proposed README Outline
1. Project name + one-line summary
2. What Briefly is
3. Core value proposition
4. Feature map (briefing, portfolio, macro, news, verticals, delivery, diagnostics)
5. Architecture map (CLI, scheduler, generators, providers, storage, web)
6. Deterministic vs ML/LLM roles (authority boundaries)
7. Quick start
8. Environment setup
9. Required vs optional API keys
10. Run modes (live, dry-run, preview, replay, backfill)
11. Scheduler/service management
12. CLI guide by workflow
13. Web UI guide by workspace
14. Profiles and preferences
15. Delivery channels and idempotency model
16. Weekend routing behavior
17. Failure alerts (and how to disable Telegram failure messages)
18. Session audit and delivery diagnostics
19. Macro Policy Dashboard + Policy Signals
20. Macro Policy Watch briefing block
21. Portfolio analytics modules
22. News intelligence and classifier pipeline
23. Vertical intelligence + healthcare status
24. Testing strategy and standard commands
25. Troubleshooting matrix
26. Security/secrets handling
27. Roadmap and known limitations
28. Glossary

---

## Stable vs Experimental Labeling
Use consistent tags in headings:
- `[Stable]`
- `[Beta]`
- `[Experimental]`
- `[Shadow-only]`

Examples:
- `LLM news classifier` -> `[Shadow-only]`
- `ML news classifier` -> `[Shadow-only]`
- `Healthcare official sources` -> `[Beta]` or `[Experimental]` depending default flags.

---

## Cross-link Strategy
README should link to:
- `docs/AUDIT_CURRENT_STATE.md`
- `docs/UX_SIMPLIFICATION_PLAN.md`
- `docs/deployment.md`
- `docs/api_keys.md`
- `docs/scheduling.md`

---

## Required Operational Clarity Blocks
1. **Delivery failure alerts**
   - explain config:
     - `delivery.failure_alerts_enabled`
     - `delivery.failure_alert_channels`
     - `delivery.failure_alert_cooldown_minutes`
   - include exact CLI commands for disable/email-only.

2. **Weekend routing**
   - explicit Sat/Sun session eligibility matrix.

3. **Preview/testing modes**
   - clear distinction:
     - `session-preview` (QA only)
     - `day-replay`
     - `catch-up`
     - `backfill`
     - `snapshots replay`

4. **Count scope caveats**
   - explain what `daily-summary` counts vs `delivery-log`.

