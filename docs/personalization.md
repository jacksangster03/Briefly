# Personalization

User-specific behavior is driven by YAML rather than hardcoded business logic.

## Files

- `configs/user_profile.yaml`
- `configs/watchlists.yaml`
- `configs/interest_weights.yaml`
- `configs/alert_rules.yaml`

## What can be personalized

- timezone
- home region
- coverage weights
- sector weights
- watchlists
- delivery times
- quiet hours
- message depth/style
- alert thresholds

The current implementation already uses sector weights, watchlists, and geography weighting inside the ranking flow. Holdings-aware relevance is deferred to a later phase.
