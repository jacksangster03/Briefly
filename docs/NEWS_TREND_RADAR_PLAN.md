# News Trend Radar Plan (Deterministic-First)

## Scope
- Build a future **News Trend Radar** that improves trend visibility without changing deterministic authority.
- Keep alert eligibility, ranking authority, and suppression rules deterministic and auditable.

## Source Strategy
- Prefer RSS/API-first ingestion from allowed/public sources.
- Use scraping only for explicitly permitted, robots-compatible public pages.
- Do not store full copyrighted article bodies when not licensed.
- No scraping-first rollouts: API/RSS and official feeds are always the first integration tier.

## Trust Tiers
- Tier 1: Official filings/regulators/central banks.
- Tier 2: High-trust wires and major financial news providers.
- Tier 3: Secondary aggregators/context sources.
- Tier 4: Low-trust/commentary-only sources (context, never authority).

## Traction Signals (Deterministic)
- source_count
- cluster_growth
- novelty
- cross_source_confirmation
- market_price_confirmation
- watchlist_or_portfolio_relevance
- **Important guardrail**: headline density alone is insufficient for “market-confirmed” risk.

## LLM Role (Shadow/Assist Only)
- Summarisation of already-selected clusters.
- Entity extraction and causal-channel extraction.
- Dedupe assistance proposals.
- No authority over scoring, ranking, suppression, or delivery eligibility.

## Deterministic Role (Authoritative)
- Final scoring
- Final ranking
- Alert eligibility
- Source trust weighting
- Portfolio/watchlist relevance weighting

## Future Training Dataset
- Store labelled accepted/rejected items with deterministic reason codes.
- Keep outcome features for later offline evaluation.
- No live trading claims, no autonomous execution behaviour.

## Phase Notes
- Phase 1: design + telemetry shape only.
- Phase 2: shadow trend extraction and UI diagnostics in News Intelligence.
- Phase 3: deterministic trend score integration after coverage/quality checks.

## Geopolitics Trend Radar Activation Criteria (planned)
- Activate geopolitics trend-radar emphasis when:
  - density is elevated **and**
  - cross-source confirmation improves **or**
  - asset transmission confirms (oil/VIX/gold/FX) **or**
  - portfolio/watchlist exposure relevance is high.
- If confirmation is missing, keep output in “headline risk elevated, market confirmation incomplete” state.
