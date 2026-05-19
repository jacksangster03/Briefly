# Vertical Intelligence API Plan (Phase 1)

Phase 1 adds API/RSS-first vertical foundations for:
- `geopolitics`
- `healthcare`
- `ai_tech`

## Deterministic Authority
- Deterministic scoring/ranking remains authoritative.
- LLM output is not used for trust, ranking, alert eligibility, or delivery gating.

## Shared Event Contract
- `app/verticals/events.py` defines `VerticalEvent`.
- Events are metadata-first (title/summary/ids), with bounded payload references (`payload_hash`).
- Full article text is not persisted.

## Sources
- Geopolitics:
  - GDELT (primary), with optional broad-media confirmation scaffolding.
- Healthcare:
  - Existing SEC/openFDA/ClinicalTrials/EMA adapters are preserved.
  - Compatibility bridge maps healthcare source events into shared `VerticalEvent`.
- AI/Tech:
  - SEC EDGAR (primary official)
  - arXiv (primary research trend signal)
  - GitHub source is scaffolded as optional/fail-soft in this phase.

## Persistence
- Generic event archive:
  - `vertical_source_events` (`VerticalSourceEventRecord`)
- Existing run diagnostics retained:
  - `vertical_run_diagnostics`

## UI / Diagnostics
- `/ui/briefing/verticals` now surfaces Healthcare, Geopolitics, and AI/Tech cards.
- Source health is explicit per vertical source (`ok|disabled|rate_limited|error|stub_inactive`).

## Briefing Integration
- Shadow-only by default:
  - `verticals.include_in_briefing = false` (default behavior)
  - `verticals.briefing_sessions = ["morning"]`
- Optional compact summaries are deterministic and non-authoritative.
