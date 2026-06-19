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

---

## Trusted Source Hierarchy and Integration Plan

### Principle: API and RSS first, scraping last

All news ingestion follows a strict tier order. Scraping is permitted only for sources that explicitly allow it (via robots.txt or explicit licence) and only after API and RSS coverage has been exhausted for that source.

### Source Tiers

| Tier | Type | Examples | Authority in output |
|---|---|---|---|
| 1 | Official filings, regulators, central banks | SEC EDGAR, ECB, FRED, BIS, FDA | Highest: directional authority |
| 2 | High-trust wires and major financial data providers | Reuters RSS, AP, Bloomberg (licensed), Finnhub news | High: primary confirmation |
| 3 | Secondary aggregators, sector journals | MarketWatch RSS, Seeking Alpha (licensed tier), STAT News | Supporting: adds context |
| 4 | Low-trust commentary, opinion, social signals | Twitter/X, Reddit, aggregated blogs | Context only: never authority |

### Causal Channel Mapping

Before publishing a news-driven signal, the system checks for causal transmission evidence:

- Oil spike news: check WTI/Brent price move confirms direction.
- Rate/inflation news: check US 10Y change confirms direction.
- Geopolitical news: check VIX, gold, and haven FX moves confirm stress signal.
- Earnings news: check company price move or futures confirm reaction.

If causal channel confirmation is absent, the signal is labelled “headline-only, unconfirmed by market”. It may appear in output but cannot drive alert eligibility.

### Guardrail: Headline Density is Not Market Confirmation

Headline density (many sources covering the same topic) raises the cluster_growth and novelty scores but does NOT constitute “market-confirmed” risk. A dense news cluster with no asset-price confirmation is labelled “headline risk elevated; market confirmation incomplete”.

This prevents a surge of geopolitical headlines from generating a false “market stress” signal when equities, VIX, and rates are unmoved.

### Scraping Policy

Scraping is permitted only:
- For sources where robots.txt allows the relevant path.
- For sources with explicit public data licences.
- As a last-resort fallback after API and RSS fail for the same source.
- Never for full article bodies when the source requires a subscription.
- Never before a stable API or RSS feed has been implemented for that source.
