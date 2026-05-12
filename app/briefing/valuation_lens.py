"""Optional deterministic valuation context module.

Disabled by default (include_valuation_lens = False in delivery preferences).
Only activates when enabled AND trigger conditions are met.

Trigger conditions (all deterministic, no LLM):
- rates_pressure is "tightening" or 10Y yield >= 4.5%
- At least one high-multiple growth/AI name in watchlist moved >2% on the day
- Earnings/valuation story is a selected theme
- Watchlist return dispersion > 3% (max - min of 1D returns)
- User preference include_valuation_lens = True

Data rules:
- Only already-configured providers (yfinance .info where available).
- If a ratio is missing, set it to None. Never substitute or hallucinate.
- Fetch: forwardPE, priceToSalesTrailing12Months, enterpriseToEbitda,
  marketCap, revenueGrowth, profitMargins from yfinance .info where available.
- For financials (banks/insurance): priceToBook and returnOnEquity instead.
- For ETFs: expenseRatio and yield where available.
"""

from __future__ import annotations

from typing import Any

from app.logger import get_logger

logger = get_logger("valuation_lens")

_AI_GROWTH_SYMBOLS = frozenset({"NVDA", "AMD", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "SMCI", "ARM"})
_FINANCIAL_KEYWORDS = frozenset({"bank", "financial", "insurer", "insurance", "asset management"})
_VALUATION_STORY_TYPES = frozenset({"commentary_valuation", "earnings_results", "guidance_change"})

SECTION_LABEL = "VALUATION LENS"
DISCLAIMER = "Valuation context only, not investment advice."


class ValuationLens:
    """
    Optional deterministic valuation context module.
    Disabled by default (include_valuation_lens = False).
    Only activates when enabled AND trigger conditions are met.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_items: int = 3,
    ) -> None:
        self.enabled = enabled
        self.max_items = max(1, min(5, max_items))

    def should_activate(self, briefing: Any) -> bool:
        """Return True if trigger conditions are met. Purely deterministic."""
        if not self.enabled:
            return False

        canonical = dict(getattr(briefing, "canonical_prices", None) or {})
        us10y_row = dict(canonical.get("US10Y") or {})
        us10y_level = float(us10y_row.get("value") or 0.0)
        us10y_change = float(us10y_row.get("change") or 0.0)

        rates_tightening = us10y_level >= 4.5 or us10y_change >= 0.03

        watchlist_quotes = list(getattr(briefing, "watchlist_quotes", None) or [])
        portfolio_quotes = list(getattr(briefing, "portfolio_quotes", None) or [])
        all_quotes = watchlist_quotes or portfolio_quotes

        watch_moves = [abs(float(q.change_percent or 0.0)) for q in all_quotes]
        watch_dispersion = (max(watch_moves) - min(watch_moves)) if len(watch_moves) >= 2 else 0.0
        high_dispersion = watch_dispersion >= 3.0

        ai_growth_move = any(
            q.symbol.upper() in _AI_GROWTH_SYMBOLS
            and abs(float(q.change_percent or 0.0)) >= 2.0
            for q in all_quotes
        )

        top_themes = list(getattr(briefing, "top_themes", None) or [])
        portfolio_focus = list(getattr(briefing, "portfolio_focus", None) or [])
        watchlist_events = list(getattr(briefing, "watchlist_events", None) or [])
        valuation_story = any(
            str((evt.raw_data or {}).get("news_story_type") or "").strip().lower()
            in _VALUATION_STORY_TYPES
            for evt in (top_themes + portfolio_focus + watchlist_events)
        )

        return rates_tightening or high_dispersion or ai_growth_move or valuation_story

    def build_lines(self, briefing: Any) -> list[str]:
        """Build valuation context bullets. Returns [] if disabled or not triggered."""
        if not self.enabled or not self.should_activate(briefing):
            return []

        watchlist_events = list(getattr(briefing, "watchlist_events", None) or [])
        portfolio_focus = list(getattr(briefing, "portfolio_focus", None) or [])
        top_themes = list(getattr(briefing, "top_themes", None) or [])
        candidates = watchlist_events or portfolio_focus or top_themes

        lines: list[str] = []
        seen_symbols: set[str] = set()

        for evt in candidates:
            if len(lines) >= self.max_items:
                break

            tickers = list(getattr(evt, "tickers", None) or [])
            symbol = next((s.upper() for s in tickers if s), "")
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)

            raw = dict(getattr(evt, "raw_data", None) or {})
            title_lower = (getattr(evt, "title", "") or "").lower()

            is_financial = any(tok in title_lower for tok in _FINANCIAL_KEYWORDS)
            is_etf = symbol.upper() in {"XLF", "XLK", "XLE", "XLV", "XLI", "SPY", "QQQ", "IWM", "GLD", "TLT"}

            metrics = _extract_metrics(raw, is_financial=is_financial, is_etf=is_etf)
            if not metrics:
                # Try fetching from yfinance .info (best-effort, no hallucination)
                metrics = _fetch_yfinance_ratios(symbol, is_financial=is_financial, is_etf=is_etf)

            if not metrics:
                continue

            lines.append(f"- {symbol}: " + " · ".join(metrics[:4]))

        if not lines:
            return []

        return [SECTION_LABEL] + lines + [DISCLAIMER]


def _extract_metrics(raw: dict[str, Any], *, is_financial: bool, is_etf: bool) -> list[str]:
    """Extract valuation metrics from existing raw event data (no external calls)."""

    def _pick(*keys: str) -> float | None:
        for key in keys:
            val = raw.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
        return None

    metrics: list[str] = []
    if is_etf:
        er = _pick("expenseRatio", "expense_ratio")
        if er is not None:
            metrics.append(f"ER {er:.2%}")
        yld = _pick("yield", "dividend_yield", "trailingAnnualDividendYield")
        if yld is not None:
            metrics.append(f"Yield {yld:.1%}")
        return metrics

    if is_financial:
        pb = _pick("pb", "price_to_book", "priceToBook")
        if pb is not None:
            metrics.append(f"P/B {pb:.2f}x")
        roe = _pick("roe", "returnOnEquity")
        if roe is not None:
            metrics.append(f"ROE {roe * 100:.1f}%")
    else:
        pe = _pick("pe", "forward_pe", "forwardPE")
        if pe is not None:
            metrics.append(f"Fwd P/E {pe:.1f}x")
        ps = _pick("ps", "price_to_sales", "priceToSalesTrailing12Months")
        if ps is not None:
            metrics.append(f"P/S {ps:.1f}x")
        ev_ebitda = _pick("ev_ebitda", "evToEbitda", "enterpriseToEbitda")
        if ev_ebitda is not None:
            metrics.append(f"EV/EBITDA {ev_ebitda:.1f}x")

    mcap = _pick("market_cap", "marketCap", "market_capitalization")
    if mcap is not None and mcap > 0:
        metrics.append(f"MCap {mcap / 1_000_000_000:.1f}B")

    return metrics


def _fetch_yfinance_ratios(symbol: str, *, is_financial: bool, is_etf: bool) -> list[str]:
    """Fetch valuation ratios from yfinance .info. Returns [] on any failure."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return []

    metrics: list[str] = []

    if is_etf:
        er = info.get("expenseRatio")
        if er is not None:
            try:
                metrics.append(f"ER {float(er):.2%}")
            except Exception:
                pass
        yld = info.get("yield") or info.get("trailingAnnualDividendYield")
        if yld is not None:
            try:
                metrics.append(f"Yield {float(yld):.1%}")
            except Exception:
                pass
        return metrics

    if is_financial:
        pb = info.get("priceToBook")
        if pb is not None:
            try:
                metrics.append(f"P/B {float(pb):.2f}x")
            except Exception:
                pass
        roe = info.get("returnOnEquity")
        if roe is not None:
            try:
                metrics.append(f"ROE {float(roe) * 100:.1f}%")
            except Exception:
                pass
    else:
        fpe = info.get("forwardPE")
        if fpe is not None:
            try:
                metrics.append(f"Fwd P/E {float(fpe):.1f}x")
            except Exception:
                pass
        ps = info.get("priceToSalesTrailing12Months")
        if ps is not None:
            try:
                metrics.append(f"P/S {float(ps):.1f}x")
            except Exception:
                pass
        ev = info.get("enterpriseToEbitda")
        if ev is not None:
            try:
                metrics.append(f"EV/EBITDA {float(ev):.1f}x")
            except Exception:
                pass

    mcap = info.get("marketCap")
    if mcap is not None:
        try:
            metrics.append(f"MCap {float(mcap) / 1_000_000_000:.1f}B")
        except Exception:
            pass

    return metrics
