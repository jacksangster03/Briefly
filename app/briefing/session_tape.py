"""
Deterministic session tape computation.
Computes open-to-close, range position and tape verdict for instruments.
Degrades gracefully when OHLC data is unavailable.

Data source: QuoteData objects already in the briefing context (open, high, low,
current_price, previous_close fields populated by yfinance/Finnhub providers).
No extra network calls are made unless a quote dict is passed directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.logger import get_logger

logger = get_logger("session_tape")

# Instruments included in US cash session recap
_US_CASH_SYMBOLS: frozenset[str] = frozenset({
    "^GSPC", "^SPX", "SPY",            # S&P 500
    "^IXIC", "^COMP", "QQQ",           # Nasdaq
    "^RUT", "IWM",                      # Russell 2000
    "CL=F", "WTI",                      # WTI Crude
    "DGS10", "^TNX", "US10Y",          # 10Y Yield (handled specially)
})

# Instruments included in Europe session recap
_EUROPE_CASH_SYMBOLS: frozenset[str] = frozenset({
    "^STOXX50E", "SX5E",               # EURO STOXX 50
    "^GDAXI", "DAX",                    # DAX
    "^FTSE",                            # FTSE 100
    "^FCHI",                            # CAC 40
    "^IBEX",                            # IBEX 35
})

# Display labels by symbol (canonical mapping)
_DISPLAY_LABELS: dict[str, str] = {
    "^GSPC": "S&P 500", "SPY": "S&P 500", "^SPX": "S&P 500",
    "^IXIC": "Nasdaq", "QQQ": "Nasdaq", "^COMP": "Nasdaq",
    "^RUT": "Russell 2000", "IWM": "Russell 2000",
    "^STOXX50E": "EURO STOXX 50", "SX5E": "EURO STOXX 50",
    "^GDAXI": "DAX", "DAX": "DAX",
    "^FTSE": "FTSE 100",
    "^FCHI": "CAC 40",
    "^IBEX": "IBEX 35",
    "CL=F": "WTI", "WTI": "WTI",
    "DGS10": "US 10Y", "^TNX": "US 10Y", "US10Y": "US 10Y",
    "GC=F": "Gold", "GOLD": "Gold",
}

# Asset class for chart colour assignment
_ASSET_CLASS: dict[str, str] = {
    "^GSPC": "equity", "SPY": "equity", "^SPX": "equity",
    "^IXIC": "equity", "QQQ": "equity", "^COMP": "equity",
    "^RUT": "equity", "IWM": "equity",
    "^STOXX50E": "equity", "SX5E": "equity",
    "^GDAXI": "equity", "DAX": "equity",
    "^FTSE": "equity",
    "^FCHI": "equity",
    "^IBEX": "equity",
    "CL=F": "commodity", "WTI": "commodity",
    "DGS10": "rates", "^TNX": "rates", "US10Y": "rates",
    "GC=F": "commodity", "GOLD": "commodity",
}

_ASSET_CLASS_COLOURS: dict[str, str] = {
    "equity": "#2563EB",
    "rates": "#DC2626",
    "commodity": "#D97706",
    "fx": "#16A34A",
}


@dataclass
class SessionTapeEntry:
    """Single-instrument session tape snapshot."""
    label: str
    symbol: str
    prior_close: float | None
    session_open: float | None
    session_high: float | None
    session_low: float | None
    latest: float | None
    change_vs_prior_close_pct: float | None
    change_vs_open_pct: float | None
    range_position_pct: float | None     # (latest - low) / (high - low)
    range_label: str                      # descriptive bucket label
    tape_verdict: str                     # deterministic narrative label
    data_quality: str                     # "full" | "partial" | "prior_close_only" | "unavailable"
    asset_class: str = field(default="equity")


def _compute_range_label(pos: float | None) -> str:
    if pos is None:
        return "range unavailable"
    if pos >= 0.80:
        return "near highs"
    if pos >= 0.60:
        return "upper half"
    if pos >= 0.40:
        return "mid-range"
    if pos >= 0.20:
        return "lower half"
    return "near lows"


def _compute_tape_verdict(
    change_vs_open_pct: float | None,
    change_vs_prior_close_pct: float | None,
    range_position_pct: float | None,
) -> str:
    """Deterministic tape verdict from position and move data."""
    cvop = change_vs_open_pct
    rpos = range_position_pct

    if cvop is not None and cvop > 0.3 and rpos is not None and rpos >= 0.70:
        return "rallied from open"
    if cvop is not None and cvop > 0.3 and rpos is not None and rpos < 0.40:
        return "faded from highs"
    if cvop is not None and cvop < -0.3 and rpos is not None and rpos >= 0.60:
        return "recovered from lows"
    if rpos is not None and rpos >= 0.80:
        return "closed near highs"
    if rpos is not None and rpos < 0.20:
        return "closed near lows"
    cvpc = change_vs_prior_close_pct
    if cvpc is not None and abs(cvpc) < 0.15:
        return "flat/mixed"
    return "unavailable"


def _build_entry_from_quote(quote: Any, symbol_override: str | None = None) -> SessionTapeEntry | None:
    """Build a SessionTapeEntry from a QuoteData-like object.

    Returns None if the quote carries no usable price at all.
    Degrades gracefully: sets OHLC fields to None when zero/missing.
    """
    sym = symbol_override or str(getattr(quote, "symbol", "") or "").strip().upper()
    if not sym:
        return None

    label = _DISPLAY_LABELS.get(sym, getattr(quote, "display_name", "") or sym)
    asset_class = _ASSET_CLASS.get(sym, "equity")

    def _nonnull(val: float) -> float | None:
        return float(val) if float(val) > 0 else None

    latest_raw = float(getattr(quote, "current_price", 0.0) or 0.0)
    prior_close_raw = float(getattr(quote, "previous_close", 0.0) or 0.0)
    open_raw = float(getattr(quote, "open", 0.0) or 0.0)
    high_raw = float(getattr(quote, "high", 0.0) or 0.0)
    low_raw = float(getattr(quote, "low", 0.0) or 0.0)

    latest = _nonnull(latest_raw)
    prior_close = _nonnull(prior_close_raw)
    session_open = _nonnull(open_raw)
    session_high = _nonnull(high_raw)
    session_low = _nonnull(low_raw)

    if latest is None:
        return None

    # Compute changes
    change_vs_prior_close_pct: float | None = None
    if prior_close is not None and prior_close > 0:
        change_vs_prior_close_pct = (latest - prior_close) / prior_close * 100.0

    change_vs_open_pct: float | None = None
    if session_open is not None and session_open > 0:
        change_vs_open_pct = (latest - session_open) / session_open * 100.0

    # Range position: requires both high and low
    range_position_pct: float | None = None
    if (
        session_high is not None
        and session_low is not None
        and session_high > session_low
    ):
        range_position_pct = (latest - session_low) / (session_high - session_low)

    range_label = _compute_range_label(range_position_pct)

    # Data quality assessment
    if session_open is not None and session_high is not None and session_low is not None:
        data_quality = "full"
    elif prior_close is not None and (session_open is not None or session_high is not None):
        data_quality = "partial"
    elif prior_close is not None:
        data_quality = "prior_close_only"
    else:
        data_quality = "unavailable"

    tape_verdict = _compute_tape_verdict(change_vs_open_pct, change_vs_prior_close_pct, range_position_pct)

    return SessionTapeEntry(
        label=label,
        symbol=sym,
        prior_close=prior_close,
        session_open=session_open,
        session_high=session_high,
        session_low=session_low,
        latest=latest,
        change_vs_prior_close_pct=change_vs_prior_close_pct,
        change_vs_open_pct=change_vs_open_pct,
        range_position_pct=range_position_pct,
        range_label=range_label,
        tape_verdict=tape_verdict,
        data_quality=data_quality,
        asset_class=asset_class,
    )


def build_session_tape(
    quotes: list[Any],
    session: str,
    *,
    section: str = "us_cash",
) -> list[SessionTapeEntry]:
    """Build session tape entries from a list of QuoteData-like objects.

    Parameters
    ----------
    quotes:
        List of QuoteData (or compatible duck-typed) objects already fetched.
    session:
        Session routing key: "closing_wrap", "into_close", "europe_midday",
        "us_preopen", "us_intraday", etc.
    section:
        Which instrument group to include: "us_cash", "europe_cash", "watchlist".
        When "watchlist", all symbols in quotes are processed.

    Returns
    -------
    List of SessionTapeEntry, possibly empty if no relevant quotes found.
    Entries with data_quality="unavailable" are excluded.
    """
    if section == "us_cash":
        relevant = _US_CASH_SYMBOLS
    elif section == "europe_cash":
        relevant = _EUROPE_CASH_SYMBOLS
    else:
        # Watchlist: all symbols
        relevant = None

    entries: list[SessionTapeEntry] = []
    seen_labels: set[str] = set()

    for quote in quotes:
        sym = str(getattr(quote, "symbol", "") or "").strip().upper()
        if relevant is not None and sym not in relevant:
            # Also check display_name for loose matches
            dname = (getattr(quote, "display_name", "") or "").upper()
            if not any(key in dname for key in _us_section_tokens(section)):
                continue

        entry = _build_entry_from_quote(quote, sym)
        if entry is None or entry.data_quality == "unavailable":
            continue

        # Deduplicate by canonical label (e.g. SPY and ^GSPC both map to "S&P 500")
        if entry.label in seen_labels:
            continue
        seen_labels.add(entry.label)
        entries.append(entry)

    return entries


def _us_section_tokens(section: str) -> tuple[str, ...]:
    if section == "us_cash":
        return ("S&P", "NASDAQ", "RUSSELL", "WTI", "CRUDE", "10Y")
    if section == "europe_cash":
        return ("STOXX", "DAX", "FTSE", "CAC", "IBEX")
    return ()


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------

def _fmt_pct(val: float, sign: bool = True) -> str:
    if sign:
        return f"{val:+.2f}%"
    return f"{val:.2f}%"


def format_session_tape_text(
    entries: list[SessionTapeEntry],
    section: str,
    session: str,
) -> str:
    """Format a session tape text block.

    Parameters
    ----------
    entries:
        Tape entries (typically from build_session_tape).
    section:
        "us_cash" | "europe_cash" | "watchlist"
    session:
        "closing_wrap" | "into_close" | "europe_midday" | "us_preopen" | "us_intraday"

    Returns
    -------
    Formatted text block, or empty string if no entries have data.
    """
    if not entries:
        return ""

    is_closing_session = session in {"closing_wrap", "into_close"}
    is_europe_section = section == "europe_cash"
    is_us_section = section == "us_cash"
    is_watchlist = section == "watchlist"

    # Determine heading
    if is_watchlist:
        heading = "WATCHLIST SESSION TAPE"
    elif is_us_section and is_closing_session:
        heading = "US CASH SESSION RECAP"
    elif is_europe_section and is_closing_session:
        heading = "EUROPE CASH SESSION RECAP"
    elif is_europe_section:
        heading = "EUROPE SESSION SO FAR"
    else:
        heading = "US CASH SESSION RECAP"

    lines = [heading]
    has_data = False

    for entry in entries:
        if entry.data_quality == "unavailable":
            continue
        has_data = True

        parts: list[str] = []

        # Change vs prior close
        if entry.change_vs_prior_close_pct is not None:
            parts.append(f"{_fmt_pct(entry.change_vs_prior_close_pct)} vs prior close")

        # Change vs open
        if entry.change_vs_open_pct is not None:
            parts.append(f"{_fmt_pct(entry.change_vs_open_pct)} from open")

        # Range label
        if entry.range_label and entry.range_label != "range unavailable":
            parts.append(entry.range_label)

        # Tape verdict (only for closing sessions)
        if is_closing_session and entry.tape_verdict not in {"unavailable", "flat/mixed"}:
            parts.append(entry.tape_verdict)

        if not parts and entry.data_quality == "prior_close_only":
            # Minimal line: just show the change
            lines.append(f"- {entry.label}: data limited to prior close.")
            continue

        detail = ", ".join(parts) + "." if parts else ""
        if detail:
            lines.append(f"- {entry.label}: {detail}")
        else:
            lines.append(f"- {entry.label}: insufficient data.")

    if not has_data:
        return ""

    return "\n".join(lines)


def format_watchlist_tape_compact(entries: list[SessionTapeEntry]) -> str:
    """Build a compact WATCHLIST SESSION TAPE block (max 5 per group).

    Only included when at least 3 entries have valid change_vs_open_pct.
    Returns empty string when suppressed.
    """
    valid = [e for e in entries if e.change_vs_open_pct is not None]
    if len(valid) < 3:
        logger.debug(
            "Watchlist session tape suppressed: only %d/%d entries have change_vs_open_pct",
            len(valid),
            len(entries),
        )
        return ""

    sorted_entries = sorted(valid, key=lambda e: e.change_vs_open_pct or 0.0, reverse=True)

    leaders = [e for e in sorted_entries if (e.change_vs_open_pct or 0.0) > 0.1][:5]
    laggards = [e for e in sorted_entries if (e.change_vs_open_pct or 0.0) < -0.1][-5:]
    near_highs = [
        e for e in valid
        if e.range_position_pct is not None and e.range_position_pct >= 0.80
    ][:5]

    lines = ["WATCHLIST SESSION TAPE"]
    if leaders:
        leader_str = ", ".join(
            f"{e.label} {_fmt_pct(e.change_vs_open_pct or 0.0)}"
            for e in leaders
        )
        lines.append(f"- Leaders from open: {leader_str}")
    if laggards:
        laggard_str = ", ".join(
            f"{e.label} {_fmt_pct(e.change_vs_open_pct or 0.0)}"
            for e in laggards
        )
        lines.append(f"- Laggards from open: {laggard_str}")
    if near_highs:
        highs_str = ", ".join(e.label for e in near_highs)
        lines.append(f"- Near session highs: {highs_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rates & Macro Tape
# ---------------------------------------------------------------------------

def build_rates_macro_tape(briefing: Any) -> str:
    """Build a compact RATES & MACRO TAPE block.

    Uses data already in the briefing context. No additional fetches.
    Returns empty string if fewer than 3 values are available, or for sessions
    where the tape is not shown.

    Shown for: morning, us_pre_open, us_preopen, into_close, closing_wrap.
    """
    session_key = str(getattr(briefing, "session_key", "") or "morning").lower()
    if session_key not in {"morning", "us_pre_open", "us_preopen", "into_close", "closing_wrap"}:
        return ""

    lines_part1: list[str] = []   # yields line
    lines_part2: list[str] = []   # usd/wti/gold line

    ten_y_val: float | None = None
    two_y_val: float | None = None
    ten_y_chg: float | None = None
    two_y_chg: float | None = None

    setup = getattr(briefing, "market_setup", None)

    if setup:
        t10 = getattr(setup, "treasury_10y", None)
        t2 = getattr(setup, "treasury_2y", None)
        if t10 and getattr(t10, "value", None):
            ten_y_val = float(t10.value)
            if getattr(t10, "change", None) is not None:
                ten_y_chg = float(t10.change)
        if t2 and getattr(t2, "value", None):
            two_y_val = float(t2.value)
            if getattr(t2, "change", None) is not None:
                two_y_chg = float(t2.change)

    # Build yield line tokens
    if two_y_val is not None:
        bp = int(round((two_y_chg or 0.0) * 100))
        bp_s = f"{bp:+d}bp" if bp != 0 else "flat"
        lines_part1.append(f"2Y {two_y_val:.2f}% ({bp_s})")

    if ten_y_val is not None:
        bp = int(round((ten_y_chg or 0.0) * 100))
        bp_s = f"{bp:+d}bp" if bp != 0 else "flat"
        trigger_note = ""
        if ten_y_val >= 4.45:
            trigger_note = ", above 4.45% trigger"
        elif ten_y_val >= 4.40:
            trigger_note = ", approaching 4.45%"
        lines_part1.append(f"10Y {ten_y_val:.2f}% ({bp_s}{trigger_note})")

    # Yield spread
    if ten_y_val is not None and two_y_val is not None:
        spread_bp = round((ten_y_val - two_y_val) * 100)
        sign = "+" if spread_bp >= 0 else ""
        lines_part1.append(f"10Y-2Y {sign}{spread_bp}bp")

    # USD direction from FX signals
    try:
        from app.briefing.fx_section import build_fx_section_text  # noqa: F401
        fx_pulse = str(getattr(briefing, "fx_pulse_section", "") or "")
        if fx_pulse:
            low = fx_pulse.lower()
            if "stronger" in low or "usd up" in low or "dollar gaining" in low:
                lines_part2.append("USD stronger")
            elif "weaker" in low or "usd down" in low or "dollar slipping" in low:
                lines_part2.append("USD weaker")
    except Exception:
        pass

    # WTI
    wti_val: float | None = None
    wti_chg_pct: float | None = None
    gold_val: float | None = None
    gold_chg_pct: float | None = None

    if setup:
        for q in list(getattr(setup, "macro_quotes", []) or []):
            dname = (getattr(q, "display_name", "") or "").upper()
            sym = (getattr(q, "symbol", "") or "").upper()
            label_key = f"{sym} {dname}"
            price = float(getattr(q, "current_price", 0.0) or 0.0)
            chg_pct = float(getattr(q, "change_percent", 0.0) or 0.0)
            if price <= 0:
                continue
            if ("WTI" in label_key or ("CRUDE" in label_key and "BRENT" not in label_key)) and wti_val is None:
                wti_val = price
                wti_chg_pct = chg_pct
            elif "GOLD" in label_key and gold_val is None:
                gold_val = price
                gold_chg_pct = chg_pct

    # Commodity strip fallback for gold
    if gold_val is None:
        for pt in list(getattr(briefing, "commodity_strip", []) or []):
            name = (getattr(pt, "name", "") or "").upper()
            if "GOLD" in name and getattr(pt, "value", None):
                gold_val = float(pt.value)
                gold_chg_pct = float(pt.change_percent or 0.0) if pt.change_percent is not None else None
                break

    if wti_val is not None and wti_chg_pct is not None:
        wti_label = _wti_level_label(wti_chg_pct)
        lines_part2.append(f"WTI ${wti_val:.2f} ({wti_chg_pct:+.2f}%, {wti_label})")
    elif wti_val is not None:
        lines_part2.append(f"WTI ${wti_val:.2f}")

    if gold_val is not None and gold_chg_pct is not None:
        lines_part2.append(f"Gold ${gold_val:,.0f} ({gold_chg_pct:+.1f}%)")
    elif gold_val is not None:
        lines_part2.append(f"Gold ${gold_val:,.0f}")

    # Count available values
    all_parts = lines_part1 + lines_part2
    if len(all_parts) < 3:
        logger.debug("Rates/macro tape suppressed: only %d values available", len(all_parts))
        return ""

    result_lines = ["RATES & MACRO TAPE"]
    if lines_part1:
        result_lines.append(" | ".join(lines_part1))
    if lines_part2:
        result_lines.append(" | ".join(lines_part2))

    return "\n".join(result_lines)


def _wti_level_label(chg_pct: float) -> str:
    """Return a brief qualitative WTI label."""
    if chg_pct >= 2.0:
        return "elevated"
    if chg_pct >= 0.5:
        return "firm"
    if chg_pct <= -2.0:
        return "under pressure"
    if chg_pct <= -0.5:
        return "easing"
    return "steady"


# ---------------------------------------------------------------------------
# Session range strip chart spec
# ---------------------------------------------------------------------------

def session_range_strip_spec(tape_entries: list[SessionTapeEntry]) -> dict | None:
    """Build a range-strip chart spec for the selected session tape entries.

    Returns None if fewer than 3 entries have full OHLC data.
    """
    full_entries = [e for e in tape_entries if e.data_quality == "full"]
    if len(full_entries) < 3:
        reason = f"Only {len(full_entries)} entries have full OHLC data (minimum 3 required)."
        logger.debug("Session range strip suppressed: %s", reason)
        return None

    series = []
    for entry in full_entries:
        series.append({
            "label": entry.label,
            "low": entry.session_low,
            "high": entry.session_high,
            "open": entry.session_open,
            "close": entry.latest,
            "prior_close": entry.prior_close,
            "colour": _ASSET_CLASS_COLOURS.get(entry.asset_class, "#2563EB"),
            "asset_class": entry.asset_class,
        })

    return {
        "chart_type": "range_strip",
        "chart_key": "session_range_strip",
        "variant": "strip",
        "title": "Session Range Strip",
        "subtitle": "Low-high range with open and close markers",
        "caption": "Shows session range (low to high) with open and close price markers per instrument.",
        "series": series,
        "available": True,
        "reason_if_hidden": "",
        "priority": 6.5,
        "annotations": [],
        "email_dimensions": {"width": 640, "height": 260},
    }
