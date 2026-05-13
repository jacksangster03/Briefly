# Briefing Output Quality

This document describes the deterministic quality layer applied to every briefing before dispatch. All rules are rewrite-first: the guard corrects or suppresses problematic language and never blocks sending.

---

## Quality guard (`BriefingQualityGuard`)

**Module**: `app/briefing/quality_guard.py`

`BriefingQualityGuard` is instantiated with flags derived from the live briefing object and applied to the assembled section list. It runs inside `TelegramFormatter._apply_quality_guards()` before the sections are joined for delivery.

### Rule 1: Duplicate section headings

If the same bold heading (e.g. `<b>MACRO POLICY WATCH</b>`) appears in more than one section block, the duplicate heading is stripped from subsequent blocks. If stripping leaves the block empty, the block is discarded entirely and a warning is logged.

This prevents the "MACRO POLICY WATCH" heading from appearing twice when the section builder and the formatter both prepend a heading.

### Rule 2: VIX unavailability

When `vix_available=False` (VIX quote was not returned by any provider), any phrase matching `"VIX confirms"` or `"VIX does not confirm"` is replaced with `"VIX unavailable"`. A warning is logged.

The `flags_from_briefing()` helper sets `vix_available=False` when:
- No VIX quote is found in `market_setup.index_quotes` or `market_setup.macro_quotes`, or
- The VIX entry in `canonical_prices` has a null value.

The `MarketDataService.get_quotes()` also logs a provider-level warning when VIX is requested but missing from all three provider tiers (Finnhub, Alpaca, yfinance).

### Rule 3: Stale Brent

When `brent_stale=True`, any phrase matching `"Brent confirms"` or `"Brent does not confirm"` is replaced with `"Brent stale/provider-held"`. A warning is logged.

`flags_from_briefing()` sets `brent_stale=True` when the `quote_freshness` dict contains a Brent entry with `freshness_state` in `{"stale", "prior_close", "carried_forward"}`, or when any `data_basis_lines` entry contains both "brent" and "stale".

### Rule 4: "review diagnostics" wording

Any occurrence of `"review diagnostics"` is replaced with:
- `"review risk"` when the phrase appears after the word "portfolio" in the same block, or
- `"monitor confirmation"` otherwise.

This ensures no briefing ever uses the internal phrase "review diagnostics" in user-facing output.

### Rule 5: Contradictory directional claims

The guard tracks directional claims for known rate metrics (e.g. "10Y yield is higher", "10Y yield falling"). If a second claim for the same metric contradicts the first (higher vs lower), the sentence containing the second claim is suppressed and a warning is logged.

---

## VIX symbol mapping

**Module**: `app/data_sources/market_data.py`

`MarketDataService` maps the internal symbol `"VIX"` to the yfinance ticker `"^VIX"` via `_YAHOO_SYMBOL_ALIASES`. This is the third-tier fallback after Finnhub and Alpaca. If all three providers fail, a warning is logged and the quality guard substitutes the confirmation language.

---

## Brent freshness

**Module**: `app/briefing/morning_generator.py`, `app/briefing/formatter.py`

Brent freshness is tracked via `briefing.quote_freshness`, a dict keyed by symbol. The freshness classifier assigns one of: `live`, `near_real_time`, `delayed`, `prior_close`, `stale`, `carried_forward`, `unavailable`. The quality guard reads this dict and enables the stale-Brent rule when the state is `stale`, `prior_close`, or `carried_forward`.

---

## Macro Policy Watch heading

**Module**: `app/briefing/macro_policy_service.py`, `app/briefing/formatter.py`

`build_macro_policy_watch_summary()` returns a multi-line string whose first line is `"MACRO POLICY WATCH"`. The Telegram formatter strips this first line before prepending its own `<b>MACRO POLICY WATCH</b>` heading. Rule 1 of the quality guard provides belt-and-braces protection against any double heading that slips through.

The email formatter renders the Telegram output inside HTML; no separate heading is added, so the heading appears exactly once there too.

---

## Chart output

**Module**: `app/briefing/chart_builder.py`

Chart count per session is controlled by `email_density_mode` and the optional `max_email_charts` preference.

| Density mode | Default cap | Behaviour |
|---|---|---|
| `full` (default for morning) | None | All rendered charts returned |
| `medium` | 6 | Capped at 6 (plus 1 for watchlist snapshot if enabled) |
| `desk` | 5 | Capped at 5 (plus 1 for watchlist snapshot if enabled) |

Set `delivery.max_email_charts` to override the density-mode default with a fixed integer cap. Suppressed charts are logged with their chart key and the reason `density_cap(<mode>)`.

The watchlist performance snapshot (morning-only) is counted separately and added on top of the density cap when `morning_section_enabled("watchlist_snapshot")` is true.

---

## Watchlist colour palette

**Module**: `app/briefing/morning_charts.py`

`assign_watchlist_colours(symbols)` maps each ticker to a stable categorical colour using alphabetical sort order within the resolved symbol list. The palette has 10 distinct colours; symbols beyond position 9 wrap (using `colour_index % 10`) and receive `marker_style = colour_index // 10` to remain distinguishable.

Palette (hex):
```
#2563EB  #16A34A  #DC2626  #D97706  #7C3AED
#0891B2  #DB2777  #65A30D  #EA580C  #0D9488
```

The same function is used by both the morning chart bundle (`_watchlist_movers_spec`) and the watchlist chart service (`build_watchlist_chart_spec`).

---

## Valuation lens

**Module**: `app/briefing/valuation_lens.py`

Disabled by default. Enable via:

```bash
python -m app.cli prefs-set --key delivery.include_valuation_lens --value true
```

### Trigger conditions (any one is sufficient)

| Condition | Threshold |
|---|---|
| 10Y yield | >= 4.5% |
| Rates change (absolute) | >= 0.03 percentage points |
| AI or growth name single-session move | > 2% |
| Watchlist cross-section return dispersion | >= 3% |
| Event `story_type` | contains "valuation" |

### Output format

```
VALUATION LENS
- <up to 3 bullet points with metric context>
Valuation context only, not investment advice.
```

### Configuration

| Preference key | Default | Description |
|---|---|---|
| `delivery.include_valuation_lens` | `false` | Enable the module |
| `delivery.valuation_lens_max_items` | `3` | Maximum bullet points |

---

---

## Live vs prior-close freshness rules per session

**Module**: `app/briefing/session_freshness.py`

| Session | US equities | Europe equities | Commodities/FX/rates |
|---|---|---|---|
| `morning` | prior close (if ts < today or age >= 8 h) | prior close (if ts < today or age >= 6 h) | by age |
| `europe_midday` | prior close | prior close with warning if provider-returned stale; otherwise by age | by age |
| `us_pre_open` | prior close (cash indices); by age (watchlist pre-market proxies) | prior close with warning if provider-returned stale; otherwise by age | by age |
| `us_intraday_risk` | by age | by age | by age |
| `into_close` | by age | by age | by age |
| `closing_wrap` | by age | by age | by age |

Age bands (open session):
- 0–15 min: `near_real_time` (displayed as "live")
- 15–60 min: `delayed`
- >60 min: `stale`

When `session_key` is `europe_midday` or `us_pre_open` AND a Europe equity quote has a prior-day timestamp, `classify_quote_freshness` sets `warning = "provider_returned_prior_close_during_open_session"`. The `build_data_basis_lines` function appends a note: "Europe cash is open but provider quotes are prior close/delayed."

---

## VIX availability, fallback, and stale rules

**Module**: `app/data_sources/market_data.py`, `app/briefing/quality_guard.py`

- yfinance requires `"^VIX"` (caret prefix) for the CBOE Volatility Index. The alias `_YAHOO_SYMBOL_ALIASES["VIX"] = "^VIX"` is applied as a retry path when `"VIX"` returns empty.
- `flags_from_briefing()` sets `vix_available=False` when:
  - No VIX quote is found in `market_setup.index_quotes/macro_quotes`, or
  - The found VIX quote has `current_price` of `None` or `0.0`, or
  - Any `data_basis_lines` entry contains both "vix:" and "unavailable".
- `vix_stale=True` is set when the VIX freshness state is `"stale"` or `"carried_forward"`.
- Both `vix_available=False` and `vix_stale=True` suppress "VIX confirms" language.

---

## Stale Brent handling and WTI fallback

- `brent_stale=True` replaces "Brent confirms" with "Brent stale/provider-held".
- The quality guard deduplicates Brent stale caveat notes across sections: the caveat "Brent stale/provider-held; WTI used for live energy impulse." appears at most once per briefing output.
- When Brent is stale, WTI is used as the primary energy reference in geo risk and oil shock assessments.

---

## Trigger wording rules: breached vs conditional

**Module**: `app/briefing/formatter.py` (`format_trigger_line`), `app/briefing/email_formatter.py`

The shared `format_trigger_line(label, metric_value, threshold, direction, consequence)` helper:

| State | Output wording |
|---|---|
| `metric_value is None` | Trigger line suppressed entirely |
| `direction="above"`, `value >= threshold` | "X is above Y, consequence (now Z)." |
| `direction="above"`, `value < threshold` | "X above Y would consequence (now Z)." |
| `direction="below"`, `value <= threshold` | "X is below Y, consequence (now Z)." |
| `direction="below"`, `value > threshold` | "X below Y would consequence (now Z)." |

This ensures trigger lines accurately reflect whether a threshold has already been breached or is still a watchpoint.

---

## Geo headline risk vs market-confirmation distinction

**Module**: `app/briefing/morning_generator.py` (`_build_geo_risk_meter`)

When geo risk level is ELEVATED due to headline density + oil, the summary wording distinguishes between:

| Condition | Wording |
|---|---|
| VIX unavailable + haven neutral + oil move < 1% | "Geo headline risk elevated; market confirmation incomplete (VIX unavailable, haven demand neutral)." |
| VIX available + below 20 | "Market signals are not yet confirming broader stress." |
| VIX available + above 20 + oil elevated | "VIX X.X and haven +Y confirm elevated stress." |

### Oil move thresholds

| Threshold | Label |
|---|---|
| `abs(oil_delta) > 3.0%` | "oil spiking" (positive) / "oil collapsing" (negative) |
| `abs(oil_delta) > 1.0%` | "oil elevated" (positive) / "oil under pressure" (negative) |
| `abs(oil_delta) <= 1.0%` | "oil steady" |

---

## Compact FX missing-value rule

**Module**: `app/briefing/fx_section.py`

In compact/short output modes (europe_midday, us_pre_open, one-liner), the `_fmt_chg()` helper uses `for_compact=True`:
- `daily_change_pct is None`: the change is omitted. Output: "EUR/USD 1.0823" (not "EUR/USD 1.0823 (n/a)").
- `value is None`: the pair is omitted from compact output entirely.
- Full/expert mode (full block, `for_compact=False` default): returns "n/a" for unavailable change.

---

## Tests

All quality-layer behaviour is covered by:

- `app/tests/test_briefing_quality.py`: 31 tests for `BriefingQualityGuard`, `ValuationLens`, chart density, colour palette, wording rules, email non-repetition
- `app/tests/test_market_freshness.py`: VIX availability, Europe equity freshness, Brent stale flags
- `app/tests/test_geo_risk.py`: oil spike thresholds, incomplete confirmation wording, trigger wording
- `app/tests/test_fx_module.py`: compact FX output, no "(n/a)" for missing change
- `app/tests/test_macro_policy_watch_briefing.py`: macro policy watch heading appears exactly once in Telegram and email
- `app/tests/test_phase64_morning_charts.py`: chart selection, yield curve, watchlist movers with colours
