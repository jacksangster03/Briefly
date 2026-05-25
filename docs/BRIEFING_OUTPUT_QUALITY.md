# Briefing Output Quality

This document describes the deterministic quality layer applied to every briefing before dispatch. All rules are rewrite-first: the guard corrects or suppresses problematic language and never blocks sending.

---

## Holiday-aware Market Status

**Module**: `app/markets/calendar.py`

All exchange holiday logic is static and deterministic, with no live API calls. The 2026 tables cover NYSE, LSE, Xetra, and Euronext. Key rules:

- NYSE (US cash) is CLOSED on Memorial Day (2026-05-25), UK Spring Bank Holiday also applies to LSE.
- Xetra (DAX) and Euronext (CAC, EURO STOXX, IBEX) are OPEN on Whit Monday (2026-05-25); this date is NOT in their holiday tables.
- `regional_market_status(dt_utc)` returns a dict with keys: `us_cash`, `uk_equities`, `continental_europe`, `asia`.
- Possible values per key: `open`, `closed`, `holiday:<name>`, `pre_market`, `after_hours`, `weekend`, `partial:<detail>`.

---

## US Holiday Labelling

On a US market holiday (NYSE closed):

- The 13:30-15:25 CEST session window is re-titled "US Holiday / Europe Handoff" instead of "US Pre-Open Setup".
- The market clock shows "US cash closed: Memorial Day" (or the relevant holiday name) instead of "Opening next: US cash".
- The closing wrap is re-titled "Holiday / Next-Day Setup" when US cash was closed all day.
- Data basis lines show "Friday close, not live (Memorial Day)" not "near-real-time" for US equities.
- The desk read opens with "Holiday-thinned tape: ..." and names which markets are open and which are closed.
- Portfolio P&L notes include: "US holdings based on Friday close due to Memorial Day".

---

## Europe Partial-Open Logic

**Module**: `app/briefing/regional_lens.py`

Europe is never labelled "unavailable" when continental index data (DAX, EURO STOXX, CAC, IBEX) is present.

| UK status | Continental data | Europe status |
|---|---|---|
| Open | Any | "active" / "lead" / "monitor" (normal) |
| Closed (holiday/weekend) | >= 1 valid input | "partial: UK closed, continental open" |
| Closed | None | "unavailable" |

The regional skew summary must not say "Europe unavailable" while simultaneously using Europe data in a region average.

Asia prior/closed context is labelled as "prior_closed_context", not "unavailable", when closed-session values are present.

---

## Weekend / Monday Setup Title Rule

**Module**: `app/briefing/formatter.py`, `app/briefing/email_formatter.py`

Briefing titles use the local timezone date, not the UTC stored timestamp:

- A briefing generated at 00:00 CEST (= 22:00 UTC Sunday) is formatted with local CEST date = Monday 25 May.
- This prevents the title "Weekend Briefing | Sun 24 May" for a briefing whose local generation time is Monday 00:00.
- Both `TelegramFormatter` and `EmailFormatter` convert `generated_at` to the profile timezone before calling `.strftime("%a %d %b")`.
- `session_mode` (set from UTC weekday in the generator) is cross-checked against the local weekday; if they disagree, the local weekday takes priority for title selection.

---

## User-Facing Desk-Read Rules

**Module**: `app/briefing/email_formatter.py` (`_top_desk_read_lines`)

Rules that apply to every desk-read line:

1. No "rates score +X.XX" in user-facing output. Internal scores appear only in diagnosis logs.
2. No "regional unavailable" in user-facing text. If regional data is absent, the line is omitted or replaced with "regional data pending".
3. On a US holiday: desk read opens with "Holiday-thinned tape: ..." and states which markets are open and closed in plain language.
4. If geo headline risk is high (ELEVATED/HIGH/EXTREME) but VIX is unavailable, the geo lens line appends: "(note: VIX unavailable, market confirmation of geo risk is incomplete)".
5. Portfolio posture on a US holiday appends: "(US holdings based on Friday close due to Memorial Day)" or the relevant holiday name.

---

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

## Provider outage handling (market + news)

When live providers are unavailable in-cycle (for example DNS/network failure or multiple provider circuit-breakers open), the morning briefing now uses explicit outage semantics:

- Header includes: `DATA OUTAGE / PROVIDER DEGRADED`
- Market tape uses: `Market data unavailable; no directional read generated.`
- News block uses: `News scan returned no usable items; provider diagnostics required.`
- Footer distinguishes provider outage vs filtering:
  - `provider outage (raw fetch 0)` when raw provider ingestion is zero
  - `X raw fetched, 0 selected after filters` when ingestion succeeded but ranking/suppression removed all candidates

---

## Vertical intelligence shadow quality (Phase 1)

- Vertical source adapters are API/RSS-first and fail-soft.
- Geopolitics and AI/Tech sources are diagnostics-first; briefing output remains off by default.
- Optional shadow briefing lines are deterministic summaries only and capped for compactness.
- Vertical shadow output never changes alert eligibility or delivery authority.

Deterministic suppression safeguards:

- Oil transmission chart is hidden when both WTI and Brent are unavailable.
- Geo confirmation ladder is hidden when all confirmation inputs are unavailable.
- Snapshot delta no longer writes fake portfolio contribution deltas from incomplete quote sets.

Unavailable vs stale vs true zero:

- `unavailable`: provider did not return usable data.
- `stale/prior_close`: value exists but is not live for the active session context.
- `0.00%`: treated as a real move only when a valid quote/value exists; missing values are not coerced into neutral-looking zeroes.

---

## Session Diagnosis Engine

**Module**: `app/briefing/session_diagnosis.py`

Briefly now runs a deterministic session diagnosis pass that outputs:

- `primary_driver`
- `secondary_drivers`
- `rejected_drivers`
- `regime_label`
- `confidence`
- `one_sentence_diagnosis`
- `regional_diagnosis`
- `portfolio_diagnosis`
- `data_caveats`
- score map:
  - `rates_pressure_score`
  - `energy_geo_score`
  - `equity_breadth_score`
  - `regional_divergence_score`
  - `tech_ai_concentration_score`
  - `portfolio_transmission_score`
  - `data_quality_score`

### Confirmation rules (risk-on/risk-off)

- `risk_on_confirmation` requires breadth + regional participation support.
- `risk_off_confirmation` requires breadth weakness + regional weakness.
- `rates_headwind` can become primary when 10Y level/change thresholds are breached.
- `geo_energy_pressure` can become primary when oil + geo stress align.
- If market data is unavailable, regime is forced to `DATA DEGRADED` (never “Mixed tape”).

### Geo headline vs market confirmation

Geo headline intensity and market confirmation are intentionally separated:

- Geo can remain elevated from headlines/context.
- If VIX/haven confirmation is missing, diagnosis records this as a rejected confirmation path.

This avoids “headline risk == fully confirmed market stress” shortcuts.

---

## Trigger Board rules

Legacy generic trigger lines are replaced by deterministic trigger-board buckets:

- **Active triggers**: breached thresholds now impacting read-through.
- **Watch triggers**: conditional thresholds not yet breached.
- **Cooled / invalidated**: previously hot channels that have cooled.

Session-specific headers:

- Morning: `TODAY'S TRIGGER BOARD`
- Europe Midday: `EUROPE/US HANDOFF TRIGGERS`
- US Pre-Open: `OPENING TRIGGER BOARD`
- US Intraday: `INTRADAY CONFIRMATION TRIGGERS`
- Into Close: `CLOSE-QUALITY TRIGGERS`
- Closing Wrap: `TOMORROW TRIGGER BOARD`

All trigger text is deterministic and uses explicit breached/conditional/unavailable wording.

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

---

## Session Tape Recap

**Module**: `app/briefing/session_tape.py`

The session tape provides a deterministic, post-session summary of how each instrument moved through its daily range.

### SessionTapeEntry fields

Each entry records:
- `prior_close`, `session_open`, `session_high`, `session_low`, `latest`: raw prices (None when unavailable)
- `change_vs_prior_close_pct`: percentage change vs prior session close
- `change_vs_open_pct`: percentage change from session open to current/close
- `range_position_pct`: `(latest - low) / (high - low)`, None when H/L unavailable
- `range_label`: descriptive bucket (see thresholds below)
- `tape_verdict`: deterministic narrative (see logic below)
- `data_quality`: "full" | "partial" | "prior_close_only" | "unavailable"

### OHLC data source

OHLC fields (`open`, `high`, `low`) are populated by the existing provider chain (Finnhub `get_quotes`, then yfinance `fast_info.day_high` / `day_low` / `open`). No extra API calls are made. When a provider returns zero for open/high/low, those fields are set to None and the entry degrades gracefully.

### Range-position labels and thresholds

| range_position_pct | label |
|---|---|
| >= 0.80 | near highs |
| >= 0.60 | upper half |
| >= 0.40 | mid-range |
| >= 0.20 | lower half |
| < 0.20 | near lows |
| None | range unavailable |

### Tape verdict logic (deterministic)

| Condition | Verdict |
|---|---|
| change_vs_open > 0.3% AND range_pos >= 0.70 | rallied from open |
| change_vs_open > 0.3% AND range_pos < 0.40 | faded from highs |
| change_vs_open < -0.3% AND range_pos >= 0.60 | recovered from lows |
| range_pos >= 0.80 | closed near highs |
| range_pos < 0.20 | closed near lows |
| abs(change_vs_prior_close) < 0.15% | flat/mixed |
| otherwise | unavailable |

Tape verdicts are appended to closing_wrap lines only.

### Session recap placement per session

| Session | US recap heading | Europe recap heading |
|---|---|---|
| closing_wrap | US CASH SESSION RECAP | EUROPE CASH SESSION RECAP |
| into_close | US CASH SESSION RECAP | EUROPE CASH SESSION RECAP |
| europe_midday | (absent) | EUROPE SESSION SO FAR |
| us_pre_open | (absent) | (absent) |
| morning | (absent) | (absent) |

### Watchlist session tape

Only rendered when at least 3 watchlist instruments have a valid `change_vs_open_pct`. Suppressed (with DEBUG log) when only prior-close data is available.

---

## Rates & Macro Tape

**Module**: `app/briefing/session_tape.build_rates_macro_tape()`

A compact rates/macro header block rendered for: morning, us_pre_open, into_close, closing_wrap.

### Format

```
RATES & MACRO TAPE
2Y 3.95% (+5bp) | 10Y 4.48% (+2bp, above 4.45% trigger) | 10Y-2Y +53bp
USD stronger | WTI $101.29 (+0.21%, elevated) | Gold $2,340 (+0.3%)
```

### Rules

- Uses data already assembled in the briefing context (no re-fetches).
- 10Y trigger note: "above 4.45% trigger" when 10Y >= 4.45%; "approaching 4.45%" when >= 4.40%.
- Spread: 10Y minus 2Y in basis points.
- USD direction: inferred from fx_pulse_section text.
- WTI qualitative label: elevated (>=+2%), firm (>=+0.5%), steady, easing (<=-0.5%), under pressure (<=-2%).
- Suppressed entirely when fewer than 3 values are available.
- In Telegram: placed after the session header and any "WHAT CHANGED" block, before macro policy watch.
- In email: placed in the top header area before desk read and trigger lines.

---

## Valuation Lens

**Module**: `app/briefing/valuation_lens.py`

Disabled by default (`include_valuation_lens = False`). Activates when:
- 10Y >= 4.4% (rates pressure tightening threshold)
- At least 2 high-multiple names (from `_AI_GROWTH_SYMBOLS`) moved > 2% intraday
- Watchlist return dispersion >= 3%
- A valuation/earnings story appears in top themes

### Peer groups

| Group | Symbols | Ratios shown |
|---|---|---|
| Mega-cap tech | AAPL, MSFT, GOOGL, AMZN, META, NVDA | fwd P/E, EV/EBITDA |
| Semis | NVDA, AMD, AVGO, QCOM, TSM, INTC, ASML | P/E, revenue growth |
| Pharma/biotech | JNJ, PFE, MRK, ABBV, LLY, BMY, etc. | P/E, pipeline context |
| ETFs/indices | SPY, QQQ, IWM, GLD, TLT, etc. | expense ratio, yield |

Additional ratios supported: FCF yield (freeCashflow / marketCap) and PEG ratio.

### No-hallucination guarantee

All ratios are fetched from yfinance `.info`. If a field is None or absent, it is omitted from output; no substitution or fabrication occurs.

---

## Tests

All quality-layer behaviour is covered by:

- `app/tests/test_briefing_quality.py`: 52 tests for `BriefingQualityGuard`, `ValuationLens`, chart density, colour palette, wording rules, email non-repetition, rates tape placement, rates tape suppression
- `app/tests/test_session_tape.py`: 14 tests for session tape range logic, tape verdict, session headings, watchlist suppression, rates tape trigger wording, valuation no-hallucination, chart richness preservation
- `app/tests/test_market_freshness.py`: VIX availability, Europe equity freshness, Brent stale flags
- `app/tests/test_geo_risk.py`: oil spike thresholds, incomplete confirmation wording, trigger wording
- `app/tests/test_fx_module.py`: compact FX output, no "(n/a)" for missing change
- `app/tests/test_macro_policy_watch_briefing.py`: macro policy watch heading appears exactly once in Telegram and email
- `app/tests/test_phase64_morning_charts.py`: chart selection, yield curve, watchlist movers with colours

---

## Morning Diagnosis Upgrade (Deterministic)

Morning diagnosis now prefers measurable drivers over generic text:

- rates/valuation pressure is explicitly named when 10Y and leadership inputs support it
- regional split is explicitly named when Europe/US diverge
- mixed fallback remains only when no stronger deterministic driver is present

This prevents generic "balanced factors" language when a clear rates or regional regime is visible.

---

## Chart Clarity Updates

- **Breadth & Leadership** no longer repeats US/Europe/Asia bars (those stay in Regional Divergence).
- **Yield Curve Shape** now labels:
  - today values + bp vs 1W
  - available 1W values per tenor (`1W: x.xx%`) near the grey dashed points

If prior-week value is missing for a tenor, only that tenor's grey label is omitted.

---

## Market Setup Formatting Rules

- Colour styling is applied to the **actual move** only.
- Range text remains neutral:
  - `| range -0.7% to +0.3% | closed upper half`
  - `| range ... | trading near highs`
- Range remains instrument-specific from each quote's own OHLC inputs.

---

## VIX Availability Rules (All Core Sessions)

VIX is treated as a core risk instrument in:
- morning
- europe_midday
- us_pre_open
- us_intraday_risk
- into_close
- closing_wrap

Rules:
- VIX is pinned in trimmed market-setup rows (it is not dropped by density trimming).
- If live quote fetch misses VIX, generator attempts latest available fallback from recent `^VIX` history.
- Data-basis lines always include explicit VIX status for core sessions.
- If VIX is unavailable, risk wording must use **unconfirmed** framing (never silent neutral confirmation).

---

## Regional vs Breadth Separation

- **Regional Divergence** owns regional bars (US/Europe/Asia).
- **Breadth & Leadership** owns internals/factor structure:
  - Breadth % Up
  - Small-Large
  - Growth-Defensive
  - optional factor spreads (Semis/Tech/Energy/Financials vs market when available)

Regional bars must not be repeated inside Breadth & Leadership.

---

## Yield Curve Prior-Week Labelling

Yield curve rendering now includes:
- orange today labels (`x.xx%` and `+/-bp vs 1W`)
- grey dashed prior-week labels per tenor (`1W: x.xx%`) when available

Missing prior-week value for one tenor suppresses only that tenor's grey label.

---

## Live Risk Board Rules

Trigger output is stateful, not purely hypothetical:

- `ACTIVE`: threshold already breached now
- `ELEVATED`: risk active but below escalation zone
- `WATCH`: threshold not breached yet
- `CONTAINED` / `COOLED`: stress easing
- `UNAVAILABLE` / `UNCONFIRMED`: required input missing

Examples:
- Rates above 4.45% => `Rates: ACTIVE ... already above 4.45%`
- WTI +1% to +3% => `Oil: ELEVATED`
- VIX unavailable => `Volatility: UNCONFIRMED`

---

## Applied News Stack

Morning briefings can include a compact deterministic **Applied News Stack**:

- Macro/Rates
- Regional
- Sector
- Watchlist
- Portfolio
- Event Risk
- Geopolitical

Each line includes:
- headline
- why it matters
- affected assets (when available)
- confirmation state and source count

Low-signal items are not force-filled just to populate sections.
