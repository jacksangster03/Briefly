# Briefly Research and Equity Intelligence Agent Strategy

Status: strategy and design document (Phase 1). No major runtime behaviour changes are made by this document. It is written to be implementable phase by phase by another agent.

Author context: this evolves the existing `market-briefing-bot` / Briefly codebase (repo `jacksangster03/Briefly`) from a scheduled six-session market briefing bot into a stronger research and equity-intelligence agent, while preserving the current scheduler, delivery, session structure, providers, deterministic scoring, diagnostics and fail-soft behaviour.

Guiding principle: API and official sources first, RSS second, controlled scraping last. Deterministic scoring stays authoritative. LLM usage is shadow-only or clearly bounded. Briefly becomes a reliable research agent, not a random web scraper.

---

## Part 1 - Repo Audit (what exists and where)

This is grounded in the current tree. File paths are real.

### Runtime entrypoints and scheduler
- `app/main.py` (2,986 lines): core orchestration. Exposes `run_session_brief`, `run_breaking_check`, `run_catch_up`. Builds the briefing payload (market/news/macro/portfolio/sections/charts), runs the send decision, delivers per channel, persists snapshots, and triggers failure alerts.
- `app/scheduler.py` (231 lines): APScheduler `BlockingScheduler`. A single unified `_run_session_cadence_check` job (`max_instances=1`) covers all six session windows on a polling interval, eliminating the two-job race. Separate cron jobs poll breaking alerts. Startup catch-up (`_run_startup_catchup`) fills sessions that elapsed while the process was down, reusing idempotency infra. A file lock (`data/state/scheduler.lock`) prevents double-start.
- `app/cli.py` (2,459 lines, Click): commands include `brief`, `morning`, `intraday`, `midday`, `preopen`, `close`, `breaking`, `session-preview`, `day-replay`, `session-send`, `catch-up`, `backfill`, `schedule-status`, `daily-summary`, `session-audit`, `verticals-status`, `verticals-history`, `prefs-show/set/unset/reset`, `news-review`, `news-label-set`, `news-dataset-export`, `news-label-quality`, `snapshots list/show/prune/replay`, `llm-usage summary/list`, `delivery-log`, `portfolio-snapshot`, `validation presets/run/sweep/fuzz`, `simulation run/runs`, `status`, `preflight`, `quote`, `news`, `web`, `init-db`, `version`.
- `app/settings.py` (237 lines): env-driven `Settings` (provider keys, timeouts, budgets, feature flags). `app/personalization/user_profile.py` loads the user profile from YAML + DB overrides.

### Six briefing sessions and current roles
Defined in `docs/SESSION_DESIGN.md` and routed by `app/briefing/session_routing.py` and `app/cadence/*`:
- Morning Briefing: overnight setup and day plan.
- Europe Midday: Europe confirmation/rejection of morning setup.
- US Pre-Open: Europe-to-US handoff and US open plan.
- US Intraday Risk Check: first-hour confirmation check (degrades to stale snapshot with banner).
- Into Close: late-session drift and close setup.
- Closing Wrap: final day verdict and tomorrow setup.

Session machinery lives across `app/briefing/`: `session_routing.py`, `session_delivery.py`, `session_delta.py`, `session_diagnosis.py`, `session_freshness.py`, `session_materiality.py`, `session_metadata.py`, `session_quality.py`, `session_snapshot.py`, `session_snapshot_service.py`, `session_tape.py`, `session_templates.py`, `market_clock.py`, `market_regions.py`, `regional_lens.py`. Generators: `morning_generator.py`, `intraday_generator.py`, `breaking_generator.py`, `day_replay.py`, `snapshot_replay.py`.

### Market data providers
- Service: `app/data_sources/market_data.py`, fallback chain `app/data_sources/quote_fallback.py`.
- Adapters in `app/data_sources/providers/`: `finnhub.py`, `yfinance_provider.py`, `alpaca.py`. Fallback hierarchy: live -> near_real_time -> delayed -> stale_snapshot -> prior_close -> unavailable (see `docs/SESSION_DESIGN.md`).
- Canonical price resolution and contradiction lints: `app/briefing/trust_contract.py`.

### News providers
- Service: `app/data_sources/news_data.py` wraps `app/data_sources/global_news_hub.py`.
- `GlobalNewsHubService` merges Finnhub, NewsAPI, GDELT, Alpha Vantage news, FMP, Mediastack, Marketaux. Adapters in `app/data_sources/providers/`: `finnhub.py`, `newsapi.py`, `gdelt.py`, `alphavantage_news.py`, `fmp_news.py`, `mediastack.py`, `marketaux.py`, `sec_provider.py`.
- Per-provider daily call budgets (`_consume_budget`), URL canonicalisation, domain extraction, and story-fingerprint dedupe (`title|domain|tickers|hour-bucket`) with quality-tuple tie-break (confidence, cluster_size, source priority, summary length).
- Processing: `app/processing/` (`pipeline.py`, `dedupe.py`, `event_clustering.py`, `relevance_scoring.py`, `personal_relevance.py`, `source_credibility.py`, `article_quality.py`, `sentiment.py`, `cleaners.py`, `event_store.py`).
- Deterministic news taxonomy/freshness: `app/briefing/news_classifier.py` (story types, freshness states, breaking eligibility, suppress reasons). LLM-side shadow classifier: `app/briefing/llm_news_classifier.py`. Audit: `app/briefing/news_audit.py`. Theme assembly: `app/briefing/theme_builder.py`.

### Macro / FRED handling
- `app/data_sources/macro_data.py` plus providers `fred.py`, `ecb.py`, `eurostat.py`. FRED `DEFAULT_SERIES` covers 2Y/10Y/30Y, curve spread, USD index, WTI/Brent, gold, gas, unemployment, CPI.
- Macro policy layer: `app/briefing/macro_policy_service.py`, `macro_policy_signals.py`, `macro_policy_calendar.py`, `macro_policy_lens.py`, plus `app/policy/service.py`. Dashboard at `/ui/briefing/macro` and `/api/v1/profile/{profile}/briefing/macro`.
- FX dollar pulse: `app/briefing/fx_section.py`, `app/fx/`, `docs/FX_DOLLAR_PULSE.md`.

### Portfolio and watchlist logic
- `app/portfolio/service.py`, `app/portfolio/importer.py`; analytics stack across `app/allocation`, `app/attribution`, `app/benchmark`, `app/risk`, `app/cma`, `app/rebalancing`, `app/simulation`, `app/bonds`, `app/esg`, `app/fx`, `app/reports`.
- Briefing-side portfolio surfaces: `app/briefing/portfolio_impact.py`, `portfolio_performance.py`, `valuation_lens.py`, `watchlist_chart_service.py`.
- Watchlists and holdings configured in `configs/watchlists.example.yaml`, `configs/holdings.example.yaml`; relevance weights in `configs/interest_weights.yaml`, `configs/sectors.yaml`, `configs/regions.yaml`. Universe metadata: `app/universe/ticker_metadata.py`, `app/universe/sector_universe.py`.

### Vertical intelligence framework
- `app/verticals/`: `base.py` (plugin protocol + `VerticalRunDiagnostics`), `engine.py` (facade, non-blocking diagnostics, persistence), `registry.py`, `config.py` (modes: off/watch/active/portfolio_linked; sessions default `["morning"]`), `events.py` (`VerticalEvent`, payload-hash), `scoring.py` (deterministic source-tier-weighted score), `source_store.py` (upsert into `vertical_source_events`), `shadow_summary.py` (compact non-authoritative briefing bullets), `healthcare_bridge.py`.
- Plugins in `app/verticals/plugins/`: `geopolitics.py`, `ai_tech.py`, `healthcare.py`. Plugin source collectors in `app/verticals/sources/`: `geopolitics.py`, `ai_tech.py`.
- Source-tier weights in `scoring.py`: official 1.0, primary 0.9, trusted_media 0.75, broad_media 0.55, social_optional 0.35.

### Healthcare vertical
- `app/healthcare/`: `classifier.py`, `scorer.py`, `section_builder.py`, `taxonomy.py`, `schemas.py`, `source_store.py`, and sources `clinicaltrials.py`, `company_ir.py`, `ema.py`, `fda.py`. Persisted to `healthcare_source_events`. The richest vertical: it has source-specific official pipelines, classifier, scorer and section builder.

### Geopolitics vertical
- `app/verticals/plugins/geopolitics.py` + `app/verticals/sources/geopolitics.py`. GDELT-driven, headline-density-heavy. `build_section` returns `None` (shadow-only). `breaking_candidates` filters deterministic score >= 0.8. Off by default.

### AI/tech vertical
- `app/verticals/plugins/ai_tech.py` + `app/verticals/sources/ai_tech.py`. SEC EDGAR (official), arXiv (research trend), GitHub (scaffold/optional). Off by default, diagnostics-first.

### Source diagnostics and provider health
- `ProviderHealthLog` (`provider_health`): per-call latency, status, items, errors.
- `VerticalRunDiagnostics` (`vertical_run_diagnostics`): per profile/date/session/vertical mode, status, counts, source status.
- `GlobalNewsHubService.last_run_stats`: provider contributions, fetched/deduped totals, duplicates suppressed.
- UI: `/ui/diagnostics`, `/ui/briefing/verticals`, `/ui/audit/{view}`.

### Delivery and idempotency
- `app/messaging/telegram.py`, `app/messaging/email.py`; HTML email via `app/briefing/email_formatter.py` and optional `llm_email_renderer.py`.
- Idempotency: `SessionSendState` (claim/finalize, unique scope + idempotency key), `SentMessage`, `CadenceMarker`, `DeliveryFailureAlertState` (cooldown/dedupe of failure alerts). Decision/routing: `app/briefing/send_decision.py`, `session_delivery.py`, `session_routing.py`.

### Stale / fallback behaviour
- `app/briefing/send_decision.py` implements the send-decision matrix (normal / market_only / news_only / degraded_context / suppressed) keyed on `market_data_status` and `news_status`. Stale snapshots are labelled and never the primary basis of a normal send. Session archive: `SessionArchiveSnapshot` (live scheduler runs only).

### Briefing formatter and email structure
- `app/briefing/formatter.py` (Telegram-first), `email_formatter.py` (HTML), `templates.py`, `session_templates.py`, `move_colors.py`, `move_context.py`. Schemas: `app/schemas/briefings.py` (`MorningBriefing`).

### Chart generation
- `app/briefing/chart_builder.py`, `chart_renderer.py`, `morning_charts.py`, `watchlist_chart_service.py`. Static assets in `static/`. User feedback on charts via `UserFeedback` table and `/api/v1/feedback`.

### Trigger board
- "Today's trigger board" / "intraday risk triggers" assembled in the generators and `app/briefing/session_diagnosis.py` (referenced in `app/tests/test_live_risk_board.py`).

### Applied news stack
- Referenced in `app/schemas/briefings.py`, `morning_generator.py`, `email_formatter.py`, `formatter.py`, `session_diagnosis.py`. Tested in `app/tests/test_applied_news_stack.py`. This is the section that maps selected high-signal news into portfolio/watchlist/macro context.

### Trust-contract checks
- `app/briefing/trust_contract.py`: canonical price resolution (one value per asset per run), closed-market filtering for breadth, pre-send contradiction lints, text-similarity guards. `app/briefing/quality_guard.py` adds further pre-send quality gating.

### Env / API setup
- `.env`, `.env.example`, `docs/API_SETUP.md`, `docs/api_keys.md`. Config in `configs/`: `sources.yaml` (provider tiers/trust), `schedules.yaml`, `alert_rules.yaml`, `delivery_preferences.yaml`, `interest_weights.yaml`, `macro_calendar.yaml`, `regions.yaml`, `sectors.yaml`, plus `*.example.yaml`.

### Docs currently present
`docs/`: `API_SETUP.md`, `AUDIT_CURRENT_STATE.md`, `BRIEFING_OUTPUT_QUALITY.md`, `FUTURE_ENHANCEMENTS.md`, `FX_DOLLAR_PULSE.md`, `LLM_VERTICAL_SHADOW_PLAN.md`, `NEWS_TREND_RADAR_PLAN.md`, `README_RESTRUCTURE_PLAN.md`, `SESSION_DESIGN.md`, `UX_SIMPLIFICATION_PLAN.md`, `VERTICAL_INTELLIGENCE_API_PLAN.md`, `api_keys.md`, `architecture.md`, `deployment.md`, `message_examples.md`, `personalization.md`, `product_modules.md`, `provider_strategy.md`, `roadmap.md`, `scheduling.md`, `setup.md`, `trading_safety.md`, plus `docs/superpowers/`.

### Tests
126 test files under `app/tests/` (delivery, session, macro, verticals, news, applied news stack, live risk board). Full suite reported green in recent audit.

### Audit conclusion
Briefly already has the foundations the research-agent vision needs: deterministic scoring, source tiers (`configs/sources.yaml`, `app/verticals/scoring.py`), an evidence store (`vertical_source_events`, `healthcare_source_events`), a freshness-aware send-decision matrix, LLM shadow plans, and dense diagnostics. The gap is depth of interpretation and differentiation, not missing scaffolding. The work below extends existing modules rather than replacing them.

---

## Part 2 - Current Limitations Holding Briefly Back

Repo-grounded weaknesses:

1. News still feels generic. `theme_builder.py` ranks by relevance and tag priority but there is no per-event "why now / why it matters / second-order read-through" object; the Applied News Stack maps to context but the interpretation is thin.
2. Headlines can be ticker-misclassified. `app/processing/personal_relevance.py` and ticker matching rely on watchlist sets; there is no hardened entity-resolution guard against name-collision false positives (the "custard apple" / ticker-as-common-word problem). `NewsClassifierLabel.manual_ticker_mismatch_risk` exists as a label field but there is no runtime suppressor wired to it.
3. Source freshness is not always clear enough. `news_classifier.py` computes freshness states, but freshness provenance (first_seen vs published vs discovered) is not consistently surfaced in output copy.
4. Stale / fallback content can look too normal. The send-decision matrix exists, but degraded_context output can still resemble a normal brief unless the formatter aggressively differentiates it.
5. Session outputs are still too similar. `docs/SESSION_DESIGN.md` defines distinct roles, but section-selection overlap remains a known signal that selection rules need tightening (stated in the design doc itself).
6. Vertical intelligence is mostly shadow/off. Geopolitics and AI/tech `build_section` return `None`; `VERTICALS_INCLUDE_IN_BRIEFING` defaults false. The framework is built but not delivering user-visible value.
7. Geopolitics is headline-density-heavy. `geopolitics.py` is GDELT-only with a deterministic-score threshold; `NEWS_TREND_RADAR_PLAN.md` itself flags that density is not market confirmation, but the causal-channel confirmation is not yet implemented as a runtime gate for geopolitics.
8. AI/tech and healthcare need deeper source-specific pipelines. Healthcare is the only vertical with a real classifier/scorer/section_builder. AI/tech is SEC + arXiv + scaffolded GitHub.
9. Market/news interpretation is not yet deep enough. There is no module that answers valuation/earnings/margin/capex/rates transmission per event.
10. "Why this matters" is often too generic. `news_classifier._why_market_relevant` returns canned strings per story type, not event-specific reasoning.
11. Source quality tiers need to be stricter. Two parallel tier systems exist (`configs/sources.yaml` trust tiers and `app/verticals/scoring.py` source-tier weights); they are not unified into one authoritative registry.
12. Scraping / page monitoring is not designed as a safe controlled system. `VERTICAL_INTELLIGENCE_API_PLAN.md` notes "no scraping/page-monitoring collectors implemented".
13. No full research dossier mode. No way to ask for a deep multi-source note.
14. No ticker-specific research memo mode.
15. No source memory / per-company event timeline. `vertical_source_events` stores events but there is no entity-centric timeline view.
16. No entity graph / causal-channel aggregation. `VerticalSourceEventRecord.causal_channel` exists as a column but is not aggregated into a cross-event channel view.

---

## Part 3 - Target Vision: Briefly as a Research and Equity Intelligence Agent

Briefly keeps every current behaviour and adds seven output capabilities. All deterministic; LLM only drafts prose over already-selected evidence.

1. Session briefings: the existing scheduled emails, made meaningfully more differentiated per session (Part 8).
2. Equity research briefs: for a ticker, what happened, why it matters, valuation angle, earnings angle, risks, catalysts, peer read-through. Built from cached events plus optional fresh fetch.
3. Sector intelligence briefs: AI/tech, semiconductors, healthcare, pharma/biotech, energy, financials, consumer, industrials. Aggregates events and price/breadth by sector.
4. Geopolitical market-impact briefs: sanctions, oil, shipping, conflict, trade controls, elections, central-bank political pressure, with causal-channel confirmation against oil/VIX/gold/FX.
5. Event dossiers: one theme/event tracked over time with a timeline, source trail, affected assets and confidence.
6. Watchlist intelligence: what matters for the actual watchlist and portfolio, not generic market news.
7. Research agent mode: a CLI/UI action to request a deep research note from existing sources, cached events, and optionally fresh fetches.

These reuse: `vertical_source_events` as the evidence store, `news_classifier.py` for taxonomy, the deterministic scorers, the portfolio/watchlist relevance layer, and the existing diagnostics tables.

---

## Part 4 - Source Strategy

One authoritative source registry (`app/research/source_registry.py`) replaces the two parallel tier systems. Each source declares: tier, trust floor, key requirement, rate limit, robots/ToS posture, freshness expectation, capabilities.

Order of preference always: Tier 0 official API -> RSS -> Tier 1/2 wires/aggregators -> Tier 3 attention signals -> Tier 4 controlled scraping (last resort, allowlisted only).

### Tier 0 - official / primary (highest authority)
| Source | Key | Cost | Limits | In repo today | Realistic |
|---|---|---|---|---|---|
| SEC EDGAR | No key (User-Agent required) | Free | ~10 req/s | `sec_provider.py` | Yes, now |
| FRED | Key | Free | generous | `fred.py` | Yes, now |
| ECB | No | Free | low | `ecb.py` | Yes, now |
| Eurostat | No | Free | low | `eurostat.py` | Yes, now |
| ClinicalTrials.gov v2 | No | Free | generous | `healthcare/sources/clinicaltrials.py` | Yes, now |
| openFDA | Optional key | Free | higher with key | `healthcare/sources/fda.py` | Yes, now |
| EMA | No | Free | low | `healthcare/sources/ema.py` | Yes, now (page/feed dependent) |
| Company IR / press releases | No | Free | per-site | `healthcare/sources/company_ir.py` | Partial; expand via Tier 4 monitoring |
| Treasury / BLS / BEA | Key (BLS/BEA) | Free | daily caps | `sources.yaml` has BLS/BEA disabled | Phase 2/3 |
| Sanctions lists (OFAC SDN, EU, UK OFSI) | No | Free | per-site | Not present | Phase 6 (controlled monitoring) |
| Official exchange calendars | No | Free | n/a | `app/cadence/*_calendar.py` | Yes, now |

### Tier 1 - trusted wire / high-quality media
| Source | Key | Cost | Notes |
|---|---|---|---|
| Reuters / AP RSS | No | Free | RSS where licence permits; headline + link only |
| Finnhub news | Key | Free tier | `finnhub.py`; reliable-source filter needed |
| Bloomberg / FT / WSJ | Licensed | Paid | Only via licensed API/RSS; never scrape paywalled bodies |
| NewsAPI (when surfacing Tier 1 sources) | Key | Free 100/day | `newsapi.py`; treat source domain, not NewsAPI, as the trust anchor |

### Tier 2 - broad market / news aggregators
| Source | Key | Cost | Notes |
|---|---|---|---|
| GDELT | No | Free | `gdelt.py`; density/coverage only, never market-confirmation |
| NewsAPI broad results | Key | Free tier | breadth, low trust floor |
| Finnhub general news | Key | Free tier | breadth |
| Alpha Vantage / FMP / Mediastack / Marketaux | Key | Free tiers | already budgeted in `global_news_hub.py` |
| RSS feeds | No | Free | configurable allowlist |

### Tier 3 - community / attention / traction signals
| Source | Key | Cost | Notes |
|---|---|---|---|
| arXiv | No | Free | `verticals/sources/ai_tech.py` |
| GitHub trends/releases | Optional token | Free | scaffolded; higher limits with token |
| Hacker News (Algolia API) | No | Free | planned |
| Reddit | Key | Free | only if explicitly configured; off by default |
| Google Trends | Unofficial | Free | only if accessible; treat as low trust |
| Hugging Face model/card trends | Optional | Free | planned only |

### Tier 4 - controlled scraping / page monitoring (last resort)
Permitted only when robots.txt and ToS allow, at low frequency, from an explicit allowlist, cached, metadata-only, never bypassing paywalls, never redistributing full text, with diagnostics recorded, and never outranking Tier 0/1. See Part 10.

---

## Part 5 - Proposed Architecture

New package `app/research/` sits alongside existing modules and reuses them. Deterministic scoring stays authoritative; LLM modules only draft prose.

```
app/research/
  source_registry.py    # one authoritative source/tier/trust/limits registry; replaces dual tiers
  query_planner.py      # turn a request (ticker/sector/geo/event) into a source-fetch + cache-read plan
  evidence_store.py     # read/write façade over VerticalSourceEventRecord + new research tables
  event_graph.py        # cluster events into themes/storylines; entity + causal-channel aggregation
  source_quality.py     # unified trust/freshness/corroboration scoring helpers
  catalyst_tracker.py   # earnings/FDA/macro/event proximity per ticker and sector
  company_context.py    # per-ticker entity context: peers, sector, exposures, recent events
  sector_context.py     # sector breadth/rotation + event aggregation
  geo_impact.py         # geopolitics causal-channel confirmation (oil/VIX/gold/FX)
  research_memo.py       # deterministic evidence object -> structured memo (LLM prose optional)
  llm_shadow.py         # bounded LLM: summarise/extract/suggest; logs det-vs-LLM disagreement
  safe_scraping.py      # controlled, allowlisted, robots-aware page monitoring
```

Connections to existing code:
- `app/verticals/`: `evidence_store.py` wraps `verticals/source_store.py` and `VerticalSourceEventRecord`; `event_graph.py` consumes `verticals/events.py`; `source_quality.py` subsumes `verticals/scoring.py` weights and `configs/sources.yaml` trust tiers via `source_registry.py`.
- `app/data_sources/`: `query_planner.py` calls existing services (`market_data.py`, `news_data.py`, `macro_data.py`) and provider adapters; no provider is bypassed.
- `app/briefing/`: `research_memo.py` reuses `news_classifier.py`, `theme_builder.py`, `valuation_lens.py`, `portfolio_impact.py`, `trust_contract.py`. Session generators call new context modules for differentiation (Part 8).
- `app/portfolio/`: `company_context.py` and `catalyst_tracker.py` read holdings/watchlists via `portfolio/service.py`.
- `app/web/`: new read-only research pages (Part 12) added to `app/web/app.py`.
- CLI: new `research` command group in `app/cli.py` (Part 12).
- DB: new models in `app/db/models.py` (Part 13).
- Diagnostics: research runs log to `provider_health` and a new `research_run_diagnostics` table.

Authority boundary (unchanged invariant): deterministic scoring decides trust, relevance, alert eligibility and market regime. LLM may summarise, extract entities, suggest causal channels, draft prose. LLM never decides trust, eligibility, ranking, or regime.

---

## Part 6 - News Quality and Ranking Algorithm (deterministic)

Extends `app/briefing/news_classifier.py` and `app/processing/relevance_scoring.py` into one composite ranker in `app/research/source_quality.py`. All inputs are deterministic and auditable.

Scored factors (each normalised to 0-1, then weighted):
- source_tier (from `source_registry.py`)
- freshness (age vs published, with first_seen provenance)
- novelty vs previous sessions (reuse `novelty_score` / sent-history)
- source_corroboration (distinct domains in the cluster)
- direct_ticker_relevance (validated entity match, Part 6 suppression)
- portfolio/watchlist_relevance (`personal_relevance.py`)
- sector_relevance, macro_relevance, geopolitics_relevance
- earnings_proximity (`catalyst_tracker.py`)
- price_confirmation (move vs expected direction)
- abnormal_move_confirmation (move vs typical range)
- volume/attention_confirmation (if available)
- causal_clarity (mapped transmission channel present)
- confidence (`factual_confidence_score`)
- duplicate_cluster_size
- content_class (official > reported > analysis > opinion > rumour > low_quality)

### Pseudocode

```
def rank_news(events, profile, session_ctx, market_ctx):
    scored = []
    for e in events:
        cls = classify_news_event(e)            # existing taxonomy/freshness
        if should_suppress(e, cls, profile):    # Part 6 suppression rules
            record_suppressed(e, reason); continue

        tier   = registry.tier_weight(e.source, e.domain)
        fresh  = freshness_score(cls)            # new>updated>repeated>stale
        novel  = novelty_vs_sent_history(e, session_ctx)
        corrob = distinct_domain_count(e.cluster_id) capped
        tick   = validated_ticker_relevance(e, profile)   # entity-resolved
        pers   = personal_relevance(e, profile)
        sect   = sector_relevance(e, profile)
        macro  = macro_relevance(cls)
        geo    = geo_relevance(cls)
        earn   = earnings_proximity(e, catalyst_tracker)
        price  = price_confirmation(e, market_ctx)        # 0 if unmoved
        absmv  = abnormal_move_confirmation(e, market_ctx)
        vol    = volume_attention_confirmation(e, market_ctx)
        causal = causal_clarity(cls)
        conf   = e.factual_confidence_score
        clsz   = cluster_size_score(e)
        ctype  = content_class_weight(cls.story_type)     # official..rumour

        score = weighted_sum({
            tier:0.16, fresh:0.10, novel:0.08, corrob:0.06,
            tick:0.10, pers:0.10, sect:0.05, macro:0.04, geo:0.04,
            earn:0.05, price:0.06, absmv:0.03, vol:0.02,
            causal:0.05, conf:0.04, clsz:0.02, ctype:weight
        })
        # Hard caps: headline density alone cannot reach "market-confirmed".
        if causal == 0 and price == 0:
            score = min(score, MARKET_UNCONFIRMED_CEILING)  # e.g. 0.6
        scored.append((score, e, explanation))
    return sorted(scored, key=score, reverse=True)
```

### Suppression rules
Wire to `news_classifier.should_suppress_low_signal` and extend:
- generic price-target spam: `analyst_action` with only "price target" + no guidance/earnings link -> suppress.
- repeated old headlines: freshness in {stale, old_context} or `already_sent` cluster -> suppress from fresh ranking.
- low-quality SEO: `_LOW_SIGNAL_PATTERNS` (already present) -> suppress.
- ticker-name false positives ("custard apple" type): require validated entity resolution (token boundary + company-name corroboration via `ticker_metadata.py`); if the only match is a common-word ticker with no company-name or sector corroboration, drop the ticker and re-evaluate relevance. Set `manual_ticker_mismatch_risk`-style flag in diagnostics.
- generic "stock moved today" articles: `generic_market_wrap` with no catalyst -> suppress.
- irrelevant broad-market clickbait: low personal + low market + no catalyst -> suppress.
- uncorroborated rumours: content_class == rumour and corroboration < 2 distinct trusted domains -> context only, never alert-eligible.

---

## Part 7 - Research Interpretation Layer

`app/research/research_memo.py` builds a deterministic `EvidenceObject` per surfaced event/cluster before any prose is drafted. Reuses `valuation_lens.py`, `portfolio_impact.py`, `catalyst_tracker.py`, `geo_impact.py`.

For each event the object answers (deterministic fields, with explicit "unknown" when unsupported):
- what_happened: from title/summary + story_type.
- why_now: freshness + trigger (filing date, earnings date, macro release).
- why_it_matters: mapped transmission channel + affected assets.
- affected_assets: tickers/sectors/assets (entity-resolved).
- price_confirmed: bool + magnitude from `market_ctx`.
- already_priced_in: heuristic from move vs surprise vs prior drift.
- earnings_relevant: from `catalyst_tracker`.
- valuation_relevant: from `valuation_lens`.
- driver_type: one of margin / revenue / capex / regulatory / geopolitical / rates.
- second_order_read_through: peers/suppliers/customers via `company_context`.
- confirm_or_invalidate: what data point would confirm/refute (e.g. next print, guidance, price level).
- watch_next: next catalyst date or level.

LLM (shadow) may draft the prose wording only after the EvidenceObject is built, and only over its fields. It cannot add facts not in the object.

---

## Part 8 - Session-Specific Design

Each session calls new context modules so outputs diverge. Selection rules tighten so a section that appears in one session is suppressed in another per `docs/SESSION_DESIGN.md`. For each session below: what to show, what to suppress, data needed, what makes it worth reading.

### Morning Briefing
- Show: overnight map (Asia close, prior US close, Europe pre-open), global risk setup, top 3 fresh catalysts (ranked by Part 6), earnings/event calendar, portfolio/watchlist exposure map, what to watch into Europe/US.
- Suppress: first-hour verdict, live US open tape.
- Data: overnight quotes (stale-labelled if pre-open), FRED rates/USD, macro calendar, fresh news, holdings.
- Worth reading: the day plan and the three things that actually matter today.

### Europe Midday
- Show: Europe live breadth, Europe sector rotation, US futures/pre-market if available, rates/FX/crude handoff, overnight news update, which morning thesis is confirming/fading (`session_delta.py`).
- Suppress: Asia context (closed), full Applied News Stack.
- Data: live Europe quotes, EUR/USD, EUR/GBP, futures, morning snapshot for delta.
- Worth reading: is the morning setup holding in Europe.

### US Pre-Open
- Show: pre-market watchlist movers, key US-open catalysts, earnings AMC/BMO, rates/FX/oil setup, market internals, specific trigger board.
- Suppress: live US intraday data (not open).
- Data: pre-market quotes, earnings calendar, Europe-to-US handoff snapshot.
- Worth reading: the opening game plan with explicit triggers.

### US Intraday Risk Check
- Show: open-to-now move, breadth deterioration/improvement, leaders/laggards, factor rotation, macro shock check, news since pre-open, pre-open thesis confirmed/faded/inverted.
- Suppress: overnight setup, earnings calendar. Do not send if live data unavailable and no fresh material news (matrix already enforces this).
- Data: live intraday quotes, pre-open snapshot for delta.
- Worth reading: did the open confirm or reject the setup.

### Into Close
- Show: what changed since intraday, closing pressure, breadth into close, volatility/rates confirmation, portfolio P&L attribution, whether tomorrow's setup is changing.
- Suppress: morning setup, Asia context.
- Data: late-session quotes, intraday snapshot for delta, portfolio returns.
- Worth reading: late-session risk and positioning.

### Closing Wrap / Next-Day Setup
- Show: day recap, close vs open, high/low/range, sector winners/losers, watchlist session tape, top confirmed drivers vs false signals, tomorrow trigger board, event calendar, news that matters tomorrow.
- Suppress: pre-market context.
- Data: full-day tape (`session_tape.py`, `day_replay.py`), confirmed-driver analysis, next-day calendar.
- Worth reading: the verdict and tomorrow's prep.

Implementation note: differentiation is enforced by a per-session section allowlist/denylist plus the delta engine, not by generating the same payload and trimming it.

---

## Part 9 - Degraded Data and No-Send Policy

The matrix in `app/briefing/send_decision.py` already implements most of this. Required behaviour and reinforcements:

- Live market data fails: do not send a normal-looking stale brief.
- Use stale snapshots only as labelled context (degraded_context mode), with a visible banner: "LIVE DATA DEGRADED: provider fetch failed at HH:MM; using latest valid snapshot from HH:MM (<session>). Treat levels as stale."
- If fresh material news exists: send a news-led update (news_only when market unavailable; degraded_context when stale snapshot present). `market_setup_analysis` is blanked in news_only.
- If no fresh material news exists: suppress the scheduled send; log `skipped_degraded_no_fresh_data` to `SessionSendState` with `channel="suppressed"`.
- Daily summary must say "suppressed: no fresh data" (distinct from failed delivery).
- Delivery log must distinguish suppressed from failed (`success=False` + real error vs `channel="suppressed"`).

Reinforcement to add (Phase 4): make degraded_context formatting visually distinct (banner, stripped charts, reduced sections) so it cannot be mistaken for a normal brief. Add a test asserting degraded output differs structurally from normal.

Examples:
- Market unavailable + fresh Fed decision -> news_only, banner, no stale price section.
- Stale snapshot + fresh earnings -> degraded_context, labelled stale levels + fresh earnings interpretation.
- Stale snapshot + no fresh news -> suppressed; daily-summary row: "Into Close: suppressed: no fresh data".

---

## Part 10 - Controlled Source Monitoring Plan (safe "scraping")

Language: "controlled source monitoring", not scraping. Implemented in `app/research/safe_scraping.py` and a human-readable `configs/monitored_sources.yaml`.

Design:
- source allowlist: only domains/paths explicitly listed in `configs/monitored_sources.yaml`.
- robots.txt check: fetch and honour robots before any request; cache the verdict; skip disallowed paths.
- terms-of-service warning: each allowlist entry must record a `tos_reviewed: true` flag and a note; refuse to fetch entries without it.
- rate limits: per-source minimum interval (default >= 6 hours); global concurrency 1; jittered.
- fetch cache: store response metadata + extracted fields only, keyed by URL + fetch date.
- page fingerprinting: hash the extracted content region; only emit an event when the fingerprint changes.
- metadata-only storage: title, link, published/updated date, short extract; never the full page body.
- HTML extraction rules: per-source CSS/XPath selectors in config; no blind full-text capture.
- never bypass paywalls; never store huge raw pages; never overload sites; never treat monitored pages as higher authority than Tier 0/1.
- source health diagnostics: log to `provider_health` and `monitored_source_health` (status: ok/disallowed_by_robots/rate_limited/error/changed/unchanged).

Candidate sources (official/public, robots-permitting): company IR pages, central-bank speeches pages, regulator press-release pages, sanctions list pages (OFAC SDN, EU, UK OFSI), clinical/regulatory update pages, official government pages, select RSS feeds.

Implementable now: RSS feeds and a tiny allowlist of clearly robots-permitted official press-release/IR pages with stable HTML. Wait: anything paywalled, robots-ambiguous, JS-heavy, or high-frequency. Sanctions-list parsing waits to Phase 6 with explicit per-source review.

---

## Part 11 - LLM Role

Codifies `docs/LLM_VERTICAL_SHADOW_PLAN.md`. Implemented in `app/research/llm_shadow.py`, logging to `LLMUsageLog`.

Allowed:
- summarise already-selected evidence,
- extract entities from text,
- suggest causal channels,
- draft memo prose over an EvidenceObject,
- compare competing explanations,
- generate questions to investigate.

Forbidden:
- decide source trust,
- decide alert eligibility,
- invent facts,
- rank unsupported events,
- override deterministic scoring,
- produce uncited claims,
- substitute for unavailable data.

LLM shadow mode logs, per item: deterministic classification, LLM-suggested classification, agreement/disagreement, and the final deterministic decision (reusing `NewsClassifierShadowRun` and adding a research-memo shadow table). Disagreements feed the labelling/review workflow (`news-review`, `news-label-set`).

Model default: latest Claude (e.g. `claude-opus-4-8` / `claude-sonnet-4-6`) via the existing LLM client; usage and cost tracked in `app/llm/usage_tracker.py`.

---

## Part 12 - CLI / UI Roadmap

CLI (new `research` group in `app/cli.py`):
- `python -m app.cli research ticker NVDA --deep`
- `python -m app.cli research sector ai-tech --since 7d`
- `python -m app.cli research geo --topic sanctions --since 48h`
- `python -m app.cli research event "US Iran sanctions" --timeline`
- `python -m app.cli verticals-status --verbose` (extend existing)
- `python -m app.cli source-health` (new: unified provider + monitored-source health)
- `python -m app.cli research-digest --watchlist`

UI (read-only pages in `app/web/app.py` + templates):
- Research Dashboard (`/ui/research`)
- Ticker Dossier (`/ui/research/ticker/{symbol}`)
- Event Timeline (`/ui/research/event/{event_key}`)
- Source Health (`/ui/research/sources`)
- Vertical Intelligence (extend `/ui/briefing/verticals`)
- Watchlist Catalyst Map (`/ui/research/watchlist`)

All research outputs carry citations and an evidence/confidence footer.

---

## Part 13 - Database and Storage Plan

Reuse `VerticalSourceEventRecord` (source events, dedupe hash, causal_channel, entities) and `healthcare_source_events`. Add bounded models to `app/db/models.py`:

- `SourceEvent` (or keep `VerticalSourceEventRecord` generalised): one row per source event, metadata-only, `payload_hash` unique.
- `EventCluster`: cluster_id, theme/storyline key, member event ids (json), first/last seen, source_count, deterministic_score, confidence.
- `CompanyEntity`: ticker, canonical name, aliases (json), sector, peers (json) - small reference table seeded from `ticker_metadata.py`.
- `MonitoredSourceHealth`: source_key, last_fetch, status, robots_verdict, fingerprint, change_count.
- `ResearchMemo`: id, scope_type (ticker/sector/geo/event), scope_key, generated_at, evidence_object_json (bounded), prose_text, citations_json, confidence, llm_used bool.
- `EventTimelineEntry`: scope_key, event_key, ts, label, source_url, confidence (per-company/event timeline).
- `DedupeHash`: content/story fingerprints (or reuse existing `content_hash` + fingerprint columns).
- `EvidenceSnippet`: short extract (bounded length, e.g. <= 500 chars), source_url, event_key. Never full articles.
- `SourceCitation`: memo_id, source_key, url, tier, retrieved_at.
- `ResearchRunDiagnostics`: run_id, scope, sources_queried_json, counts, latency, errors.

Storage rules: bounded text columns; never store full copyrighted article bodies; prune research tables on a retention schedule (mirror `SessionArchiveSnapshot` pruning); metadata + short extract + link only.

---

## Part 14 - Implementation Roadmap

### Phase 1 - Strategy and audit only (this document)
- Purpose: produce this MD. Files: `docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md`. Tests: none (doc only). Risk: none. Output: agreed plan.

### Phase 2 - Research event layer
- Purpose: generic evidence/event store, unified source registry, scoring helpers.
- Files: `app/research/source_registry.py`, `evidence_store.py`, `source_quality.py`; new DB models (`EventCluster`, `ResearchRunDiagnostics`); `configs/sources.yaml` consolidation.
- Tests: registry tier resolution, evidence-store upsert/no-full-text, source_quality scoring determinism.
- Risk: duplicating logic in `verticals/scoring.py`; mitigate by having `scoring.py` delegate to `source_quality.py`.
- Output: an authoritative source registry + queryable evidence store.

### Phase 3 - Better news ranking
- Purpose: trust tiers, dedupe, entity validation, suppression rules.
- Files: `app/research/source_quality.py`, `app/processing/personal_relevance.py`, `app/universe/ticker_metadata.py`, `app/briefing/news_classifier.py`.
- Tests: ticker false-positive suppression, official-outranks-broad-media, rumour corroboration gate, stale suppression.
- Risk: regressions in current news selection; mitigate with golden-output tests on existing fixtures.
- Output: higher-signal, less generic news.

### Phase 4 - Session redesign
- Purpose: make the six emails meaningfully different.
- Files: session generators, `session_delta.py`, `session_routing.py`, per-session section allow/deny config, `email_formatter.py`, `formatter.py`.
- Tests: session-outputs-differ-by-role, degraded output structurally distinct.
- Risk: idempotency/cadence regressions; keep delivery paths untouched, change only content selection.
- Output: distinct, worth-reading sessions.

### Phase 5 - Research CLI
- Purpose: ticker/sector/geo/event memos and watchlist digest.
- Files: `app/research/research_memo.py`, `query_planner.py`, `company_context.py`, `sector_context.py`, `geo_impact.py`, `catalyst_tracker.py`, `event_graph.py`; `app/cli.py` `research` group; `ResearchMemo`/`EventTimelineEntry` models.
- Tests: memo has citations + evidence, deterministic memo fields stable, geo causal-confirmation gate.
- Risk: scope creep; keep memos read-only over cached evidence + optional single fetch.
- Output: on-demand research notes.

### Phase 6 - Controlled source monitoring
- Purpose: allowlisted page/RSS monitoring with diagnostics.
- Files: `app/research/safe_scraping.py`, `configs/monitored_sources.yaml`, `MonitoredSourceHealth` model.
- Tests: robots respected, rate-limit enforced, allowlist respected, metadata-only storage, no full-text.
- Risk: legal/ToS and site load; mitigate with strict allowlist, `tos_reviewed` gate, long intervals.
- Output: safe official-page change detection.

### Phase 7 - LLM shadow summarisation
- Purpose: bounded summaries/prose over selected evidence.
- Files: `app/research/llm_shadow.py`, `app/briefing/llm_news_classifier.py`, `app/llm/usage_tracker.py`; shadow log tables.
- Tests: LLM cannot override deterministic score, all prose cites evidence, disagreement logged.
- Risk: hallucination/authority creep; mitigate with EvidenceObject-only inputs and shadow-by-default.
- Output: better wording, no authority change.

### Phase 8 - UI dashboards
- Purpose: research dashboard, source health, event timelines, catalyst map.
- Files: `app/web/app.py`, `app/web/templates/*`.
- Tests: route contract tests, citations rendered, read-only.
- Risk: UI sprawl (called out in `AUDIT_CURRENT_STATE.md`); mitigate with progressive disclosure.
- Output: visible research surfaces.

---

## Part 15 - Tests and Quality Gates (for future phases)

- source quality scoring: deterministic, official outranks broad media.
- source health fail-soft: provider/monitor failure never crashes briefing.
- no full-text storage: assert stored extracts <= bound; no article-body column populated.
- no false ticker matches: "custard apple" style common-word tickers dropped without company-name corroboration.
- no normal briefing from stale-only data: degraded path never emits normal mode.
- news-only degraded update: market unavailable + fresh material news -> news_only, no stale price analysis.
- official source outranks broad media in ranking.
- LLM cannot override deterministic score: shadow output never changes final ranking/eligibility.
- session outputs differ by role: section sets differ across the six sessions.
- research memo has citations/evidence: every memo references sources.
- scraping allowlist respected: only configured domains fetched.
- robots / rate-limit checks: disallowed paths skipped; interval enforced.
- duplicate suppression: fingerprint dedupe keeps best, suppresses rest.
- stale news suppression: stale/old_context excluded from fresh ranking and breaking.
- API config docs consistency: `configs/sources.yaml`, `.env.example`, `docs/API_SETUP.md` agree on keys/flags.

---

## Part 16 - Final Output Requirements

This file (`docs/BRIEFLY_RESEARCH_AGENT_STRATEGY.md`) is the deliverable. It is detailed enough for another agent to implement phase by phase. No broad runtime changes were made.

Cross-references: builds on `docs/SESSION_DESIGN.md`, `docs/NEWS_TREND_RADAR_PLAN.md`, `docs/LLM_VERTICAL_SHADOW_PLAN.md`, `docs/VERTICAL_INTELLIGENCE_API_PLAN.md`, `docs/AUDIT_CURRENT_STATE.md`, and `docs/FUTURE_ENHANCEMENTS.md`. It does not contradict them; it unifies the source tiers, adds the research-agent output modes, and sequences delivery.
