"""Optional deterministic valuation context module.

Disabled by default (include_valuation_lens = False in delivery preferences).
Only activates when enabled AND trigger conditions are met.

Trigger conditions (all deterministic, no LLM):
- rates_pressure is "tightening" or 10Y yield >= 4.4%
- At least 2 watchlist names moved > 2% intraday with high-multiple profile
- Earnings/valuation story is a selected theme
- Watchlist return dispersion > 3% (max - min of 1D returns)
- User preference include_valuation_lens = True

Data rules:
- Only already-configured providers (yfinance .info where available).
- If a ratio is missing, set it to None. Never substitute or hallucinate.
- Fetch: forwardPE, priceToSalesTrailing12Months, enterpriseToEbitda,
  marketCap, revenueGrowth, profitMargins, freeCashflow, pegRatio
  from yfinance .info where available.
- For financials (banks/insurance): priceToBook and returnOnEquity instead.
- For ETFs: expenseRatio and yield where available.

Peer groups:
- Mega-cap tech (AAPL, MSFT, GOOGL, AMZN, META, NVDA): fwd P/E and EV/EBITDA
- Semis (NVDA, AMD, AVGO, QCOM, TSM, INTC, ASML): P/E and revenue growth
- Pharma/biotech: P/E and pipeline context if available
- ETFs/indices: expense ratio and yield/duration
"""

from __future__ import annotations

from typing import Any

from app.logger import get_logger

logger = get_logger("valuation_lens")

_AI_GROWTH_SYMBOLS = frozenset({"NVDA", "AMD", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "SMCI", "ARM"})
_FINANCIAL_KEYWORDS = frozenset({"bank", "financial", "insurer", "insurance", "asset management"})
_VALUATION_STORY_TYPES = frozenset({"commentary_valuation", "earnings_results", "guidance_change"})

# Peer group definitions
_MEGA_CAP_TECH = frozenset({"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA"})
_SEMIS = frozenset({"NVDA", "AMD", "AVGO", "QCOM", "TSM", "INTC", "ASML"})
_PHARMA_BIOTECH = frozenset({"JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN", "GILD", "MRNA", "BIIB"})
_ETFS = frozenset({"XLF", "XLK", "XLE", "XLV", "XLI", "SPY", "QQQ", "IWM", "GLD", "TLT", "AGG", "HYG", "LQD"})

SECTION_LABEL = "VALUATION LENS"
DISCLAIMER = "Valuation context only, not investment advice."


def _peer_group_for(symbol: str) -> str:
    sym = symbol.upper()
    if sym in _MEGA_CAP_TECH:
        return "mega_cap_tech"
    if sym in _SEMIS:
        return "semis"
    if sym in _PHARMA_BIOTECH:
        return "pharma_biotech"
    if sym in _ETFS:
        return "etf"
    return "other"


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

        # Updated threshold: >= 4.4% (lowered from 4.5%)
        rates_tightening = us10y_level >= 4.4 or us10y_change >= 0.03

        watchlist_quotes = list(getattr(briefing, "watchlist_quotes", None) or [])
        portfolio_quotes = list(getattr(briefing, "portfolio_quotes", None) or [])
        all_quotes = watchlist_quotes or portfolio_quotes

        watch_moves = [abs(float(q.change_percent or 0.0)) for q in all_quotes]
        watch_dispersion = (max(watch_moves) - min(watch_moves)) if len(watch_moves) >= 2 else 0.0
        high_dispersion = watch_dispersion >= 3.0

        # Updated: at least 2 high-multiple names moved > 2% (was 1)
        ai_growth_movers = [
            q for q in all_quotes
            if q.symbol.upper() in _AI_GROWTH_SYMBOLS
            and abs(float(q.change_percent or 0.0)) >= 2.0
        ]
        ai_growth_move = len(ai_growth_movers) >= 2

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

        # Build a rates context note if 10Y is elevated
        canonical = dict(getattr(briefing, "canonical_prices", None) or {})
        us10y_row = dict(canonical.get("US10Y") or {})
        us10y_level = float(us10y_row.get("value") or 0.0)

        preamble_lines: list[str] = []
        if us10y_level >= 4.4:
            preamble_lines.append(
                f"- {us10y_level:.2f}% 10Y keeps pressure on high-duration growth multiples."
            )

        # Build peer group summaries
        peer_lines = self._build_peer_group_lines(briefing)

        # Per-name lines for watchlist/portfolio
        item_lines = self._build_per_name_lines(briefing)

        all_bullets = preamble_lines + peer_lines + item_lines
        if not all_bullets:
            return []

        # Cap at max_items bullets
        bullets = all_bullets[:self.max_items]

        # Check data completeness for label annotation
        has_partial = any("unavailable" in line.lower() or "partial" in line.lower() for line in bullets)
        label = f"{SECTION_LABEL} (partial data)" if has_partial else SECTION_LABEL

        return [label] + bullets + [DISCLAIMER]

    def _build_peer_group_lines(self, briefing: Any) -> list[str]:
        """Build peer-group comparison lines for mega-cap tech and semis."""
        watchlist_quotes = list(getattr(briefing, "watchlist_quotes", None) or [])
        portfolio_quotes = list(getattr(briefing, "portfolio_quotes", None) or [])
        all_quotes = watchlist_quotes + portfolio_quotes

        # Group symbols by peer group
        mega_cap_syms = [q.symbol.upper() for q in all_quotes if q.symbol.upper() in _MEGA_CAP_TECH]
        semi_syms = [q.symbol.upper() for q in all_quotes if q.symbol.upper() in _SEMIS]

        lines: list[str] = []

        if len(mega_cap_syms) >= 2:
            line = self._peer_comparison_line("Mega-cap tech", mega_cap_syms[:3], ["forwardPE", "enterpriseToEbitda"])
            if line:
                lines.append(line)

        if len(semi_syms) >= 2:
            line = self._peer_comparison_line("Semis", semi_syms[:3], ["forwardPE", "revenueGrowth"])
            if line:
                lines.append(line)

        return lines

    def _peer_comparison_line(
        self,
        group_label: str,
        symbols: list[str],
        ratio_keys: list[str],
    ) -> str:
        """Fetch ratios for multiple symbols and build a peer comparison line."""
        ratios_by_sym: dict[str, dict[str, float | None]] = {}
        for sym in symbols:
            ratios_by_sym[sym] = _fetch_yfinance_ratio_dict(sym, ratio_keys)

        # Compute median for each ratio
        for ratio_key in ratio_keys:
            vals = [
                v[ratio_key]
                for v in ratios_by_sym.values()
                if v.get(ratio_key) is not None
            ]
            if not vals:
                continue
            median = sorted(vals)[len(vals) // 2]

            # Find the outlier (if any)
            for sym, ratios in ratios_by_sym.items():
                val = ratios.get(ratio_key)
                if val is None:
                    continue
                ratio_label = _ratio_display_name(ratio_key)
                sym_val_str = _format_ratio(ratio_key, val)
                median_str = _format_ratio(ratio_key, median)
                if len(vals) >= 2:
                    return (
                        f"- {group_label}: {sym} {ratio_label} {sym_val_str} "
                        f"vs peer median {median_str}."
                    )
                return f"- {group_label}: {sym} {ratio_label} {sym_val_str}."

        # No data at all for this group
        available = [sym for sym, r in ratios_by_sym.items() if any(v is not None for v in r.values())]
        unavailable = [sym for sym, r in ratios_by_sym.items() if all(v is None for v in r.values())]
        if available:
            avail_str = ", ".join(available)
            unavail_str = ", ".join(unavailable) if unavailable else ""
            partial_note = f"; {unavail_str} unavailable" if unavail_str else ""
            return f"- {group_label}: valuation data partial; {avail_str} data available{partial_note}."
        return ""

    def _build_per_name_lines(self, briefing: Any) -> list[str]:
        """Build per-name valuation lines for watchlist/portfolio items."""
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
            is_etf = symbol.upper() in _ETFS

            metrics = _extract_metrics(raw, is_financial=is_financial, is_etf=is_etf)
            if not metrics:
                metrics = _fetch_yfinance_ratios(symbol, is_financial=is_financial, is_etf=is_etf)

            if not metrics:
                continue

            lines.append(f"- {symbol}: " + " · ".join(metrics[:4]))

        return lines


def _ratio_display_name(key: str) -> str:
    mapping = {
        "forwardPE": "fwd P/E",
        "enterpriseToEbitda": "EV/EBITDA",
        "revenueGrowth": "rev growth",
        "priceToSalesTrailing12Months": "P/S",
        "pegRatio": "PEG",
        "freeCashflowYield": "FCF yield",
    }
    return mapping.get(key, key)


def _format_ratio(key: str, val: float) -> str:
    if key in {"revenueGrowth", "freeCashflowYield"}:
        return f"{val * 100:.1f}%"
    if key in {"forwardPE", "enterpriseToEbitda", "pegRatio"}:
        return f"{val:.0f}x"
    if key == "priceToSalesTrailing12Months":
        return f"{val:.1f}x"
    return f"{val:.2f}"


def _fetch_yfinance_ratio_dict(symbol: str, ratio_keys: list[str]) -> dict[str, float | None]:
    """Fetch specific ratios from yfinance .info. Returns dict with None for unavailable."""
    result: dict[str, float | None] = {k: None for k in ratio_keys}
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return result

    for key in ratio_keys:
        val = info.get(key)
        if val is not None:
            try:
                result[key] = float(val)
            except Exception:
                pass

    # Derived FCF yield
    if "freeCashflowYield" in ratio_keys:
        fcf = info.get("freeCashflow")
        mcap = info.get("marketCap")
        if fcf is not None and mcap is not None and float(mcap or 0) > 0:
            try:
                result["freeCashflowYield"] = float(fcf) / float(mcap)
            except Exception:
                pass

    return result


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
        # FCF yield from raw data
        fcf = raw.get("freeCashflow")
        mcap = raw.get("marketCap")
        if fcf is not None and mcap is not None and float(mcap or 0) > 0:
            try:
                fcf_yield = float(fcf) / float(mcap)
                metrics.append(f"FCF yield {fcf_yield * 100:.1f}%")
            except Exception:
                pass
        # PEG
        peg = _pick("pegRatio")
        if peg is not None:
            metrics.append(f"PEG {peg:.2f}x")

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
        # FCF yield
        fcf = info.get("freeCashflow")
        mcap_val = info.get("marketCap")
        if fcf is not None and mcap_val is not None and float(mcap_val or 0) > 0:
            try:
                fcf_yield = float(fcf) / float(mcap_val)
                metrics.append(f"FCF yield {fcf_yield * 100:.1f}%")
            except Exception:
                pass
        # PEG
        peg = info.get("pegRatio")
        if peg is not None:
            try:
                metrics.append(f"PEG {float(peg):.2f}x")
            except Exception:
                pass

    mcap = info.get("marketCap")
    if mcap is not None:
        try:
            metrics.append(f"MCap {float(mcap) / 1_000_000_000:.1f}B")
        except Exception:
            pass

    return metrics
