# Briefly Product Modules

Briefly is organized as one platform with three modules:

1. **Market Briefing** (live)
   - Purpose: deliver morning, intraday, and breaking market intelligence.
   - Core paths:
     - `app/briefing/`
     - `app/processing/`
     - `app/messaging/`
2. **Portfolio Control** (live)
   - Purpose: manage holdings snapshots and personalization overrides that shape relevance/routing.
   - Core paths:
     - `app/portfolio/`
     - `app/personalization/`
     - `app/web/`
3. **Trading Lab** (planned)
   - Purpose: add execution/sentiment workflows once intelligence + controls are stable.
   - Core path:
     - `app/trading/`

Supporting modules:
- `app/tracker/` for position/state tracking helpers.
- `app/analytics/` for exposure/risk/signal analytics helpers.

This split keeps the deterministic intelligence pipeline shared, while product surfaces evolve independently.
