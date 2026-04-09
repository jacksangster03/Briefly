# Provider Strategy

The repository is intentionally free-first.

## Primary providers

- Finnhub: quotes, market news, company news, earnings calendar
- FRED: macro series and treasury context
- SEC EDGAR: official filings
- NewsAPI: broad headline enrichment

## Fallbacks

- yfinance is used only as a fallback for quotes

## Design principles

- provider adapters isolate upstream dependencies
- degraded providers should not crash the full briefing flow
- observability records latency and failures per provider call
- premium providers remain optional upgrades rather than core dependencies
