"""Deterministic session freshness classification for market values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.briefing.session_routing import session_window_for_key
from app.cadence.exchange_calendar import exchange_for_symbol, is_exchange_closed
from app.schemas.events import QuoteData


FRESHNESS_LIVE = "live"
FRESHNESS_NEAR_REAL_TIME = "near_real_time"
FRESHNESS_DELAYED = "delayed"
FRESHNESS_PRIOR_CLOSE = "prior_close"
FRESHNESS_STALE = "stale"
FRESHNESS_CARRIED = "carried_forward"
FRESHNESS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FreshnessMeta:
    symbol: str
    label: str
    value_timestamp: datetime | None
    generated_at: datetime
    session_key: str
    market_status: str
    freshness_state: str
    freshness_label: str
    should_show_as_live: bool
    display_prefix: str
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "label": self.label,
            "value_timestamp": self.value_timestamp.isoformat() if self.value_timestamp else None,
            "generated_at": self.generated_at.isoformat(),
            "session_key": self.session_key,
            "market_status": self.market_status,
            "freshness_state": self.freshness_state,
            "freshness_label": self.freshness_label,
            "should_show_as_live": self.should_show_as_live,
            "display_prefix": self.display_prefix,
            "warning": self.warning,
        }


def classify_quote_freshness(
    *,
    quote: QuoteData,
    generated_at: datetime,
    session_key: str,
    timezone_name: str,
    carried_forward: bool = False,
) -> FreshnessMeta:
    tz = _safe_tz(timezone_name)
    now_local = _as_local(generated_at, tz)
    ts_local = _as_local(quote.timestamp, tz) if quote.timestamp else None
    label = (quote.display_name or quote.symbol or "").strip() or quote.symbol
    symbol = (quote.symbol or "").strip().upper()
    market_status = _market_status_for_quote(
        quote=quote,
        generated_at=generated_at,
        session_key=session_key,
        timezone_name=timezone_name,
    )

    if ts_local is None:
        return FreshnessMeta(
            symbol=symbol,
            label=label,
            value_timestamp=None,
            generated_at=generated_at,
            session_key=session_key,
            market_status=market_status,
            freshness_state=FRESHNESS_UNAVAILABLE,
            freshness_label="timestamp unavailable",
            should_show_as_live=False,
            display_prefix="unavailable",
            warning="missing_timestamp",
        )

    age = max(timedelta(0), now_local - ts_local)
    is_equity_like = _is_equity_like(quote)
    is_us_equity_like = _is_us_equity_like(quote)
    is_open_session = market_status in {"open", "pre_market", "post_market"}

    # Explicit carried-forward flag wins; used by delta engine for repeated context.
    if carried_forward:
        return FreshnessMeta(
            symbol=symbol,
            label=label,
            value_timestamp=ts_local,
            generated_at=generated_at,
            session_key=session_key,
            market_status=market_status,
            freshness_state=FRESHNESS_CARRIED,
            freshness_label=f"carried forward, {ts_local.strftime('%H:%M %Z')}",
            should_show_as_live=False,
            display_prefix="carried forward",
        )

    # Before US open, US equities/watchlist are frequently prior close.
    if is_us_equity_like and session_key in {"morning", "europe_midday", "us_pre_open"}:
        if ts_local.date() < now_local.date() or age >= timedelta(hours=8):
            return FreshnessMeta(
                symbol=symbol,
                label=label,
                value_timestamp=ts_local,
                generated_at=generated_at,
                session_key=session_key,
                market_status=market_status,
                freshness_state=FRESHNESS_PRIOR_CLOSE,
                freshness_label=f"prior close, {ts_local.strftime('%a %d %b %H:%M %Z')}",
                should_show_as_live=False,
                display_prefix="prior close",
            )

    if is_open_session:
        if age <= timedelta(minutes=15):
            state = FRESHNESS_NEAR_REAL_TIME
            label_text = f"near-real-time, {ts_local.strftime('%H:%M %Z')}"
            display_prefix = "live"
            live = True
        elif age <= timedelta(minutes=60):
            state = FRESHNESS_DELAYED
            label_text = f"delayed, {ts_local.strftime('%H:%M %Z')}"
            display_prefix = "delayed"
            live = False
        else:
            state = FRESHNESS_STALE
            label_text = f"stale, {ts_local.strftime('%H:%M %Z')}"
            display_prefix = "stale"
            live = False
    else:
        # Closed markets: prior close is expected for equities/indices.
        if is_equity_like:
            state = FRESHNESS_PRIOR_CLOSE
            label_text = f"prior close, {ts_local.strftime('%a %d %b %H:%M %Z')}"
            display_prefix = "prior close"
            live = False
        else:
            # Commodities/FX/rates can continue updating outside cash session.
            if age <= timedelta(minutes=15):
                state = FRESHNESS_NEAR_REAL_TIME
                label_text = f"near-real-time, {ts_local.strftime('%H:%M %Z')}"
                display_prefix = "live"
                live = True
            elif age <= timedelta(minutes=60):
                state = FRESHNESS_DELAYED
                label_text = f"delayed, {ts_local.strftime('%H:%M %Z')}"
                display_prefix = "delayed"
                live = False
            else:
                state = FRESHNESS_STALE
                label_text = f"stale, {ts_local.strftime('%H:%M %Z')}"
                display_prefix = "stale"
                live = False

    return FreshnessMeta(
        symbol=symbol,
        label=label,
        value_timestamp=ts_local,
        generated_at=generated_at,
        session_key=session_key,
        market_status=market_status,
        freshness_state=state,
        freshness_label=label_text,
        should_show_as_live=live,
        display_prefix=display_prefix,
    )


def build_freshness_map(
    *,
    quotes: list[QuoteData],
    generated_at: datetime,
    session_key: str,
    timezone_name: str,
    carried_forward_symbols: set[str] | None = None,
) -> dict[str, dict]:
    carried = {sym.upper() for sym in (carried_forward_symbols or set())}
    out: dict[str, dict] = {}
    for quote in quotes:
        symbol = (quote.symbol or "").upper()
        meta = classify_quote_freshness(
            quote=quote,
            generated_at=generated_at,
            session_key=session_key,
            timezone_name=timezone_name,
            carried_forward=symbol in carried,
        )
        key = _freshness_key(quote)
        out[key] = meta.to_dict()
        if symbol:
            out.setdefault(symbol, meta.to_dict())
    return out


def build_data_basis_lines(
    *,
    session_key: str,
    generated_at: datetime,
    timezone_name: str,
    index_quotes: list[QuoteData],
    macro_quotes: list[QuoteData],
    watchlist_quotes: list[QuoteData],
) -> list[str]:
    tz = _safe_tz(timezone_name)
    stamp = _as_local(generated_at, tz).strftime("%H:%M %Z")

    us_index_quotes = [q for q in index_quotes if _is_us_equity_like(q)]
    us_watch_quotes = [q for q in watchlist_quotes if _is_us_equity_like(q)]
    us_quotes = us_index_quotes + us_watch_quotes
    eu_quotes = [q for q in index_quotes if _is_europe_equity_like(q)]
    commodity_quotes = [q for q in macro_quotes if _is_commodity_like(q)]

    us_basis = _basis_for_group(us_quotes, generated_at, session_key, timezone_name, fallback="unavailable")
    eu_basis = _basis_for_group(eu_quotes, generated_at, session_key, timezone_name, fallback="unavailable")
    com_basis = _basis_for_group(commodity_quotes, generated_at, session_key, timezone_name, fallback="unavailable")

    lines = [
        f"US equities: {us_basis}",
        f"Europe equities: {eu_basis}",
        f"Commodities: {com_basis}",
        f"News: live scan, {stamp}",
        "Macro/FRED: latest official release",
    ]
    if session_key == "us_pre_open":
        us_cash_basis = _basis_for_group(
            us_index_quotes, generated_at, session_key, timezone_name, fallback="unavailable"
        )
        us_proxy_basis = _basis_for_group(
            us_watch_quotes, generated_at, session_key, timezone_name, fallback="unavailable"
        )
        lines[0] = (
            f"US cash indices: {us_cash_basis} (pre-open context); "
            f"US watchlist/pre-market proxies: {us_proxy_basis}"
        )

    if session_key in {"europe_midday", "us_pre_open"} and eu_basis.startswith(("prior close", "delayed", "stale")):
        lines.append("Europe cash is open but provider quotes are prior close/delayed.")

    # VIX availability note for intraday risk reads.
    if session_key in {"us_intraday_risk", "into_close"}:
        vix_quote = next(
            (
                q for q in index_quotes + macro_quotes
                if "VIX" in f"{q.display_name} {q.symbol}".upper()
            ),
            None,
        )
        if vix_quote is None:
            lines.append("VIX: unavailable (provider path did not return a live quote this cycle).")
        else:
            vix_meta = classify_quote_freshness(
                quote=vix_quote,
                generated_at=generated_at,
                session_key=session_key,
                timezone_name=timezone_name,
            )
            lines.append(f"VIX: {vix_meta.display_prefix}, {vix_meta.freshness_label}.")

    # Brent stale/unchanged-provider note when WTI is updating but Brent is not.
    wti_quote = next(
        (q for q in macro_quotes if "WTI" in f"{q.display_name} {q.symbol}".upper() or "CRUDE" in f"{q.display_name} {q.symbol}".upper()),
        None,
    )
    brent_quote = next(
        (q for q in macro_quotes if "BRENT" in f"{q.display_name} {q.symbol}".upper()),
        None,
    )
    if wti_quote is not None and brent_quote is not None:
        wti_state = classify_quote_freshness(
            quote=wti_quote,
            generated_at=generated_at,
            session_key=session_key,
            timezone_name=timezone_name,
        ).freshness_state
        brent_meta = classify_quote_freshness(
            quote=brent_quote,
            generated_at=generated_at,
            session_key=session_key,
            timezone_name=timezone_name,
        )
        if wti_state in {FRESHNESS_NEAR_REAL_TIME, FRESHNESS_LIVE} and brent_meta.freshness_state in {
            FRESHNESS_STALE,
            FRESHNESS_PRIOR_CLOSE,
            FRESHNESS_DELAYED,
        }:
            lines.append(
                f"Brent is {brent_meta.display_prefix}; treating Brent as stale/prior-provider context."
            )
    return lines


def _basis_for_group(
    quotes: list[QuoteData],
    generated_at: datetime,
    session_key: str,
    timezone_name: str,
    *,
    fallback: str,
) -> str:
    if not quotes:
        return fallback
    states = [
        classify_quote_freshness(
            quote=q,
            generated_at=generated_at,
            session_key=session_key,
            timezone_name=timezone_name,
        )
        for q in quotes
    ]
    priority = [
        FRESHNESS_NEAR_REAL_TIME,
        FRESHNESS_LIVE,
        FRESHNESS_DELAYED,
        FRESHNESS_PRIOR_CLOSE,
        FRESHNESS_STALE,
        FRESHNESS_UNAVAILABLE,
    ]
    state = next((p for p in priority if any(s.freshness_state == p for s in states)), FRESHNESS_UNAVAILABLE)
    if state == FRESHNESS_NEAR_REAL_TIME:
        return f"near-real-time, {_latest_stamp(states)}"
    if state == FRESHNESS_LIVE:
        return f"live, {_latest_stamp(states)}"
    if state == FRESHNESS_DELAYED:
        return f"delayed, {_latest_stamp(states)}"
    if state == FRESHNESS_PRIOR_CLOSE:
        return f"prior close, {_latest_stamp(states)}"
    if state == FRESHNESS_STALE:
        return f"stale, {_latest_stamp(states)}"
    return fallback


def _latest_stamp(states: list[FreshnessMeta]) -> str:
    stamps = [s.value_timestamp for s in states if s.value_timestamp is not None]
    if not stamps:
        return "timestamp unavailable"
    latest = max(stamps)
    return latest.strftime("%a %d %b %H:%M %Z")


def _market_status_for_quote(
    *,
    quote: QuoteData,
    generated_at: datetime,
    session_key: str,
    timezone_name: str,
) -> str:
    ex = exchange_for_symbol(quote.symbol)
    if ex:
        local_date = _as_local(generated_at, _safe_tz(timezone_name)).date()
        closed, _ = is_exchange_closed(ex, local_date)
        if closed:
            return "closed"
    session = session_window_for_key(session_key)
    if session.key == "us_pre_open":
        return "pre_market"
    if session.key in {"us_intraday_risk", "into_close"}:
        return "open"
    if session.key == "closing_wrap":
        return "post_market"
    return "unknown"


def _is_us_equity_like(quote: QuoteData) -> bool:
    text = f"{quote.symbol} {quote.display_name}".upper()
    if any(tok in text for tok in ("S&P", "NASDAQ", "RUSSELL", "DOW", "SPX", "COMP", "^GSPC", "^IXIC", "^DJI")):
        return True
    symbol = (quote.symbol or "").upper().strip()
    # Conservative fallback for single-name US equities in watchlists (e.g. AMD, NVDA).
    if symbol and symbol.isalpha() and 1 <= len(symbol) <= 5 and not _is_commodity_like(quote):
        return True
    return False


def _is_europe_equity_like(quote: QuoteData) -> bool:
    text = f"{quote.symbol} {quote.display_name}".upper()
    return any(tok in text for tok in ("STOXX", "DAX", "CAC", "IBEX", "FTSE"))


def _is_equity_like(quote: QuoteData) -> bool:
    return _is_us_equity_like(quote) or _is_europe_equity_like(quote)


def _is_commodity_like(quote: QuoteData) -> bool:
    text = f"{quote.symbol} {quote.display_name}".upper()
    return any(tok in text for tok in ("WTI", "BRENT", "CRUDE", "GOLD", "SILVER", "NG", "NAT GAS"))


def _freshness_key(quote: QuoteData) -> str:
    sym = (quote.symbol or "").upper().strip()
    name = (quote.display_name or "").upper().strip()
    return sym or name


def _safe_tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("UTC")


def _as_local(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt.astimezone(tz)
