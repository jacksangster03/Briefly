"""Phase 9.X — MoveContextEngine: deterministic move, range, and level context.

Pure computation module: no I/O, no provider calls, no side effects.
Pass in quote fields and optional price history; receive a MoveContext.

Answers four questions for every market number:
  1. Is this move up or down?
  2. Is the move big relative to normal daily moves?
  3. Is the current level high or low relative to its own recent range?
  4. Is the current print near the top, middle, or bottom of today's range?
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

# ---------------------------------------------------------------------------
# Asset type constants
# ---------------------------------------------------------------------------

ASSET_TYPE_EQUITY_INDEX = "equity_index"
ASSET_TYPE_EQUITY_ETF = "equity_etf"
ASSET_TYPE_STOCK = "stock"
ASSET_TYPE_TREASURY_YIELD = "treasury_yield"
ASSET_TYPE_COMMODITY = "commodity"
ASSET_TYPE_VOLATILITY_INDEX = "volatility_index"
ASSET_TYPE_FX = "fx"

_PRICE_ASSET_TYPES = frozenset({
    ASSET_TYPE_EQUITY_INDEX,
    ASSET_TYPE_EQUITY_ETF,
    ASSET_TYPE_STOCK,
    ASSET_TYPE_COMMODITY,
    ASSET_TYPE_FX,
})

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MoveContext:
    symbol: str
    label: str
    asset_type: str
    level_display: str
    daily_move_display: str
    move_direction: str                   # "up" | "down" | "flat"
    move_context_label: str
    level_context_label: str | None
    move_percentile_1y: float | None
    level_range_percentile_1y: float | None
    day_range_display: str | None
    day_range_position_label: str | None
    final_display: str
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(values: Sequence[float], v: float) -> float:
    """Fraction of |values| that are strictly less than v. Result in [0, 1]."""
    if not values:
        return 0.5
    return sum(1 for x in values if x < v) / len(values)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Static magnitude thresholds
# ---------------------------------------------------------------------------

def _magnitude_label_price(abs_pct: float, is_gain: bool) -> str:
    """Direction-sensitive label from static thresholds for equities/commodities/FX."""
    if abs_pct < 0.30:
        return "Tiny gain" if is_gain else "Tiny decline"
    if abs_pct < 0.80:
        return "Normal gain" if is_gain else "Normal decline"
    if abs_pct < 1.50:
        return "Firm gain" if is_gain else "Firm decline"
    if abs_pct < 3.00:
        return "Strong gain" if is_gain else "Sharp fall"
    return "Extreme gain" if is_gain else "Extreme fall"


def _magnitude_label_price_percentile(pct: float, is_gain: bool) -> str:
    """Direction-sensitive label from 1Y daily-move percentile."""
    if pct < 0.20:
        return "Small gain" if is_gain else "Small decline"
    if pct < 0.40:
        return "Normal gain" if is_gain else "Normal decline"
    if pct < 0.60:
        return "Firm gain" if is_gain else "Firm decline"
    if pct < 0.80:
        return "Strong day" if is_gain else "Weak day"
    return "Strong day, top decile vs 1Y daily moves" if is_gain else "Weak day, bottom decile vs 1Y daily moves"


def _magnitude_label_yield_bp(abs_bp: float, is_rally: bool) -> str:
    """Direction-sensitive label for yield moves in basis points."""
    direction = "Treasury rally" if is_rally else "rates selloff"
    if abs_bp < 3:
        return "Flat"
    if abs_bp < 6:
        return f"Mild {direction}"
    if abs_bp < 11:
        return f"Firm {direction}"
    if abs_bp < 21:
        return f"Sharp {direction}"
    return f"Extreme {direction}"


# ---------------------------------------------------------------------------
# Range / level helpers
# ---------------------------------------------------------------------------

_RANGE_LABELS = [
    (0.20, "near session lows"),
    (0.40, "lower half"),
    (0.60, "mid-range"),
    (0.80, "upper half"),
    (1.01, "near session highs"),
]


def _range_position_label(position: float) -> str:
    """Map a [0, 1] within-range position to a human label."""
    for threshold, label in _RANGE_LABELS:
        if position < threshold:
            return label
    return "near session highs"


def _level_context_label(level_pct: float) -> str | None:
    """Map a [0, 1] 1Y level percentile to a short label (or None if mid-range)."""
    if level_pct < 0.10:
        return "near 1Y lows"
    if level_pct < 0.30:
        return "low vs 1Y range"
    if level_pct < 0.70:
        return None   # mid-range: not worth calling out
    if level_pct < 0.90:
        return "high vs 1Y range"
    return "near 1Y highs"


_SESSION_RANGE_LABEL: dict[str, str] = {
    "morning": "prior session range",
    "europe_midday": "Europe session range",
    "us_pre_open": "pre-market range",
    "us_intraday_risk_check": "session range so far",
    "into_close": "session range so far",
    "closing_wrap": "full session range",
    # weekends & fallback
    "saturday": "prior session range",
    "sunday": "prior session range",
}

_DEFAULT_RANGE_LABEL = "range"


def _session_range_label(session_mode: str) -> str:
    return _SESSION_RANGE_LABEL.get(session_mode, _DEFAULT_RANGE_LABEL)


# ---------------------------------------------------------------------------
# VIX level classification
# ---------------------------------------------------------------------------

def _vix_level_label(level: float) -> str:
    if level < 15:
        return "Calm"
    if level < 20:
        return "Watchful"
    if level < 30:
        return "Stressed"
    return "Panic"


# ---------------------------------------------------------------------------
# Public API — compute_move_context
# ---------------------------------------------------------------------------

def compute_move_context(
    *,
    symbol: str,
    label: str,
    asset_type: str,
    current_price: float,
    change: float,
    change_percent: float,
    day_high: float = 0.0,
    day_low: float = 0.0,
    prev_close: float = 0.0,
    session_mode: str = "morning",
    price_history: list[float] | None = None,
) -> MoveContext:
    """Compute move, range, and level context for a non-yield instrument.

    Args:
        symbol: Ticker, e.g. "SPY".
        label: Human label, e.g. "S&P 500".
        asset_type: One of the ASSET_TYPE_* constants.
        current_price: Current / last traded price.
        change: Absolute price change from prev close.
        change_percent: Percentage change from prev close.
        day_high: Intraday high (0 = unavailable).
        day_low: Intraday low (0 = unavailable).
        prev_close: Previous session close (0 = unavailable).
        session_mode: Session key, e.g. "morning", "closing_wrap".
        price_history: Optional list of 1Y daily closing prices (newest last).
            Used to compute move percentile and level percentile.

    Returns:
        A fully populated MoveContext. Never raises.
    """
    errors: list[str] = []

    # ── Direction ────────────────────────────────────────────────────────────
    abs_pct = abs(float(change_percent))
    is_gain = float(change_percent) >= 0
    move_direction = "flat" if abs_pct < 0.005 else ("up" if is_gain else "down")

    # ── Level display ────────────────────────────────────────────────────────
    if asset_type == ASSET_TYPE_COMMODITY and label.lower() in ("wti", "oil", "brent", "nat gas", "natural gas", "gold"):
        level_display = f"${current_price:,.2f}"
    elif asset_type == ASSET_TYPE_FX:
        level_display = f"{current_price:.4f}"
    else:
        level_display = f"{current_price:,.2f}"

    # ── Move display ─────────────────────────────────────────────────────────
    if move_direction == "flat":
        daily_move_display = "flat"
    else:
        sign = "+" if is_gain else ""
        daily_move_display = f"{sign}{change_percent:.2f}%"

    # ── VIX-specific ─────────────────────────────────────────────────────────
    if asset_type == ASSET_TYPE_VOLATILITY_INDEX:
        vix_level = _vix_level_label(float(current_price))
        vix_dir = "flat" if move_direction == "flat" else ("falling" if not is_gain else "rising")
        move_context_label = f"{vix_level}, {vix_dir}"
    else:
        # ── Magnitude context ────────────────────────────────────────────────
        move_percentile_1y: float | None = None
        if price_history and len(price_history) >= 20:
            try:
                daily_moves = [
                    abs(price_history[i] / price_history[i - 1] - 1) * 100
                    for i in range(1, len(price_history))
                    if price_history[i - 1] > 0
                ]
                if daily_moves:
                    move_percentile_1y = _clamp(_percentile(daily_moves, abs_pct))
                    move_context_label = _magnitude_label_price_percentile(move_percentile_1y, is_gain)
                else:
                    move_context_label = _magnitude_label_price(abs_pct, is_gain)
            except Exception as exc:
                errors.append(f"move_percentile: {exc}")
                move_context_label = _magnitude_label_price(abs_pct, is_gain)
        else:
            move_context_label = _magnitude_label_price(abs_pct, is_gain)
            move_percentile_1y = None

    move_percentile_1y_out = move_percentile_1y if asset_type != ASSET_TYPE_VOLATILITY_INDEX else None

    # ── Level range percentile ────────────────────────────────────────────────
    level_range_percentile_1y: float | None = None
    level_context_label: str | None = None
    if price_history and len(price_history) >= 20:
        try:
            lo = min(price_history)
            hi = max(price_history)
            if hi > lo:
                level_range_percentile_1y = _clamp((float(current_price) - lo) / (hi - lo))
                level_context_label = _level_context_label(level_range_percentile_1y)
        except Exception as exc:
            errors.append(f"level_percentile: {exc}")

    # ── Day range ─────────────────────────────────────────────────────────────
    day_range_display: str | None = None
    day_range_position_label: str | None = None
    effective_prev = float(prev_close) if float(prev_close) > 0 else (float(current_price) - float(change)) if float(change) != 0 else 0.0
    dh = float(day_high)
    dl = float(day_low)

    if dh > 0 and dl > 0 and effective_prev > 0 and dh != dl:
        try:
            low_move_pct = (dl / effective_prev - 1) * 100
            high_move_pct = (dh / effective_prev - 1) * 100
            lo_s = f"{'+' if low_move_pct >= 0 else ''}{low_move_pct:.1f}%"
            hi_s = f"{'+' if high_move_pct >= 0 else ''}{high_move_pct:.1f}%"
            day_range_display = f"{lo_s} to {hi_s}"

            range_span = dh - dl
            price_pos = _clamp((float(current_price) - dl) / range_span)
            day_range_position_label = _range_position_label(price_pos)
        except Exception as exc:
            errors.append(f"day_range: {exc}")

    # ── Build final_display ───────────────────────────────────────────────────
    range_label = _session_range_label(session_mode)
    parts = [f"{label}: {level_display} {daily_move_display}"]
    if day_range_display:
        range_part = f"{range_label} {day_range_display}"
        if day_range_position_label:
            range_part += f", {day_range_position_label}"
        parts.append(f"| {range_part}")

    context_parts: list[str] = []
    if move_context_label:
        context_parts.append(move_context_label)
    if level_context_label:
        context_parts.append(f"still {level_context_label}" if day_range_display else level_context_label)
    if context_parts:
        parts.append(f"| {', '.join(context_parts)}")

    final_display = " ".join(parts)

    return MoveContext(
        symbol=symbol,
        label=label,
        asset_type=asset_type,
        level_display=level_display,
        daily_move_display=daily_move_display,
        move_direction=move_direction,
        move_context_label=move_context_label,
        level_context_label=level_context_label,
        move_percentile_1y=move_percentile_1y_out,
        level_range_percentile_1y=level_range_percentile_1y,
        day_range_display=day_range_display,
        day_range_position_label=day_range_position_label,
        final_display=final_display,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Public API — compute_yield_context
# ---------------------------------------------------------------------------

def compute_yield_context(
    *,
    symbol: str,
    label: str,
    current_yield: float,
    change_yield: float,
    day_high_yield: float = 0.0,
    day_low_yield: float = 0.0,
    prev_yield: float = 0.0,
    session_mode: str = "morning",
    yield_history: list[float] | None = None,
) -> MoveContext:
    """Compute move and range context for a Treasury yield.

    Yields display in basis points (never percentage change of the yield).
    Direction: yield down = Treasury rally; yield up = rates selloff.

    Args:
        symbol: e.g. "US10Y".
        label: e.g. "US 10Y".
        current_yield: Current yield level in percent, e.g. 4.42.
        change_yield: Change in yield in percent, e.g. -0.03 for -3 bp.
        day_high_yield: Intraday yield high in percent (0 = unavailable).
        day_low_yield: Intraday yield low in percent (0 = unavailable).
        prev_yield: Prior session yield close in percent (0 = unavailable).
        session_mode: Session key for range label.
        yield_history: Optional 1Y daily yield closes for percentile.

    Returns:
        A MoveContext. Never raises.
    """
    errors: list[str] = []

    change_bp = float(change_yield) * 100
    abs_bp = abs(change_bp)
    is_rally = change_bp < 0       # yield down = Treasury rally
    move_direction = "flat" if abs_bp < 0.5 else ("down" if is_rally else "up")

    # Yields never show % change — only bp
    level_display = f"{float(current_yield):.2f}%"
    if move_direction == "flat":
        daily_move_display = "flat"
    else:
        sign = "+" if not is_rally else ""
        daily_move_display = f"{sign}{change_bp:+.0f} bp".replace("+-", "-")
        # Tidy: "+3 bp" / "-3 bp"
        bp_int = round(change_bp)
        daily_move_display = f"{bp_int:+d} bp"

    move_context_label = _magnitude_label_yield_bp(abs_bp, is_rally)

    # ── Level percentile (yield level vs 1Y range) ────────────────────────────
    level_range_percentile_1y: float | None = None
    level_context_label: str | None = None
    if yield_history and len(yield_history) >= 20:
        try:
            lo = min(yield_history)
            hi = max(yield_history)
            if hi > lo:
                level_range_percentile_1y = _clamp((float(current_yield) - lo) / (hi - lo))
                level_context_label = _level_context_label(level_range_percentile_1y)
        except Exception as exc:
            errors.append(f"yield_level_percentile: {exc}")

    # ── Move percentile (yield bp change vs 1Y distribution) ─────────────────
    move_percentile_1y: float | None = None
    if yield_history and len(yield_history) >= 20:
        try:
            bp_moves = [
                abs(yield_history[i] - yield_history[i - 1]) * 100
                for i in range(1, len(yield_history))
            ]
            if bp_moves:
                move_percentile_1y = _clamp(_percentile(bp_moves, abs_bp))
        except Exception as exc:
            errors.append(f"yield_move_percentile: {exc}")

    # ── Intraday range (basis points from prior close) ─────────────────────────
    day_range_display: str | None = None
    day_range_position_label: str | None = None
    eff_prev = float(prev_yield) if float(prev_yield) > 0 else (float(current_yield) - float(change_yield)) if float(change_yield) != 0 else 0.0
    dh = float(day_high_yield)
    dl = float(day_low_yield)

    if dh > 0 and dl > 0 and eff_prev > 0 and abs(dh - dl) > 1e-6:
        try:
            low_bp = round((dl - eff_prev) * 100)
            high_bp = round((dh - eff_prev) * 100)
            lo_s = f"{low_bp:+d}"
            hi_s = f"{high_bp:+d}"
            day_range_display = f"{lo_s} to {hi_s} bp"

            span = dh - dl
            # Position: yield DOWN = "higher in price" = lower in yield index
            # So low yield = high bond price = "near session highs" for the BOND
            # But for yield display we track yield position naturally
            pos = _clamp((float(current_yield) - dl) / span)
            day_range_position_label = _range_position_label(pos)
        except Exception as exc:
            errors.append(f"yield_day_range: {exc}")

    # ── Build final_display ───────────────────────────────────────────────────
    range_label = _session_range_label(session_mode)
    parts = [f"{label}: {level_display} {daily_move_display}"]
    if day_range_display:
        range_part = f"{range_label} {day_range_display}"
        if day_range_position_label:
            range_part += f", {day_range_position_label}"
        parts.append(f"| {range_part}")

    context_parts: list[str] = [move_context_label] if move_context_label else []
    if level_context_label:
        context_parts.append(level_context_label)
    if context_parts:
        parts.append(f"| {', '.join(context_parts)}")

    final_display = " ".join(parts)

    return MoveContext(
        symbol=symbol,
        label=label,
        asset_type=ASSET_TYPE_TREASURY_YIELD,
        level_display=level_display,
        daily_move_display=daily_move_display,
        move_direction=move_direction,
        move_context_label=move_context_label,
        level_context_label=level_context_label,
        move_percentile_1y=move_percentile_1y,
        level_range_percentile_1y=level_range_percentile_1y,
        day_range_display=day_range_display,
        day_range_position_label=day_range_position_label,
        final_display=final_display,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Convenience: from QuoteData
# ---------------------------------------------------------------------------

def move_context_from_quote(
    quote,
    *,
    asset_type: str,
    label: str | None = None,
    session_mode: str = "morning",
    price_history: list[float] | None = None,
) -> MoveContext:
    """Build a MoveContext from a QuoteData object.

    Falls back gracefully if any QuoteData field is missing.
    Never raises.
    """
    try:
        return compute_move_context(
            symbol=str(quote.symbol or ""),
            label=str(label or quote.display_name or quote.symbol or ""),
            asset_type=asset_type,
            current_price=float(quote.current_price or 0.0),
            change=float(quote.change or 0.0),
            change_percent=float(quote.change_percent or 0.0),
            day_high=float(getattr(quote, "high", 0.0) or 0.0),
            day_low=float(getattr(quote, "low", 0.0) or 0.0),
            prev_close=float(getattr(quote, "previous_close", 0.0) or 0.0),
            session_mode=session_mode,
            price_history=price_history,
        )
    except Exception:
        # Ultimate fallback — return minimal context
        sym = str(getattr(quote, "symbol", "") or "")
        lbl = str(label or getattr(quote, "display_name", "") or sym)
        pct = float(getattr(quote, "change_percent", 0.0) or 0.0)
        sign = "+" if pct >= 0 else ""
        return MoveContext(
            symbol=sym,
            label=lbl,
            asset_type=asset_type,
            level_display="—",
            daily_move_display=f"{sign}{pct:.2f}%",
            move_direction="up" if pct > 0 else ("down" if pct < 0 else "flat"),
            move_context_label="",
            level_context_label=None,
            move_percentile_1y=None,
            level_range_percentile_1y=None,
            day_range_display=None,
            day_range_position_label=None,
            final_display=lbl,
            errors=["fallback: exception in move_context_from_quote"],
        )


# ---------------------------------------------------------------------------
# Compact formatters for Telegram and email rows
# ---------------------------------------------------------------------------

def format_move_context_line(ctx: MoveContext, *, include_range: bool = True, include_level: bool = True) -> str:
    """One-line Telegram-ready summary for a non-yield instrument.

    Example:
        Russell 2000: 2,845.00 +1.75% | range +0.4% to +1.9%, near session highs | Strong day
    """
    parts = [f"{ctx.label}: {ctx.level_display} {ctx.daily_move_display}"]
    if include_range and ctx.day_range_display:
        rng = ctx.day_range_display
        if ctx.day_range_position_label:
            rng += f", {ctx.day_range_position_label}"
        parts.append(f"| {rng}")
    context_parts: list[str] = []
    if ctx.move_context_label:
        context_parts.append(ctx.move_context_label)
    if include_level and ctx.level_context_label:
        context_parts.append(f"still {ctx.level_context_label}" if ctx.day_range_display else ctx.level_context_label)
    if context_parts:
        parts.append(f"| {', '.join(context_parts)}")
    return " ".join(parts)


def format_watchlist_move_label(ctx: MoveContext) -> str:
    """Ultra-compact label for watchlist rows: 'AMD +3.53% Strong, near highs'.

    Suppresses the label entirely for Tiny moves.
    """
    parts = [ctx.daily_move_display]
    label = ctx.move_context_label or ""
    # Strip verbose suffixes for compact display
    for strip in (", top decile vs 1Y daily moves", ", bottom decile vs 1Y daily moves"):
        label = label.replace(strip, "")
    if label and "Tiny" not in label:
        parts.append(label)
    if ctx.day_range_position_label in ("near session highs", "near session lows"):
        short = "near highs" if "highs" in ctx.day_range_position_label else "near lows"
        parts.append(short)
    return " ".join(p for p in parts if p)


def format_yield_context_line(ctx: MoveContext) -> str:
    """One-line Telegram-ready summary for a Treasury yield."""
    parts = [f"{ctx.label}: {ctx.level_display} {ctx.daily_move_display}"]
    if ctx.day_range_display:
        rng = ctx.day_range_display
        if ctx.day_range_position_label:
            rng += f", {ctx.day_range_position_label}"
        parts.append(f"| {rng}")
    context_parts: list[str] = []
    if ctx.move_context_label and ctx.move_context_label != "Flat":
        context_parts.append(ctx.move_context_label)
    if ctx.level_context_label:
        context_parts.append(ctx.level_context_label)
    if context_parts:
        parts.append(f"| {', '.join(context_parts)}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Session-to-session "what changed" helper
# ---------------------------------------------------------------------------

def format_session_change(
    label: str,
    *,
    asset_type: str,
    current: float,
    prev_session: float,
    is_yield: bool = False,
) -> str:
    """Describe the change from a prior session in plain English.

    Suppresses meaningless ±0.00% outputs. For yields, uses basis points.
    """
    if prev_session <= 0 or current <= 0:
        return ""

    if is_yield or asset_type == ASSET_TYPE_TREASURY_YIELD:
        bp = round((current - prev_session) * 100)
        if abs(bp) < 1:
            return f"{label}: flat vs prior session"
        return f"{label}: {bp:+d} bp vs prior session"

    pct = (current / prev_session - 1) * 100
    abs_pct = abs(pct)
    if abs_pct < 0.05:
        return f"{label}: flat vs prior session"
    if abs_pct < 0.30:
        direction = "modestly higher" if pct > 0 else "modestly lower"
        return f"{label}: {direction} vs prior session"
    if abs_pct < 1.50:
        direction = "higher" if pct > 0 else "lower"
        return f"{label}: {direction} vs prior session ({pct:+.2f}%)"
    direction = "sharply higher" if pct > 0 else "sharply lower"
    return f"{label}: {direction} vs prior session ({pct:+.2f}%)"
