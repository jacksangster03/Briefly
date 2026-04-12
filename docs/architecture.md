# Architecture

`Briefly` is organized as a layered portfolio-intelligence platform with three product modules:

1. Market Briefing
   Morning, intraday, and breaking intelligence delivery.
2. Portfolio Control
   Holdings snapshots and profile-level personalization controls.
3. Trading Lab (planned)
   Future execution/sentiment workflows built on the same deterministic core.

Shared platform pipeline:

1. Providers
   Free-first adapters for Finnhub, FRED, SEC EDGAR, NewsAPI, and yfinance fallback.
2. Data services
   Service-level aggregation for market data, news, macro data, and filings.
3. Processing
   Deduplication, clustering, credibility scoring, personal relevance, sent-history classification, and final ranking.
4. Briefing generation
   Morning, intraday, and breaking generators assemble product-specific outputs from the processed event stream.
5. Formatting and delivery
   Telegram-first formatting, with email available as a backup channel.
6. Persistence
   SQLite stores sent-message history, processed sent events, snapshots, and provider health.

The core design goal is to keep market-intelligence logic generic while user-specific behavior lives in YAML configuration and ranking adjustments.
