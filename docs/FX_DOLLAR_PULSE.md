# FX & Dollar Pulse: Architecture Reference

This document covers the design, data contracts, and operating rules for
the FX & Dollar Pulse feature in Briefly.

---

## Architecture overview

The FX module consists of four components:

```
app/fx/basket.py      - Profile-aware basket builder (FXInstrument list)
app/fx/panel.py       - Data fetcher (FXQuote list, provider-agnostic)
app/fx/signals.py     - Deterministic signal builder (FXSignals)
app/briefing/fx_section.py - Session-aware text formatter
```

The morning generator (`app/briefing/morning_generator.py`) calls these in
sequence after assembling macro/rates context. Results are stored on the
`MorningBriefing` schema as `fx_pulse_section` (text) and
`fx_materiality_score` (int).

The Telegram formatter and email formatter both include the FX section if
it is non-empty. The email formatter inherits the section via the Telegram
formatter's output (both formats share the same narrative pipeline).

---

## FXInstrument schema

```python
@dataclass
class FXInstrument:
    label: str           # e.g. "EUR/USD"
    symbol: str          # e.g. "EURUSD=X" or "DTWEXBGS"
    source: str          # "yfinance" | "fred" | "ecb"
    base: str            # base currency, e.g. "EUR"
    quote: str           # quote currency, e.g. "USD"
    is_dxy_proxy: bool   # True for trade-weighted USD proxy
    optional: bool       # True for conditionally included pairs
    condition: str | None  # trigger condition: "oil_shock", "commodity_shock"
```

---

## FXQuote schema

```python
@dataclass
class FXQuote:
    instrument: FXInstrument
    value: float | None
    daily_change_pct: float | None
    change_5d_pct: float | None
    source: str
    freshness: str   # "live" | "delayed" | "prior_close" | "stale" | "unavailable"
    status: str      # "ok" | "stale" | "unavailable"
    fetched_at: datetime | None
```

---

## Basket builder logic per region

`build_fx_basket(profile, settings, context)` reads `profile["home_region"]`
and selects the appropriate core and optional pair set.

### Spain / Eurozone / EMEA

Core: EUR/USD, Trade-weighted USD (FRED DTWEXBGS), EUR/GBP, USD/JPY, USD/CNH

Optional pairs (soft-optional, included by default):
- EUR/CHF

Optional pairs (condition-gated):
- USD/NOK: included when `context["oil_shock"] is True`
- AUD/USD: included when `context["commodity_shock"] is not None`

### US / Americas

Core: Trade-weighted USD (FRED), EUR/USD, USD/JPY, GBP/USD, USD/CNH

Optional:
- USD/CAD (soft-optional, always included)

### UK / United Kingdom

Core: GBP/USD, EUR/GBP, Trade-weighted USD (FRED), USD/JPY

Optional:
- GBP/JPY (soft-optional, always included)

### APAC / Asia / Japan / Australia

Core: USD/JPY, USD/CNH, AUD/USD, Trade-weighted USD (FRED)

Secondary (always included): EUR/USD

### Default fallback

EUR/USD, Trade-weighted USD, USD/JPY, GBP/USD

---

## Data sources

| Source | Instruments | API key required | Freshness |
|---|---|---|---|
| FRED | DTWEXBGS (trade-weighted USD) | Yes (`fred_api_key`) | T+1 (daily) |
| yfinance | All FX pairs (EURUSD=X, etc.) | No | Prior close |
| ECB | EUR-base reference rates | No | Prior close |

FRED data is T+1: the most recent observation may be one business day old.
The module marks FRED data as `freshness="stale"` when the observation is
more than one day old.

yfinance CNH pairs fall back to CNY (`USDCNY=X`) if `USDCNH=X` returns
no data.

ECB reference rates fall back to yfinance if the ECB endpoint fails.

---

## Signal logic

All signals are computed by `build_fx_signals(panel, context)`.

### USD pressure

Source: Trade-weighted USD (DXY proxy, FRED) or EUR/USD (inverse).

- daily_change_pct > 0.2%: "stronger"
- daily_change_pct < -0.2%: "weaker"
- else: "neutral"
- no data: "unavailable"

### EUR pressure

Source: EUR/USD.

- daily_change_pct > 0.2%: "stronger"
- daily_change_pct < -0.2%: "weaker"
- else: "neutral"

### Sterling pressure

Source: GBP/USD (primary) or EUR/GBP (inverse, fallback).

- Same 0.2% threshold
- "not_applicable" if no GBP pair is in the basket

### Yen risk signal

Source: USD/JPY.

- USD/JPY daily_change_pct > 0.5% (yen weakening): "carry_on"
- USD/JPY daily_change_pct < -0.5% (yen strengthening): "risk_off"
- else: "neutral"

### China FX stress

Source: USD/CNH.

- daily_change_pct > 0.5% (yuan weakening): "active"
- else: "neutral"

---

## Materiality scoring table

| Trigger | Score added |
|---|---|
| DXY/trade-weighted USD abs daily > 0.5% | +2 |
| EUR/USD abs daily > 0.5% | +2 |
| EUR/GBP abs daily > 0.35% | +1 |
| USD/JPY abs daily > 0.7% | +2 |
| USD/CNH abs daily > 0.5% | +2 |
| Gold move > 1% (from context) | +1 |
| Oil move > 2% (from context) | +1 |
| US 10Y move > 5 bp (from context) | +1 |
| Regional spread > 1% (from context) | +1 |

Score ranges:

- 0-1: "low"
- 2-3: "medium"
- 4+: "high"

---

## Session inclusion rules

| Session key | Include when | Format |
|---|---|---|
| morning | fx_materiality in ("medium", "high") | Full block |
| europe_midday | materiality_score >= 2 | Short block |
| us_pre_open | materiality_score >= 2 | Short block |
| us_intraday_risk | fx_materiality == "high" | One-liner |
| into_close | fx_materiality == "high" | One-liner |
| closing_wrap | fx_materiality in ("medium", "high") | Full block |
| saturday_weekend_briefing | fx_materiality in ("medium", "high") | Full block |
| sunday_weekend_watch | fx_materiality in ("medium", "high") | Full block |

---

## Dashboard API contract

`GET /api/fx-pulse`

Query parameters:
- `profile` (str, default "default_user")
- `mode` ("simple" | "expert", default "simple")

Simple mode response:

```json
{
  "usd_pressure": "stronger | weaker | neutral | unavailable",
  "eur_usd": {"value": 1.1050, "change_pct": 0.60},
  "usd_jpy": {"value": 156.50, "change_pct": 0.70},
  "fx_materiality": "low | medium | high",
  "materiality_score": 4,
  "profile_basket_label": "Spain/Eurozone",
  "drivers": ["EUR/USD +0.60%", "USD/JPY +0.70%"],
  "missing": [],
  "eur_pressure": "stronger | weaker | neutral | unavailable",
  "sterling_pressure": "stronger | weaker | neutral | not_applicable | unavailable",
  "yen_risk_signal": "risk_off | carry_on | neutral | unavailable",
  "china_fx_stress": "active | neutral | unavailable",
  "note": "Deterministic signal, not a forecast."
}
```

Expert mode (`?mode=expert`) adds a `quotes` array with full FXQuote data
per instrument, including source, freshness, status, daily_change_pct,
change_5d_pct, and fetched_at.

---

## FX Pulse chart

Chart key: `fx_pulse_chart`

Included in the full morning chart bundle when:
- `fx_materiality_score >= 5`
- Session is a full-mode session (morning, closing wrap)

Each FX pair is rendered as a line series with a stable colour assignment
from `WATCHLIST_COLOUR_PALETTE`. Colour indices are fixed in
`_FX_COLOUR_ASSIGNMENTS` so the same pair always gets the same colour.

The chart does not displace other charts. It is added to the selection
stack only if capacity allows.

---

## Configuration options

| Setting | Default | Description |
|---|---|---|
| `fred_api_key` | "" | Required for FRED-sourced instruments |
| `FEATURE_FX_PULSE_CHART` env var | "true" | Enable/disable the FX Pulse chart |
| profile `home_region` | "spain" | Drives basket selection |

---

## Fallback and degradation behaviour

- If a single instrument fetch fails, its `FXQuote` gets `status="unavailable"` and is added to `signals.missing`. The rest of the panel is unaffected.
- If all instruments are unavailable, `build_fx_section_text` returns "FX data unavailable for this session."
- If the entire FX module raises an unexpected exception, the morning generator catches it, logs at DEBUG level, and continues without a FX section.
- The "confirms" language quality guard already applied to other sections also applies here: if a metric is unavailable, the section never says "confirms".
- The `/api/fx-pulse` endpoint returns a graceful degradation response (with `usd_pressure: "unavailable"`) if providers fail, rather than a 500 error.
