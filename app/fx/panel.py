"""FX data panel: fetches quotes for an FX basket and returns FXQuote objects.

All failures are handled gracefully: individual instrument failures produce
an FXQuote with status="unavailable" rather than raising exceptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.fx.basket import FXInstrument
from app.logger import get_logger

logger = get_logger("fx.panel")


@dataclass
class FXQuote:
    """Quote data for a single FX instrument."""

    instrument: FXInstrument
    value: float | None
    daily_change_pct: float | None
    change_5d_pct: float | None
    source: str
    freshness: str   # "live" | "delayed" | "prior_close" | "stale" | "unavailable"
    status: str      # "ok" | "stale" | "unavailable"
    fetched_at: datetime | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_fred_instrument(instrument: FXInstrument, settings: dict) -> FXQuote:
    """Fetch a FRED-sourced instrument (e.g. DTWEXBGS trade-weighted USD)."""
    fred_api_key = settings.get("fred_api_key", "")
    if not fred_api_key:
        logger.warning("FX panel: FRED API key not configured; skipping %s", instrument.label)
        return FXQuote(
            instrument=instrument,
            value=None,
            daily_change_pct=None,
            change_5d_pct=None,
            source="fred",
            freshness="unavailable",
            status="unavailable",
            fetched_at=None,
        )

    try:
        import requests
        from datetime import timedelta, date

        series_id = instrument.symbol
        today = _utcnow().date()
        # Request 10 business days of data to compute 5D change reliably
        observation_start = (today - timedelta(days=20)).isoformat()
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": fred_api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
            "observation_start": observation_start,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        observations = [
            o for o in data.get("observations", [])
            if o.get("value", ".") != "."
        ]
        if not observations:
            raise ValueError("No valid observations returned")

        latest_val = float(observations[0]["value"])
        latest_date_str = observations[0].get("date", "")

        daily_change_pct: float | None = None
        if len(observations) >= 2:
            prev_val = float(observations[1]["value"])
            if prev_val != 0:
                daily_change_pct = (latest_val - prev_val) / abs(prev_val) * 100.0

        change_5d_pct: float | None = None
        if len(observations) >= 6:
            val_5d = float(observations[5]["value"])
            if val_5d != 0:
                change_5d_pct = (latest_val - val_5d) / abs(val_5d) * 100.0

        # Determine freshness: FRED data is T+1 at best
        try:
            obs_date = date.fromisoformat(latest_date_str)
            days_old = (today - obs_date).days
            freshness = "live" if days_old <= 1 else "stale"
        except Exception:
            freshness = "stale"

        return FXQuote(
            instrument=instrument,
            value=round(latest_val, 4),
            daily_change_pct=round(daily_change_pct, 4) if daily_change_pct is not None else None,
            change_5d_pct=round(change_5d_pct, 4) if change_5d_pct is not None else None,
            source="fred",
            freshness=freshness,
            status="ok" if freshness != "stale" else "stale",
            fetched_at=_utcnow(),
        )

    except Exception as exc:
        logger.warning("FX panel: FRED fetch failed for %s: %s", instrument.label, exc)
        return FXQuote(
            instrument=instrument,
            value=None,
            daily_change_pct=None,
            change_5d_pct=None,
            source="fred",
            freshness="unavailable",
            status="unavailable",
            fetched_at=None,
        )


def _fetch_yfinance_instrument(instrument: FXInstrument) -> FXQuote:
    """Fetch a yfinance-sourced FX instrument."""
    symbols_to_try = [instrument.symbol]
    # Add CNY fallback for CNH
    if instrument.symbol == "USDCNH=X":
        symbols_to_try.append("USDCNY=X")

    for sym in symbols_to_try:
        try:
            import yfinance as yf
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="10d", interval="1d")
            if hist.empty or len(hist) < 1:
                continue

            closes = hist["Close"].dropna()
            if len(closes) < 1:
                continue

            latest_val = float(closes.iloc[-1])
            daily_change_pct: float | None = None
            change_5d_pct: float | None = None

            if len(closes) >= 2:
                prev_val = float(closes.iloc[-2])
                if prev_val != 0:
                    daily_change_pct = (latest_val - prev_val) / abs(prev_val) * 100.0

            if len(closes) >= 6:
                val_5d = float(closes.iloc[-6])
                if val_5d != 0:
                    change_5d_pct = (latest_val - val_5d) / abs(val_5d) * 100.0

            return FXQuote(
                instrument=instrument,
                value=round(latest_val, 4),
                daily_change_pct=round(daily_change_pct, 4) if daily_change_pct is not None else None,
                change_5d_pct=round(change_5d_pct, 4) if change_5d_pct is not None else None,
                source="yfinance",
                freshness="prior_close",
                status="ok",
                fetched_at=_utcnow(),
            )
        except Exception as exc:
            logger.warning(
                "FX panel: yfinance fetch failed for %s (%s): %s",
                instrument.label, sym, exc,
            )
            continue

    return FXQuote(
        instrument=instrument,
        value=None,
        daily_change_pct=None,
        change_5d_pct=None,
        source="yfinance",
        freshness="unavailable",
        status="unavailable",
        fetched_at=None,
    )


def _fetch_ecb_instrument(instrument: FXInstrument) -> FXQuote:
    """Fetch an ECB reference-rate instrument. Falls back to yfinance on failure."""
    try:
        import requests
        # ECB Data Portal: daily EUR FX reference rates
        # e.g. EUR/USD: series D.USD.EUR.SP00.A
        quote_ccy = instrument.quote.upper()
        if instrument.base.upper() != "EUR":
            raise ValueError("ECB source only supports EUR base; falling back to yfinance")
        series_key = f"D.{quote_ccy}.EUR.SP00.A"
        url = (
            f"https://data-api.ecb.europa.eu/service/data/EXR/{series_key}"
            "?format=jsondata&lastNObservations=10"
        )
        response = requests.get(url, timeout=10, headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()

        # Navigate ECB SDMX-JSON structure
        series_data = (
            data.get("dataSets", [{}])[0]
            .get("series", {})
            .get("0:0:0:0:0", {})
        )
        observations = series_data.get("observations", {})
        if not observations:
            raise ValueError("No ECB observations")

        # ECB returns EUR/quote, but we want quote/EUR-like. The series is already EUR=1 unit.
        # For EUR/USD, ECB gives USD per EUR.
        sorted_keys = sorted(observations.keys(), key=int, reverse=True)
        latest_key = sorted_keys[0]
        latest_val = float(observations[latest_key][0])

        daily_change_pct: float | None = None
        change_5d_pct: float | None = None

        if len(sorted_keys) >= 2:
            prev_val = float(observations[sorted_keys[1]][0])
            if prev_val != 0:
                daily_change_pct = (latest_val - prev_val) / abs(prev_val) * 100.0

        if len(sorted_keys) >= 6:
            val_5d = float(observations[sorted_keys[5]][0])
            if val_5d != 0:
                change_5d_pct = (latest_val - val_5d) / abs(val_5d) * 100.0

        return FXQuote(
            instrument=instrument,
            value=round(latest_val, 4),
            daily_change_pct=round(daily_change_pct, 4) if daily_change_pct is not None else None,
            change_5d_pct=round(change_5d_pct, 4) if change_5d_pct is not None else None,
            source="ecb",
            freshness="prior_close",
            status="ok",
            fetched_at=_utcnow(),
        )

    except Exception as exc:
        logger.warning(
            "FX panel: ECB fetch failed for %s: %s; falling back to yfinance",
            instrument.label, exc,
        )
        return _fetch_yfinance_instrument(instrument)


def fetch_fx_panel(basket: list[FXInstrument], settings: dict) -> list[FXQuote]:
    """Fetch FX quotes for all instruments in the basket.

    Individual failures are handled gracefully: each instrument gets a
    quote with status="unavailable" rather than raising an exception.

    Parameters
    ----------
    basket:
        List of FXInstrument objects from build_fx_basket().
    settings:
        Application settings dict. Must contain fred_api_key if any
        FRED-sourced instruments are in the basket.

    Returns
    -------
    list[FXQuote]
        One FXQuote per instrument, in the same order as the basket.
    """
    quotes: list[FXQuote] = []
    for instrument in basket:
        try:
            if instrument.source == "fred":
                quote = _fetch_fred_instrument(instrument, settings)
            elif instrument.source == "ecb":
                quote = _fetch_ecb_instrument(instrument)
            else:
                quote = _fetch_yfinance_instrument(instrument)
        except Exception as exc:
            logger.warning(
                "FX panel: unexpected error for %s: %s", instrument.label, exc
            )
            quote = FXQuote(
                instrument=instrument,
                value=None,
                daily_change_pct=None,
                change_5d_pct=None,
                source=instrument.source,
                freshness="unavailable",
                status="unavailable",
                fetched_at=None,
            )
        quotes.append(quote)
    return quotes
