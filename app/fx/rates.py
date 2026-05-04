"""FX rate fetch via yfinance + FXRate DB cache management."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db.models import FXRate
from app.db.session import get_session
from app.logger import get_logger

logger = get_logger("fx.rates")

# Exchange suffix to home currency mapping
_SUFFIX_CURRENCY: dict[str, str] = {
    ".L": "GBP",
    ".PA": "EUR",
    ".AS": "EUR",
    ".DE": "EUR",
    ".F": "EUR",
    ".MI": "EUR",
    ".MC": "EUR",
    ".SW": "CHF",
    ".TO": "CAD",
    ".V": "CAD",
    ".AX": "AUD",
    ".T": "JPY",
    ".HK": "HKD",
    ".SS": "CNY",
    ".SZ": "CNY",
}

_ALL_CURRENCIES = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "JPY", "HKD", "CNY"]


def infer_currency(symbol: str) -> str:
    """Infer listing currency from ticker suffix. Defaults to USD."""
    sym_upper = symbol.upper()
    for suffix, currency in _SUFFIX_CURRENCY.items():
        if sym_upper.endswith(suffix.upper()):
            return currency
    return "USD"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _yf_pair(from_ccy: str, to_ccy: str) -> str:
    return f"{from_ccy}{to_ccy}=X"


def fetch_fx_rates(
    currency_pairs: list[tuple[str, str]],
    lookback_days: int = 30,
    force_refresh: bool = False,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Fetch historical FX rates for given (from, to) currency pairs.
    Caches daily close in FXRate table. Returns {(from, to): [{date, rate}, ...]}.
    """
    today = _utcnow().date()
    results: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for from_ccy, to_ccy in currency_pairs:
        if from_ccy == to_ccy:
            results[(from_ccy, to_ccy)] = [{"date": str(today), "rate": 1.0}]
            continue

        if not force_refresh:
            with get_session() as session:
                cached = (
                    session.query(FXRate)
                    .filter(
                        FXRate.from_currency == from_ccy,
                        FXRate.to_currency == to_ccy,
                        FXRate.as_of_date == today,
                    )
                    .first()
                )
            if cached:
                results[(from_ccy, to_ccy)] = [{"date": str(today), "rate": cached.rate}]
                continue

        try:
            import yfinance as yf
            ticker_sym = _yf_pair(from_ccy, to_ccy)
            hist = yf.download(
                ticker_sym,
                period=f"{lookback_days}d",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
            if hist.empty:
                results[(from_ccy, to_ccy)] = []
                continue

            rows = []
            for ts, row_data in hist.iterrows():
                d = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                close = float(row_data["Close"].iloc[0] if hasattr(row_data["Close"], "iloc") else row_data["Close"])
                if close > 0:
                    rows.append({"date": str(d), "rate": round(close, 6)})
                    _cache_rate(from_ccy, to_ccy, d, close)

            results[(from_ccy, to_ccy)] = rows

        except Exception as exc:
            logger.debug("FX fetch failed %s/%s: %s", from_ccy, to_ccy, exc)
            results[(from_ccy, to_ccy)] = []

    return results


def get_latest_rate(from_ccy: str, to_ccy: str, force_refresh: bool = False) -> float | None:
    """Return the most recent cached or fetched rate for a currency pair."""
    if from_ccy == to_ccy:
        return 1.0
    today = _utcnow().date()

    if not force_refresh:
        with get_session() as session:
            cached = (
                session.query(FXRate)
                .filter(FXRate.from_currency == from_ccy, FXRate.to_currency == to_ccy)
                .order_by(FXRate.as_of_date.desc())
                .first()
            )
        if cached:
            return cached.rate

    pairs = fetch_fx_rates([(from_ccy, to_ccy)], lookback_days=5, force_refresh=force_refresh)
    rows = pairs.get((from_ccy, to_ccy), [])
    if rows:
        return rows[-1]["rate"]
    return None


def _cache_rate(from_ccy: str, to_ccy: str, as_of: date, rate: float) -> None:
    try:
        with get_session() as session:
            existing = (
                session.query(FXRate)
                .filter(
                    FXRate.from_currency == from_ccy,
                    FXRate.to_currency == to_ccy,
                    FXRate.as_of_date == as_of,
                )
                .first()
            )
            if existing:
                existing.rate = rate
            else:
                session.add(FXRate(
                    from_currency=from_ccy,
                    to_currency=to_ccy,
                    rate=rate,
                    as_of_date=as_of,
                    created_at=_utcnow(),
                ))
            session.commit()
    except Exception as exc:
        logger.debug("FX cache write failed: %s", exc)
