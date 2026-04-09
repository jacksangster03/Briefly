"""yfinance fallback provider: quotes and price history via Yahoo Finance.

IMPORTANT: yfinance is an unofficial wrapper around Yahoo Finance.
It requires no API key but may break without notice.
Use only as a fallback when primary providers fail.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import QuoteData

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
