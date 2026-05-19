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

## Environment/config quick matrix

| Setting | Default | Purpose |
|---|---|---|
| `VERTICALS_INCLUDE_IN_BRIEFING` | `false` | Keep vertical sections shadow/off by default |
| `VERTICALS_BRIEFING_SESSIONS` | `["morning"]` | Sessions eligible for compact shadow block |
| `VERTICALS_GEOPOLITICS_ENABLED` | `false` | Geopolitics live section toggle (foundation remains diagnostics-capable) |
| `VERTICALS_HEALTHCARE_ENABLED` | `true` (profile-dependent) | Healthcare mode compatibility with existing behaviour |
| `VERTICALS_AI_TECH_ENABLED` | `false` | AI/Tech live section toggle |
| `SEC_USER_AGENT` | required etiquette string | Required by SEC EDGAR access policy |

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

### Free/no-key/key-required matrix

| Source | Key required | Notes |
|---|---|---|
| GDELT | No | Open API; geopolitics density/coverage input |
| ClinicalTrials.gov | No | Official API v2 |
| arXiv | No | Research trend context |
| SEC EDGAR | No API key | Requires `SEC_USER_AGENT` identifier |
| openFDA | Optional free key | Recommended for higher reliability/rate limits |
| GitHub | Optional token | Token recommended for higher limits/private contexts |

### Source health behaviour
- GDELT failures/timeouts are represented as `status=error` with `last_error`.
- Missing `SEC_USER_AGENT` marks SEC AI/Tech source as `status=disabled` with reason `missing SEC_USER_AGENT`.
- arXiv/GitHub failures degrade safely into diagnostics without crashing plugin/briefing paths.
- Healthcare source status remains backward compatible with existing `sec/openfda/clinicaltrials/ema` keys.

## Persistence
- Generic event archive:
  - `vertical_source_events` (`VerticalSourceEventRecord`)
  - unique key: `(vertical, source_name, payload_hash)` prevents duplicate inserts.
  - metadata-only persistence; no full raw copyrighted text storage.
  - upsert path is best-effort and non-blocking (DB failures do not crash briefing generation).
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

## Known limitations (Phase 1)
- Geopolitics and AI/Tech are diagnostics-first; full rich sections are intentionally not enabled live by default.
- arXiv/GitHub outputs are trend/context signals and cannot independently claim market-confirmed catalysts.
- No scraping/page-monitoring collectors are implemented in this phase.

## Phase 1b recommendations
- Add official policy/sanctions feeds for geopolitics as higher-trust confirmations.
- Add richer SEC filing taxonomy for AI capex/regulation/event typing.
- Add intraday materiality gate for optional vertical shadow bullets (if intraday sessions are enabled in preferences).
- Add explicit per-source cooldown/rate-limit visibility to `/ui/diagnostics`.
- Add company IR RSS/page monitor adapters (official/public and robots-compatible only).
- Add optional Hugging Face adapter once endpoint stability/constraints are validated.
- Add Eurostat/ECB macro linkage for richer cross-vertical macro policy context.
