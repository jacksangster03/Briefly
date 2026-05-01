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
            self._client = StockHistoricalDataClient(self.api_key, self.api_secret)
        return self._client

    def _fetch_latest_bar(self, symbol: str):
        from alpaca.data.requests import StockLatestBarRequest
        client = self._get_client()
        req = StockLatestBarRequest(symbol_or_symbols=symbol)
        bars = client.get_stock_latest_bar(req)
        return bars.get(symbol)

    def _fetch_prev_close(self, symbol: str) -> float | None:
        bars = self._fetch_bars(symbol, period="5d", interval="1d")
        if len(bars) >= 2:
            return float(bars[-2].close)
        return None

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
            bar = self._fetch_latest_bar(symbol)
            if bar is None:
                return None
            prev_close = self._fetch_prev_close(symbol) or float(bar.close)
            price = float(bar.close)
            change = price - float(prev_close)
            pct = (change / float(prev_close) * 100) if prev_close else 0.0
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

    def get_quotes(self, symbols: list[str]) -> list[QuoteData]:
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
