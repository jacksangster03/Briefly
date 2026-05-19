# LLM Vertical Shadow Plan (Non-Authoritative)

This document defines allowed and forbidden LLM usage for vertical intelligence.

## Allowed (Shadow-Only)
- Summarise cluster context.
- Extract candidate entities/tickers.
- Suggest candidate causal channels.
- Draft optional “why it matters” phrasing for review.

## Forbidden
- Deciding source trust tier.
- Deciding deterministic relevance/ranking authority.
- Deciding breaking alert eligibility.
- Generating fabricated outcomes (regulatory, trial, ratio, price).

## Governance
- Always log deterministic score/driver.
- Log optional LLM suggested driver separately.
- Record agreement/disagreement for later human review and dataset curation.

## Delivery Rules
- LLM shadow outputs do not change scheduler, delivery routing, or idempotency paths.
- No LLM output is sent as authoritative market truth by default.
