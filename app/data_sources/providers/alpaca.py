"""Alpaca market data provider.

Uses alpaca-py (official SDK). Provides real-time quotes and historical
bars via Alpaca's free market data tier.

Install: pip install alpaca-py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data_sources.base import BaseProvider
from app.logger import get_logger
from app.schemas.events import PricePoint, QuoteData

logger = get_logger("alpaca")

_PERIOD_TO_DAYS = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "max": 1825,
}
_INTERVAL_TO_TF = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min",
    "30m": "30Min", "1h": "1Hour", "1d": "1Day",
}


class AlpacaProvider(BaseProvider):
    name = "alpaca"

    def __init__(self, api_key: str, api_secret: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key
        self.api_secret = api_secret
        self._client = None

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _get_client(self):
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient
            # alpaca_base_url is for the trading broker client (order management).
            # StockHistoricalDataClient uses data.alpaca.markets internally.
            self._client = StockHistoricalDataClient(self.api_key, self.api_secret)
        return self._client

    def _fetch_recent_daily_bars(self, symbol: str, days: int = 5) -> list:
        """Fetch recent daily bars in a single API call."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        client = self._get_client()
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        bars_response = client.get_stock_bars(req)
        return list(bars_response.get(symbol, []))

    def _fetch_bars(self, symbol: str, period: str = "1mo", interval: str = "1d") -> list:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        days = _PERIOD_TO_DAYS.get(period, 30)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        tf_str = _INTERVAL_TO_TF.get(interval, "1Day")
        if tf_str == "1Day":
            timeframe = TimeFrame.Day
        elif tf_str == "1Hour":
            timeframe = TimeFrame.Hour
        else:
            minutes = int(tf_str.replace("Min", ""))
            timeframe = TimeFrame(minutes, TimeFrameUnit.Minute)

        client = self._get_client()
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        bars_response = client.get_stock_bars(req)
        return list(bars_response.get(symbol, []))

    def get_quote(self, symbol: str) -> QuoteData | None:
        if not self.is_configured():
            return None
        try:
            bars = self._fetch_recent_daily_bars(symbol, days=5)
            if not bars:
                return None
            bar = bars[-1]
            prev_close = float(bars[-2].close) if len(bars) >= 2 else float(bar.close)
            price = float(bar.close)
            change = price - prev_close
            pct = (change / prev_close * 100) if prev_close else 0.0
            return QuoteData(
                symbol=symbol,
                current_price=round(price, 2),
                change=round(change, 2),
                change_percent=round(pct, 2),
                high=round(float(bar.high), 2),
                low=round(float(bar.low), 2),
                open=round(float(bar.open), 2),
                previous_close=round(float(prev_close), 2),
                volume=float(bar.volume) if hasattr(bar, "volume") else None,
                timestamp=bar.timestamp if hasattr(bar, "timestamp") else datetime.now(timezone.utc),
                source="alpaca",
            )
        except Exception as exc:
            logger.warning("Alpaca quote failed for %s: %s", symbol, exc)
            return None

    def _get_quotes_batch(self, symbols: list[str]) -> list[QuoteData]:
        """Fetch quotes for multiple symbols in a single API call."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=5)
        client = self._get_client()
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        bars_response = client.get_stock_bars(req)
        results = []
        for sym in symbols:
            bars = list(bars_response.get(sym, []))
            if not bars:
                continue
            bar = bars[-1]
            prev_close = float(bars[-2].close) if len(bars) >= 2 else float(bar.close)
            price = float(bar.close)
            change = price - prev_close
            pct = (change / prev_close * 100) if prev_close else 0.0
            results.append(QuoteData(
                symbol=sym,
                current_price=round(price, 2),
                change=round(change, 2),
                change_percent=round(pct, 2),
                high=round(float(bar.high), 2),
                low=round(float(bar.low), 2),
                open=round(float(bar.open), 2),
                previous_close=round(float(prev_close), 2),
                volume=float(bar.volume) if hasattr(bar, "volume") else None,
                timestamp=bar.timestamp if hasattr(bar, "timestamp") else datetime.now(timezone.utc),
                source="alpaca",
            ))
        return results

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
        if not self.is_configured() or not symbols:
            return []
        try:
            return self._get_quotes_batch(symbols)
        except Exception as exc:
            logger.warning("Alpaca batch quotes failed, falling back to serial: %s", exc)
            results = []
            for sym in symbols:
                q = self.get_quote(sym)
                if q:
                    results.append(q)
            return results

    def get_price_history(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> list[PricePoint]:
        if not self.is_configured():
            return []
        try:
            bars = self._fetch_bars(symbol, period=period, interval=interval)
            return [
                PricePoint(
                    symbol=symbol,
                    timestamp=b.timestamp if hasattr(b, "timestamp") else datetime.now(timezone.utc),
                    open=round(float(b.open), 2),
                    high=round(float(b.high), 2),
                    low=round(float(b.low), 2),
                    close=round(float(b.close), 2),
                    volume=float(b.volume) if hasattr(b, "volume") else None,
                    source="alpaca",
                )
                for b in bars
            ]
        except Exception as exc:
            logger.warning("Alpaca history failed for %s: %s", symbol, exc)
            return []
