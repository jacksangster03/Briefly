# Future Enhancements

This document lists potential future upgrades for Briefly. These are not required for the current stable workflow and should be implemented only after the core briefing, delivery, portfolio, macro and diagnostics flows remain stable.

## Guiding Principles

- Keep deterministic logic authoritative.
- Keep LLM/ML features opt-in, shadow-only or clearly non-authoritative unless explicitly promoted.
- Avoid heavy dependencies in the base install.
- Prefer modular providers and pure analytics functions.
- Preserve delivery/idempotency safety.
- Add tests before enabling new behaviour in live briefings.

## Candidate Enhancements

### 1. FinBERT Sentiment Scoring

Add optional financial-news sentiment scoring using a FinBERT model.

Potential use:
- Score headline/body sentiment.
- Add a small sentiment adjustment to relevance scoring.
- Surface sentiment as context, not final authority.

Constraints:
- Disabled by default.
- Optional ML dependency only.
- Must not slow normal briefing generation unless enabled.
- Deterministic scoring remains primary.

### 2. SEC Form 4 Insider Trade Expansion

Improve insider trading detection and presentation.

Potential use:
- Better Form 4 parsing.
- Insider purchase/sale classification.
- Watchlist and portfolio relevance.
- Distinguish routine compensation sales from more meaningful insider activity.

Constraints:
- Avoid over-weighting isolated insider trades.
- Require clear issuer/ticker mapping.
- Add conservative relevance rules.

### 3. GARCH Volatility Metrics

Add optional volatility forecasting and regime context using GARCH-style models.

Potential use:
- Risk dashboard context.
- Volatility regime diagnostics.
- Portfolio risk monitoring.

Constraints:
- Disabled by default.
- Analytics-only.
- Do not present as a forecast with certainty.
- Keep deterministic caveats clear.

### 4. ECB and Eurostat Macro Expansion

Broaden Eurozone macro coverage beyond the existing ECB/FRED setup.

Potential use:
- Eurozone unemployment.
- HICP and core inflation.
- ECB rates and reference FX.
- Spain/Eurozone country-lens improvements.

Constraints:
- Prefer official public APIs.
- Maintain explicit freshness labels.
- Missing data must degrade gracefully.

### 5. Quantstats Tear Sheets

Add optional portfolio tear sheets for deeper performance analytics.

Potential use:
- Return metrics.
- Drawdown analysis.
- Rolling volatility.
- Sharpe/Sortino-style diagnostics.

Constraints:
- Web/report-only at first.
- Not part of normal Telegram/email briefings by default.
- Optional analytics dependency.

### 6. Portfolio Optimisation

Add optional optimisation methods such as HRP or minimum-CVaR.

Potential use:
- Rebalancing workbench.
- Scenario comparison.
- Allocation diagnostics.

Constraints:
- Advisory/diagnostic only.
- No automated trading.
- Must show assumptions and constraints clearly.
- Keep current policy-based allocation as the stable baseline.

### 7. Earnings Estimate Enrichment

Improve earnings context when provider data is incomplete.

Potential use:
- Consensus estimate where available.
- Surprise percentage.
- Guidance context.
- Portfolio/watchlist relevance.

Constraints:
- Do not hallucinate estimates.
- Missing estimates should be explicit.
- Prefer reliable provider data.

### 8. Alpaca Market Data Provider

Add Alpaca as an optional market data provider.

Potential use:
- More reliable US quote/history data.
- Possible improvement over fallback chains for some instruments.

Constraints:
- Optional provider.
- No trading integration by default.
- Must preserve existing provider fallback behaviour.
- Must include clear provider health diagnostics.

## Possible Implementation Order

1. Alpaca market data provider
2. SEC Form 4 improvements
3. ECB/Eurostat macro expansion
4. Earnings estimate enrichment
5. FinBERT sentiment scoring
6. GARCH volatility metrics
7. Quantstats tear sheets
8. Portfolio optimisation

## Future News Trend Radar

A separate future module could detect high-traction geopolitical and macro stories.

Potential design:
- Use APIs/RSS before scraping.
- Cluster repeated stories.
- Score source velocity, source credibility, novelty, market relevance and price confirmation.
- Map stories to transmission channels such as energy, shipping, sanctions, tariffs, FX, inflation, rates, defence and semiconductors.
- Keep LLM use summarisation-only and non-authoritative.

This should be implemented separately from freshness/briefing-quality fixes.
