"""FX & Dollar Pulse section builder for session briefings.

Determines whether the FX block should appear in a given session,
then builds the appropriate text block (full, short, or one-liner).
All logic is deterministic: no forecasts, no LLM calls.
"""

from __future__ import annotations

from app.fx.panel import FXQuote
from app.fx.signals import FXSignals
from app.logger import get_logger

logger = get_logger("fx.section")

# Session keys used throughout the briefing engine
_SESSION_MORNING = "morning"
_SESSION_EUROPE_MIDDAY = "europe_midday"
_SESSION_US_PRE_OPEN = "us_pre_open"
_SESSION_US_INTRADAY = "us_intraday_risk"
_SESSION_INTO_CLOSE = "into_close"
_SESSION_CLOSING_WRAP = "closing_wrap"
_SESSION_WEEKEND_SAT = "saturday_weekend_briefing"
_SESSION_WEEKEND_SUN = "sunday_weekend_watch"

# Materiality thresholds for EUR/USD or EUR/GBP significance
_MIDDAY_EUR_THRESHOLD = 0.3   # abs % to trigger midday block
_PREOPEN_USD_THRESHOLD = 0.3  # abs % to trigger pre-open block


def _quote_for(panel: list[FXQuote], *label_fragments: str) -> FXQuote | None:
    for fragment in label_fragments:
        frag_lower = fragment.lower()
        for q in panel:
            if frag_lower in q.instrument.label.lower():
                return q
    return None


def _fmt(value: float | None, decimals: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def _fmt_chg(change_pct: float | None, *, for_compact: bool = False) -> str:
    """Format a percentage change.

    Parameters
    ----------
    change_pct:
        The percentage change value. None means unavailable.
    for_compact:
        When True, returns an empty string for None (compact output omits
        the change rather than displaying "(n/a)"). When False (default),
        returns "n/a" for display in full/expert contexts.
    """
    if change_pct is None:
        return "" if for_compact else "n/a"
    sign = "+" if change_pct >= 0 else ""
    return f"{sign}{change_pct:.2f}%"


def should_include_fx(
    signals: FXSignals,
    session: str,
    profile: dict,
) -> bool:
    """Determine whether the FX block should be included for this session.

    Parameters
    ----------
    signals:
        FXSignals from build_fx_signals().
    session:
        Session key string, e.g. "morning", "europe_midday".
    profile:
        User profile dict.

    Returns
    -------
    bool
        True if the FX block should be included.
    """
    session = (session or "morning").lower().strip()
    materiality = signals.fx_materiality
    score = signals.materiality_score

    if session == _SESSION_MORNING:
        return materiality in ("medium", "high")

    if session == _SESSION_EUROPE_MIDDAY:
        # Include if EUR/USD or EUR/GBP move is material
        return score >= 2  # at least EUR/USD or EUR/GBP triggered

    if session == _SESSION_US_PRE_OPEN:
        # Include if USD, USD/JPY or USD/CNH is material
        return score >= 2

    if session == _SESSION_US_INTRADAY:
        # Only high materiality
        return materiality == "high"

    if session == _SESSION_INTO_CLOSE:
        # Only high materiality (one-liner preferred)
        return materiality == "high"

    if session == _SESSION_CLOSING_WRAP:
        return materiality in ("medium", "high")

    if session in (_SESSION_WEEKEND_SAT, _SESSION_WEEKEND_SUN):
        return materiality in ("medium", "high")

    # Default: include only if high
    return materiality == "high"


def _portfolio_lens_text(signals: FXSignals) -> str:
    """Build a deterministic portfolio lens statement."""
    parts: list[str] = []

    if signals.usd_pressure == "stronger":
        parts.append(
            "Stronger USD can pressure gold, EM risk and non-US earnings translation."
        )
    elif signals.usd_pressure == "weaker":
        parts.append(
            "Weaker USD supports EM, gold and commodity-linked assets."
        )

    if signals.yen_risk_signal == "risk_off":
        parts.append(
            "Yen strengthening may signal risk-off positioning."
        )

    if signals.china_fx_stress == "active":
        parts.append(
            "Yuan weakness signals China growth/trade caution."
        )

    if not parts:
        return "FX moves were modest; no strong directional signal."

    return " ".join(parts)


def _format_full_block(
    panel: list[FXQuote],
    signals: FXSignals,
    profile: dict,
) -> str:
    """Full FX block for morning and closing wrap sessions."""
    usd_label = signals.usd_pressure if signals.usd_pressure != "unavailable" else "data pending"

    eur_usd = _quote_for(panel, "EUR/USD")
    eur_gbp = _quote_for(panel, "EUR/GBP")
    usd_jpy = _quote_for(panel, "USD/JPY")

    eur_usd_str = ""
    if eur_usd and eur_usd.value is not None:
        chg = _fmt_chg(eur_usd.daily_change_pct, for_compact=True)
        eur_usd_str = f"EUR/USD {_fmt(eur_usd.value, 4)} ({chg})" if chg else f"EUR/USD {_fmt(eur_usd.value, 4)}"

    eur_gbp_str = ""
    if eur_gbp and eur_gbp.value is not None:
        chg = _fmt_chg(eur_gbp.daily_change_pct, for_compact=True)
        eur_gbp_str = f"EUR/GBP {_fmt(eur_gbp.value, 4)} ({chg})" if chg else f"EUR/GBP {_fmt(eur_gbp.value, 4)}"

    usd_jpy_str = ""
    if usd_jpy and usd_jpy.value is not None:
        chg = _fmt_chg(usd_jpy.daily_change_pct, for_compact=True)
        usd_jpy_str = f"USD/JPY {_fmt(usd_jpy.value, 2)} ({chg})" if chg else f"USD/JPY {_fmt(usd_jpy.value, 2)}"

    rate_parts = [s for s in [eur_usd_str, eur_gbp_str, usd_jpy_str] if s]
    rates_line = ", ".join(rate_parts) if rate_parts else "no rate data available"

    lens = _portfolio_lens_text(signals)
    missing_note = ""
    if signals.missing:
        missing_note = f"\nData unavailable: {', '.join(signals.missing)}."

    lines = [
        "FX & DOLLAR PULSE",
        f"USD {usd_label}; {rates_line}.{missing_note}",
        f"Portfolio lens: {lens}",
        "Deterministic signal, not a forecast.",
    ]
    return "\n".join(lines)


def _format_short_block(
    panel: list[FXQuote],
    signals: FXSignals,
    session: str,
) -> str:
    """Short FX block for midday and pre-open sessions."""
    usd_label = signals.usd_pressure if signals.usd_pressure != "unavailable" else "data pending"

    if session == _SESSION_EUROPE_MIDDAY:
        eur_usd = _quote_for(panel, "EUR/USD")
        if eur_usd and eur_usd.value is not None:
            chg = _fmt_chg(eur_usd.daily_change_pct, for_compact=True)
            rate_part = f"EUR/USD {_fmt(eur_usd.value, 4)} ({chg})" if chg else f"EUR/USD {_fmt(eur_usd.value, 4)}"
            return f"FX: USD {usd_label}; {rate_part}."
        return f"FX: USD {usd_label}; EUR/USD data unavailable."

    if session == _SESSION_US_PRE_OPEN:
        usd_jpy = _quote_for(panel, "USD/JPY")
        usd_cnh = _quote_for(panel, "USD/CNH", "USD/CNY")
        parts: list[str] = [f"USD {usd_label}"]
        if usd_jpy and usd_jpy.value is not None:
            chg = _fmt_chg(usd_jpy.daily_change_pct, for_compact=True)
            parts.append(f"USD/JPY {_fmt(usd_jpy.value, 2)} ({chg})" if chg else f"USD/JPY {_fmt(usd_jpy.value, 2)}")
        if usd_cnh and usd_cnh.value is not None:
            chg = _fmt_chg(usd_cnh.daily_change_pct, for_compact=True)
            parts.append(f"{usd_cnh.instrument.label} {_fmt(usd_cnh.value, 4)} ({chg})" if chg else f"{usd_cnh.instrument.label} {_fmt(usd_cnh.value, 4)}")
        return "FX: " + "; ".join(parts) + "."

    # Generic short
    eur_usd = _quote_for(panel, "EUR/USD")
    if eur_usd and eur_usd.value is not None:
        chg = _fmt_chg(eur_usd.daily_change_pct, for_compact=True)
        rate_part = f"EUR/USD {_fmt(eur_usd.value, 4)} ({chg})" if chg else f"EUR/USD {_fmt(eur_usd.value, 4)}"
        return f"FX: USD {usd_label}; {rate_part}."
    return f"FX: USD {usd_label}."


def _format_one_liner(
    panel: list[FXQuote],
    signals: FXSignals,
) -> str:
    """Minimal one-liner for intraday and into-close sessions."""
    parts: list[str] = []
    for frag in ["EUR/USD", "USD/JPY"]:
        q = _quote_for(panel, frag)
        if q and q.value is not None and q.daily_change_pct is not None:
            parts.append(f"{q.instrument.label} {_fmt_chg(q.daily_change_pct)}")
    if parts:
        return "FX: " + ", ".join(parts) + "."
    return "FX: data unavailable this session."


def build_fx_section_text(
    panel: list[FXQuote],
    signals: FXSignals,
    profile: dict,
    session: str,
) -> str:
    """Build the FX section text appropriate for the given session.

    Parameters
    ----------
    panel:
        List of FXQuote objects from fetch_fx_panel().
    signals:
        FXSignals from build_fx_signals().
    profile:
        User profile dict.
    session:
        Session key string.

    Returns
    -------
    str
        FX section text, or empty string if FX data is entirely unavailable.
    """
    session = (session or "morning").lower().strip()

    # If all data is missing, return a graceful unavailable message
    available = [q for q in panel if q.status == "ok"]
    if not available:
        return "FX data unavailable for this session."

    if session in (_SESSION_MORNING, _SESSION_CLOSING_WRAP, _SESSION_WEEKEND_SAT, _SESSION_WEEKEND_SUN):
        return _format_full_block(panel, signals, profile)

    if session in (_SESSION_EUROPE_MIDDAY, _SESSION_US_PRE_OPEN):
        return _format_short_block(panel, signals, session)

    if session in (_SESSION_US_INTRADAY, _SESSION_INTO_CLOSE):
        return _format_one_liner(panel, signals)

    # Default: full block
    return _format_full_block(panel, signals, profile)
