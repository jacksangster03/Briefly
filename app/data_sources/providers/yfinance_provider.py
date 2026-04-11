"""yfinance fallback provider: quotes and price history via Yahoo Finance.

IMPORTANT: yfinance is an unofficial wrapper around Yahoo Finance.
It requires no API key but may break without notice.
Use only as a fallback when primary providers fail.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import PricePoint, QuoteData

logger = get_logger("yfinance")


class YFinanceProvider(BaseProvider):
    name = "yfinance"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._yf = None

    def is_configured(self) -> bool:
        return self._ensure_import()

    def _ensure_import(self) -> bool:
        """Lazily import yfinance; return False if not installed."""
        if self._yf is not None:
            return True
        try:
            import yfinance as yf
            self._yf = yf
            return True
        except ImportError:
            logger.info("yfinance not installed; fallback provider unavailable")
            return False

    # -- Quotes ---------------------------------------------------------------

    def get_quote(self, symbol: str) -> QuoteData | None:
        """Fetch a quote using yfinance. Returns None on failure."""
        if not self._ensure_import():
            return None

        try:
            ticker = self._yf.Ticker(symbol)
            info = ticker.fast_info
            price = getattr(info, "last_price", None) or 0.0
            prev_close = getattr(info, "previous_close", None) or 0.0
            change = price - prev_close if price and prev_close else 0.0
            pct = (change / prev_close * 100) if prev_close else 0.0

            if price == 0:
                return None

            return QuoteData(
                symbol=symbol,
                current_price=round(price, 2),
                change=round(change, 2),
                change_percent=round(pct, 2),
                high=round(getattr(info, "day_high", 0.0) or 0.0, 2),
                low=round(getattr(info, "day_low", 0.0) or 0.0, 2),
                open=round(getattr(info, "open", 0.0) or 0.0, 2),
                previous_close=round(prev_close, 2),
                volume=getattr(info, "last_volume", None),
                timestamp=datetime.now(timezone.utc),
                source="yfinance",
            )
        except Exception as exc:
            logger.warning("yfinance failed for %s: %s", symbol, exc)
            return None

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
        """Fetch quotes for multiple symbols. Skips failures."""
        results = []
        for sym in symbols:
            quote = self.get_quote(sym)
            if quote:
                results.append(quote)
        return results

    # -- Price history -------------------------------------------------------

    def get_price_history(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> list[PricePoint]:
        """Fetch simple OHLC history for chart rendering."""
        if not self._ensure_import():
            return []

        try:
            ticker = self._yf.Ticker(symbol)
            history = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            logger.warning("yfinance history failed for %s: %s", symbol, exc)
            return []

        if history is None or history.empty:
            return []

        points: list[PricePoint] = []
        for index, row in history.iterrows():
            ts = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            points.append(
                PricePoint(
                    symbol=symbol,
                    timestamp=ts,
                    open=round(float(row.get("Open", 0.0) or 0.0), 2),
                    high=round(float(row.get("High", 0.0) or 0.0), 2),
                    low=round(float(row.get("Low", 0.0) or 0.0), 2),
                    close=round(float(row.get("Close", 0.0) or 0.0), 2),
                    volume=float(row.get("Volume", 0.0) or 0.0) or None,
                    source="yfinance",
                )
            )
        return points
