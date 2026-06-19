# Session Design

Each of the six daily sessions has a distinct job. Shared generic structure between sessions is a signal that the section selection rules need tightening.

## Session Roles

### Morning Briefing
**Job**: Overnight setup and day plan.
**Sections**: Opening diagnosis, rates and macro tape, Asia / US prior close / Europe setup, today's trigger board, event calendar, Applied News Stack, portfolio exposure map, watchlist prior-close context.
**Not**: First-hour verdict, US open tape.

### Europe Midday
**Job**: Europe confirmation or rejection of morning setup.
**Sections**: Europe setup verdict, Europe session tape, Europe breadth and leaders, EUR/USD and EUR/GBP, rates/oil update, what changed since morning, US pre-open watch items.
**Not**: Asia context (closed), full Applied News Stack.

### US Pre-Open
**Job**: Europe-to-US handoff and US open plan.
**Sections**: US open setup verdict, Europe-to-US handoff, pre-market movers, rates/FX/commodities pressure map, opening confirmation triggers, earnings/event risk, portfolio first-90-min risk.
**Not**: Live US intraday data (market not open yet).

### US Intraday Risk Check
**Job**: First-hour confirmation check.
**Sections**: First-hour verdict, pre-open thesis confirmed/faded/inverted, US open tape from open, sector breadth live, watchlist movers from open, cross-asset confirmation, intraday risk triggers.
**Fallback**: If live data unavailable, use stale snapshot from US Pre-Open with "LIVE DATA DEGRADED" banner.
**Not**: Overnight setup, earnings calendar.

### Into Close
**Job**: Late-session drift and Europe-close/US-close setup.
**Sections**: Into-close verdict, Europe close recap, US session tape so far, sector rotation, watchlist from open, portfolio attribution, closing watch triggers.
**Not**: Morning setup, Asia context.

### Closing Wrap
**Job**: Final day verdict and tomorrow setup.
**Sections**: Day verdict, US cash session recap, Europe close recap, confirmed drivers vs false signals, portfolio attribution, watchlist session tape, overnight risk and tomorrow setup, earnings/news after close.
**Not**: Pre-market context (session is done).

## Fallback Hierarchy for Market Data

When live providers fail, values are sourced in this order:
1. **live**: current provider quote (Finnhub, Alpaca, or yfinance)
2. **near_real_time**: alternate provider quote, already in the provider chain
3. **delayed**: quote older than 15 min but within 60 min
4. **stale_snapshot**: prior session's archived market_summary_json
5. **prior_close**: yfinance history fallback (prior session close)
6. **unavailable**: no data available from any source

All stale_snapshot values are labelled explicitly. Stale and live values are never silently mixed without labels.

## Data Outage Email Rules

**Provider outage with snapshot available:**
Show: "LIVE DATA DEGRADED: provider fetch failed at HH:MM; using latest valid snapshot from HH:MM (<session> session). Treat levels as stale until provider recovery."
Then show last-known SPX/VIX/US10Y/WTI/watchlist with stale label.

**Provider outage with no snapshot:**
Show compact outage block. Suppress regime shift and portfolio transmission sections.

## Send-Decision Matrix (Freshness-Aware Gating)

| Market Data | News Status | Scheduled | Mode | Action |
|---|---|---|---|---|
| live / partial | fresh | yes | normal | send |
| live / partial | stale/empty | yes | market_only | send |
| stale_snapshot | fresh + material | yes | degraded_context | send with stale caveat |
| unavailable | fresh + material | yes | news_only | send news only |
| stale_snapshot / unavailable | no fresh news | yes | suppressed | do not send; log skipped_degraded_no_fresh_data |
| any | any | dry-run | degraded_context | always render, never suppress |

Stale snapshots provide context only. They are never the primary basis for a normal briefing send.

`briefing_mode` field values: `normal`, `market_only`, `news_only`, `degraded_context`, `suppressed`.
`market_data_status` field values: `live`, `partial`, `stale_snapshot`, `unavailable`.
`news_status` field values: `fresh`, `stale`, `empty`, `provider_outage`.

Suppressed sessions are logged to `SessionSendState` with `channel="suppressed"` and `error_message="suppressed: <reason>"`. They are distinct from failed deliveries (`success=False` with a real error).
