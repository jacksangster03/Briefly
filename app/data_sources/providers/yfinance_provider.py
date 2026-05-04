"""yfinance fallback provider: quotes and price history via Yahoo Finance.

IMPORTANT: yfinance is an unofficial wrapper around Yahoo Finance.
It requires no API key but may break without notice.
Use only as a fallback when primary providers fail.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import MarketBreadth, PricePoint, QuoteData

_SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLC": "Communication Services",
}

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

    # -- Market breadth -------------------------------------------------------

    def get_index_breadth(self, symbol: str) -> MarketBreadth | None:
        """Volume-vs-20d-avg breadth for a single index or ETF."""
        if not self._ensure_import():
            return None
        try:
            ticker = self._yf.Ticker(symbol)
            history = ticker.history(period="1mo", interval="1d", auto_adjust=False)
            if history is None or history.empty or len(history) < 5:
                return None

            volumes = history["Volume"].dropna()
            today_vol = float(volumes.iloc[-1]) if not volumes.empty else None
            prior_vols = volumes.iloc[:-1].tail(20)
            avg_vol_20d = float(prior_vols.mean()) if len(prior_vols) >= 5 else None
            volume_vs_avg = (
                round(today_vol / avg_vol_20d, 3)
                if (today_vol and avg_vol_20d and avg_vol_20d > 0)
                else None
            )

            info = ticker.fast_info
            price = getattr(info, "last_price", 0.0) or 0.0
            prev = getattr(info, "previous_close", 0.0) or 0.0
            chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0

            return MarketBreadth(
                symbol=symbol,
                change_percent=chg_pct,
                day_high=round(getattr(info, "day_high", 0.0) or 0.0, 2),
                day_low=round(getattr(info, "day_low", 0.0) or 0.0, 2),
                volume=today_vol,
                avg_volume_20d=round(avg_vol_20d, 0) if avg_vol_20d else None,
                volume_vs_avg=volume_vs_avg,
            )
        except Exception as exc:
            logger.warning("yfinance breadth failed for %s: %s", symbol, exc)
            return None

    def get_sector_breadth(self) -> list[MarketBreadth]:
        """Return a MarketBreadth entry per SPDR sector ETF (up/down proxy)."""
        if not self._ensure_import():
            return []
        results: list[MarketBreadth] = []
        for symbol, name in _SECTOR_ETFS.items():
            try:
                ticker = self._yf.Ticker(symbol)
                info = ticker.fast_info
                price = getattr(info, "last_price", 0.0) or 0.0
                prev = getattr(info, "previous_close", 0.0) or 0.0
                chg_pct = round((price - prev) / prev * 100, 2) if prev else 0.0
                results.append(MarketBreadth(
                    symbol=symbol,
                    display_name=name,
                    change_percent=chg_pct,
                    day_high=round(getattr(info, "day_high", 0.0) or 0.0, 2),
                    day_low=round(getattr(info, "day_low", 0.0) or 0.0, 2),
                    volume=getattr(info, "last_volume", None),
                ))
            except Exception as exc:
                logger.warning("yfinance sector breadth failed for %s: %s", symbol, exc)
        logger.info("Sector breadth: %d/%d ETFs fetched", len(results), len(_SECTOR_ETFS))
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
