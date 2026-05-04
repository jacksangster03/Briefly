"""Trust-and-contract helpers for morning briefing consistency.

This module centralizes:
1. Canonical price resolution (one value per asset per run)
2. Closed-market filtering for active breadth calculations
3. Pre-send contradiction lints
4. Small text-similarity checks used by chart copy guards
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import math
import re
from zoneinfo import ZoneInfo

from app.cadence.exchange_calendar import exchange_for_symbol, is_exchange_closed
from app.schemas.briefings import MorningBriefing
from app.schemas.events import MacroDataPoint, NormalisedEvent, QuoteData
from app.universe.ticker_metadata import company_name_for_ticker

_EPS = 1e-12
_GEO_LEVELS = ("LOW", "MODERATE", "ELEVATED", "HIGH", "EXTREME")
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|corp|corp\.|corporation|company|co|co\.|group|plc|ltd|limited|holdings?|sa|ag|nv)\b",
    re.IGNORECASE,
)

_QUOTE_SOURCE_RANK = {
    "finnhub": 5,
    "alpaca": 4,
    "yfinance": 3,
    "yfinance_live": 3,
    "fred": 2,
    "ecb": 2,
    "unknown": 1,
}


def active_index_quotes(briefing: MorningBriefing, *, timezone_name: str) -> list[QuoteData]:
    """Return index quotes that should count toward *active* breadth.

    Closed exchange benchmarks (e.g., FTSE on UK holidays) are excluded.
    """
    local_date = briefing.generated_at.astimezone(ZoneInfo(timezone_name)).date()
    active: list[QuoteData] = []
    for quote in briefing.market_setup.index_quotes:
        ex = exchange_for_symbol(quote.symbol)
        if not ex:
            active.append(quote)
            continue
        closed, _ = is_exchange_closed(ex, local_date)
        if not closed:
            active.append(quote)
    return active


def resolve_canonical_prices(briefing: MorningBriefing) -> dict[str, dict]:
    """Build canonical per-asset values and apply them across briefing surfaces."""
    canonical: dict[str, dict] = {}
    quote_pools: list[QuoteData] = (
        list(briefing.market_setup.index_quotes)
        + list(briefing.market_setup.macro_quotes)
        + list(briefing.watchlist_quotes)
        + list(briefing.portfolio_quotes)
        + [snap.etf_quote for snap in briefing.sector_scan if snap.etf_quote]
    )
    for quote in quote_pools:
        key = _asset_key_from_quote(quote)
        if not key:
            continue
        record = {
            "asset_key": key,
            "symbol": quote.symbol,
            "value": float(quote.current_price or 0.0),
            "change": float(quote.change or 0.0),
            "change_percent": float(quote.change_percent or 0.0),
            "source": (quote.source or "unknown").strip().lower() or "unknown",
            "timestamp": quote.timestamp.isoformat() if quote.timestamp else "",
        }
        current = canonical.get(key)
        if current is None or _quote_rank(record) > _quote_rank(current):
            canonical[key] = record

    # Rates/macros are canonicalized from macro context first.
    for point in briefing.macro_context:
        key = _asset_key_from_macro(point)
        if not key:
            continue
        canonical.setdefault(
            key,
            {
                "asset_key": key,
                "symbol": point.series_id,
                "value": float(point.value or 0.0),
                "change": float(point.change or 0.0) if point.change is not None else 0.0,
                "change_percent": float(point.change_percent or 0.0) if point.change_percent is not None else 0.0,
                "source": (point.source or "unknown").strip().lower() or "unknown",
                "timestamp": point.date or "",
            },
        )

    # Commodity strip fills missing commodity keys when live quotes are absent.
    for point in briefing.commodity_strip:
        key = _asset_key_from_macro(point)
        if not key:
            continue
        canonical.setdefault(
            key,
            {
                "asset_key": key,
                "symbol": point.series_id,
                "value": float(point.value or 0.0),
                "change": float(point.change or 0.0) if point.change is not None else 0.0,
                "change_percent": float(point.change_percent or 0.0) if point.change_percent is not None else 0.0,
                "source": (point.source or "unknown").strip().lower() or "unknown",
                "timestamp": point.date or "",
            },
        )

    _apply_canonical_to_briefing(briefing, canonical)
    return canonical


def run_pre_send_lints(
    briefing: MorningBriefing,
    *,
    timezone_name: str,
) -> list[str]:
    """Run trust lints that guard against contradictory output."""
    warnings: list[str] = []
    warnings.extend(_lint_same_asset_mismatches(briefing))
    warnings.extend(_lint_geo_label_conflict(briefing))
    warnings.extend(_lint_ticker_company_mismatch(briefing))
    warnings.extend(_lint_closed_market_breadth(briefing, timezone_name=timezone_name))
    warnings.extend(_lint_index_level_integrity(briefing))
    warnings.extend(_lint_rates_direction_conflict(briefing))
    return warnings


def distinct_lines(read_line: str, why_line: str, lens_line: str) -> tuple[str, str, str]:
    """Ensure READ/WHY/LENS are not duplicates after normalization."""
    read_display = re.sub(r"\s+", " ", (read_line or "").strip())
    why_display = re.sub(r"\s+", " ", (why_line or "").strip())
    lens_display = re.sub(r"\s+", " ", (lens_line or "").strip())

    read_norm = _normalize_sentence(read_display)
    why_norm = _normalize_sentence(why_display)
    lens_norm = _normalize_sentence(lens_display)

    if _is_duplicate(read_norm, why_norm):
        why_display = "This matters only if follow-through is confirmed across breadth and cross-asset context."
        why_norm = _normalize_sentence(why_display)
    if _is_duplicate(read_norm, lens_norm) or _is_duplicate(why_norm, lens_norm):
        lens_display = "Map this move to concentration and macro sensitivity before changing risk posture."
    return read_display, why_display, lens_display


def chart_copy_is_distinct(read_line: str, why_line: str, lens_line: str) -> bool:
    read = _normalize_sentence(read_line)
    why = _normalize_sentence(why_line)
    lens = _normalize_sentence(lens_line)
    return not _is_duplicate(read, why) and not _is_duplicate(read, lens) and not _is_duplicate(why, lens)


def section_confidence(briefing: MorningBriefing, *, timezone_name: str) -> dict[str, str]:
    quotes = list(briefing.watchlist_quotes or briefing.portfolio_quotes or briefing.market_setup.index_quotes)
    latest_quote_ts = max((q.timestamp for q in quotes if q.timestamp), default=None)
    market_conf = "LOW"
    if latest_quote_ts is not None:
        now_local = briefing.generated_at.astimezone(ZoneInfo(timezone_name))
        ts_local = latest_quote_ts.astimezone(ZoneInfo(timezone_name)) if latest_quote_ts.tzinfo else latest_quote_ts.replace(tzinfo=ZoneInfo(timezone_name))
        age_h = max(0.0, (now_local - ts_local).total_seconds() / 3600.0)
        if age_h <= 6:
            market_conf = "HIGH"
        elif age_h <= 24:
            market_conf = "MEDIUM"
        else:
            market_conf = "LOW"

    macro_keys = {"DGS2", "DGS10", "T10Y2Y"}
    present_macro = {p.series_id.upper() for p in briefing.macro_context if p.series_id}
    macro_conf = "HIGH" if macro_keys.issubset(present_macro) else "MEDIUM" if present_macro else "LOW"
    news_conf = "HIGH" if briefing.events_fetched >= 300 else "MEDIUM" if briefing.events_fetched >= 120 else "LOW"
    portfolio_conf = "HIGH" if briefing.portfolio_quotes else "MEDIUM" if briefing.watchlist_quotes else "LOW"
    geo_conf = "HIGH" if briefing.geo_risk_level and briefing.geo_risk_summary else "MEDIUM" if briefing.geo_risk_level else "LOW"
    regime_base = (briefing.market_setup_analysis_confidence or "low").strip().lower()
    regime_conf = "HIGH" if regime_base in {"high", "med-high", "medium-high"} else "MEDIUM" if regime_base in {"medium", "med"} else "LOW"
    section = {
        "Regime": regime_conf,
        "Market prices": market_conf,
        "News": news_conf,
        "Macro/rates": macro_conf,
        "Portfolio": portfolio_conf,
        "Geo risk": geo_conf,
    }
    if briefing.healthcare_intelligence and briefing.healthcare_intelligence.enabled:
        section["Healthcare"] = briefing.healthcare_intelligence.confidence or "MEDIUM"
    return section


def freshness_block(briefing: MorningBriefing, *, timezone_name: str) -> dict[str, str]:
    tz = ZoneInfo(timezone_name)
    generated = briefing.generated_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    quotes = list(briefing.watchlist_quotes or briefing.portfolio_quotes or briefing.market_setup.index_quotes)
    latest_quote_ts = max((q.timestamp for q in quotes if q.timestamp), default=None)
    if latest_quote_ts is None:
        market_line = "unavailable"
    else:
        local_ts = latest_quote_ts.astimezone(tz) if latest_quote_ts.tzinfo else latest_quote_ts.replace(tzinfo=tz)
        age_h = max(0.0, (briefing.generated_at.astimezone(tz) - local_ts).total_seconds() / 3600.0)
        tag = "prior close" if age_h >= 8 else "near-real-time"
        market_line = f"{tag}, {local_ts.strftime('%Y-%m-%d %H:%M %Z')}"

    latest_macro_date = max((p.date for p in briefing.macro_context if p.date), default="")
    macro_line = f"latest available release ({latest_macro_date})" if latest_macro_date else "latest available release"

    latest_port_ts = max((q.timestamp for q in briefing.portfolio_quotes if q.timestamp), default=None)
    if latest_port_ts is None:
        pnl_line = "derived from available portfolio quote set"
    else:
        local_pt = latest_port_ts.astimezone(tz) if latest_port_ts.tzinfo else latest_port_ts.replace(tzinfo=tz)
        pnl_line = f"based on quote snapshot {local_pt.strftime('%Y-%m-%d %H:%M %Z')}"
    return {
        "Market Prices": market_line,
        "News": f"live, generated {generated}",
        "Macro/FRED": macro_line,
        "Portfolio P&L": pnl_line,
    }


def _asset_key_from_quote(quote: QuoteData) -> str:
    sym = (quote.symbol or "").upper().strip()
    name = (quote.display_name or "").upper().strip()
    key_text = f"{sym} {name}"
    if "VIX" in key_text:
        return "VIX"
    if "WTI" in key_text or ("CRUDE" in key_text and "BRENT" not in key_text) or sym in {"CL1:COM", "CL=F"}:
        return "WTI"
    if "BRENT" in key_text or sym in {"BZ=F"}:
        return "BRENT"
    if "GOLD" in key_text or sym in {"GC1:COM", "GC=F"}:
        return "GOLD"
    if "10Y" in key_text and "YIELD" in key_text:
        return "US10Y"
    if "2Y" in key_text and "YIELD" in key_text:
        return "US2Y"
    if sym in {"SPX", "^GSPC"} or ("S&P 500" in name and sym.startswith("^")):
        return "SPX"
    if sym in {"COMP", "^IXIC"} or ("NASDAQ COMPOSITE" in name and sym.startswith("^")):
        return "COMP"
    if sym in {"DJIA", "^DJI"} or ("DOW JONES" in name and sym.startswith("^")):
        return "DJIA"
    if sym in {"RUT", "^RUT"} or ("RUSSELL 2000" in name and sym.startswith("^")):
        return "RUT"
    if sym in {"SPY", "QQQ", "DIA", "IWM"}:
        return sym
    if sym in {"^FTSE", "FTSE", "UKX"} or "FTSE" in name:
        return "FTSE"
    if sym in {"^STOXX50E", "SX5E"} or "STOXX" in name:
        return "STOXX50"
    if sym in {"^N225", "NIKKEI"} or "NIKKEI" in name:
        return "NIKKEI225"
    if sym in {"^HSI", "HSI"} or "HANG SENG" in name:
        return "HANGSENG"
    if sym.startswith("XL") or sym in {"IGV", "XBI", "KRE"}:
        return f"SECTOR:{sym}"
    return sym or name


def _asset_key_from_macro(point: MacroDataPoint) -> str:
    sid = (point.series_id or "").upper().strip()
    name = (point.name or "").upper().strip()
    key_text = f"{sid} {name}"
    if "DGS10" in sid or "10Y TREASURY" in name:
        return "US10Y"
    if "DGS2" in sid or "2Y TREASURY" in name:
        return "US2Y"
    if "DGS30" in sid or "30Y TREASURY" in name:
        return "US30Y"
    if "T10Y2Y" in sid or "10Y-2Y" in name:
        return "US10Y2Y"
    if "DCOILWTICO" in sid or "WTI" in key_text:
        return "WTI"
    if "DCOILBRENTEU" in sid or "BRENT" in key_text:
        return "BRENT"
    if "GOLD" in sid or "GOLD" in name:
        return "GOLD"
    return sid or name


def _quote_rank(record: dict) -> tuple[int, str]:
    source = str(record.get("source") or "unknown").lower()
    return (_QUOTE_SOURCE_RANK.get(source, 1), str(record.get("timestamp") or ""))


def _apply_canonical_to_briefing(briefing: MorningBriefing, canonical: dict[str, dict]) -> None:
    for quote in briefing.market_setup.index_quotes:
        _apply_canonical_to_quote(quote, canonical)
    for quote in briefing.market_setup.macro_quotes:
        _apply_canonical_to_quote(quote, canonical)
    for quote in briefing.watchlist_quotes:
        _apply_canonical_to_quote(quote, canonical)
    for quote in briefing.portfolio_quotes:
        _apply_canonical_to_quote(quote, canonical)
    for snap in briefing.sector_scan:
        if snap.etf_quote:
            _apply_canonical_to_quote(snap.etf_quote, canonical)
    for row in briefing.market_setup.market_breadth:
        key = f"SECTOR:{(row.symbol or '').upper()}"
        canon = canonical.get(key)
        if canon is not None:
            row.change_percent = float(canon.get("change_percent") or 0.0)
    for point in briefing.macro_context:
        key = _asset_key_from_macro(point)
        canon = canonical.get(key)
        if canon is not None:
            point.value = float(canon.get("value") or point.value)
            point.change = float(canon.get("change") or 0.0)
            point.change_percent = float(canon.get("change_percent") or 0.0)
    for point in briefing.commodity_strip:
        key = _asset_key_from_macro(point)
        canon = canonical.get(key)
        if canon is not None:
            point.value = float(canon.get("value") or point.value)
            point.change = float(canon.get("change") or 0.0)
            point.change_percent = float(canon.get("change_percent") or 0.0)
    if briefing.market_setup.treasury_10y:
        canon = canonical.get("US10Y")
        if canon is not None:
            briefing.market_setup.treasury_10y.value = float(canon.get("value") or briefing.market_setup.treasury_10y.value)
            briefing.market_setup.treasury_10y.change = float(canon.get("change") or 0.0)
            briefing.market_setup.treasury_10y.change_percent = float(canon.get("change_percent") or 0.0)
    if briefing.market_setup.treasury_2y:
        canon = canonical.get("US2Y")
        if canon is not None:
            briefing.market_setup.treasury_2y.value = float(canon.get("value") or briefing.market_setup.treasury_2y.value)
            briefing.market_setup.treasury_2y.change = float(canon.get("change") or 0.0)
            briefing.market_setup.treasury_2y.change_percent = float(canon.get("change_percent") or 0.0)


def _apply_canonical_to_quote(quote: QuoteData, canonical: dict[str, dict]) -> None:
    key = _asset_key_from_quote(quote)
    canon = canonical.get(key)
    if canon is None:
        return
    quote.current_price = float(canon.get("value") or quote.current_price)
    quote.change = float(canon.get("change") or quote.change)
    quote.change_percent = float(canon.get("change_percent") or quote.change_percent)


def _lint_same_asset_mismatches(briefing: MorningBriefing) -> list[str]:
    observed: dict[str, list[float]] = {}
    for quote in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes + briefing.watchlist_quotes + briefing.portfolio_quotes:
        key = _asset_key_from_quote(quote)
        if not key:
            continue
        observed.setdefault(key, []).append(float(quote.current_price or 0.0))
    for point in briefing.macro_context + briefing.commodity_strip:
        key = _asset_key_from_macro(point)
        if not key:
            continue
        observed.setdefault(key, []).append(float(point.value or 0.0))

    warnings: list[str] = []
    for key, values in observed.items():
        clean = [v for v in values if not math.isnan(v)]
        if len(clean) <= 1:
            continue
        span = max(clean) - min(clean)
        avg = sum(abs(v) for v in clean) / len(clean)
        tol = max(0.02, avg * 0.002)  # 20 bps relative tolerance for display consistency
        if span > tol:
            warnings.append(f"Asset mismatch: {key} span {span:.4f} across sections.")

    # Sector ETF consistency: market breadth % should align with sector scan ETF % when both exist.
    breadth_moves = {
        (row.symbol or "").upper(): float(row.change_percent or 0.0)
        for row in briefing.market_setup.market_breadth
        if row.symbol
    }
    for snap in briefing.sector_scan:
        if not snap.etf_quote:
            continue
        sym = (snap.etf_quote.symbol or "").upper()
        if sym in breadth_moves:
            a = breadth_moves[sym]
            b = float(snap.etf_quote.change_percent or 0.0)
            if abs(a - b) > 0.06:
                warnings.append(f"Asset mismatch: sector {sym} breadth={a:+.2f}% vs sector_scan={b:+.2f}%.")
    return warnings


def _lint_geo_label_conflict(briefing: MorningBriefing) -> list[str]:
    warnings: list[str] = []
    final = (briefing.geo_risk_level or "").strip().upper()
    raw = (briefing.geo_risk_raw_level or "").strip().upper()
    summary = (briefing.geo_risk_summary or "").upper()
    mentioned = [level for level in _GEO_LEVELS if f"GEO RISK {level}" in summary]
    unique = sorted(set(mentioned))
    if len(unique) > 1:
        warnings.append(f"Geo summary mentions multiple levels: {', '.join(unique)}.")
    if raw and final and raw != final and raw in summary and final in summary:
        warnings.append(f"Geo raw/final conflict exposed in summary ({raw} vs {final}).")
    return warnings


def _lint_ticker_company_mismatch(briefing: MorningBriefing) -> list[str]:
    warnings: list[str] = []
    events = briefing.global_news + briefing.top_themes + briefing.portfolio_focus + briefing.watchlist_events
    for event in events:
        if not event.tickers:
            continue
        if _event_ticker_text_match(event):
            continue
        confidence = _event_ticker_confidence(event)
        if confidence < 0.75:
            tickers = ",".join(event.tickers[:2])
            warnings.append(f"Ticker/company mismatch risk on '{event.title[:54]}' ({tickers}, conf={confidence:.2f}).")
    return warnings


def _event_ticker_confidence(event: NormalisedEvent) -> float:
    raw = event.raw_data or {}
    for key in ("symbol_confidence", "ticker_confidence", "resolution_confidence"):
        val = raw.get(key)
        if isinstance(val, (float, int)):
            return float(val)
    return 0.5


def _event_ticker_text_match(event: NormalisedEvent) -> bool:
    text = f"{event.title} {event.summary}".lower()
    for ticker in event.tickers:
        symbol = (ticker or "").upper().strip()
        if not symbol:
            continue
        company = company_name_for_ticker(symbol)
        if company == symbol:
            return True
        aliases = _company_aliases(company)
        if aliases and any(alias in text for alias in aliases):
            return True
    return False


def _company_aliases(company: str) -> list[str]:
    base = (company or "").lower().strip()
    if not base:
        return []
    cleaned = _COMPANY_SUFFIX_RE.sub(" ", base)
    cleaned = re.sub(r"[^a-z0-9&.\-\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    aliases: list[str] = []
    if cleaned:
        aliases.append(cleaned)
        parts = [p for p in cleaned.split(" ") if len(p) >= 4]
        if parts:
            aliases.append(parts[0])
    return list(dict.fromkeys(aliases))


def _lint_closed_market_breadth(briefing: MorningBriefing, *, timezone_name: str) -> list[str]:
    warnings: list[str] = []
    local_day: date = briefing.generated_at.astimezone(ZoneInfo(timezone_name)).date()
    closed_symbols: list[str] = []
    for quote in briefing.market_setup.index_quotes:
        ex = exchange_for_symbol(quote.symbol)
        if not ex:
            continue
        closed, _ = is_exchange_closed(ex, local_day)
        if closed:
            closed_symbols.append(quote.symbol)
    if not closed_symbols:
        return warnings

    active_count = len(
        [
            quote for quote in briefing.market_setup.index_quotes
            if quote.symbol not in closed_symbols
        ]
    )
    match = re.search(r"\((\d+)/(\d+)\s+tracked benchmarks up\)", briefing.market_setup_analysis or "", re.IGNORECASE)
    if match:
        total = int(match.group(2))
        if total != active_count:
            warnings.append(
                f"Closed market breadth mismatch: narrative total={total}, active_total={active_count}, closed={','.join(closed_symbols)}."
            )
    return warnings


def _lint_index_level_integrity(briefing: MorningBriefing) -> list[str]:
    """Flag likely proxy/scaled values shown under canonical index labels."""
    warnings: list[str] = []
    thresholds = {
        "SPX": 1000.0,
        "COMP": 2000.0,
        "DJIA": 5000.0,
        "RUT": 500.0,
        "FTSE": 1000.0,
        "STOXX50": 500.0,
        "NIKKEI225": 5000.0,
        "HANGSENG": 1000.0,
    }
    for quote in briefing.market_setup.index_quotes:
        key = _asset_key_from_quote(quote)
        sym = (quote.symbol or "").upper()
        label = (quote.display_name or "").upper()
        if sym in {"SPY", "IVV", "VOO"} and ("S&P 500" in label or "SPX" in label):
            warnings.append(f"Index level integrity: {quote.display_name or quote.symbol} is using ETF proxy symbol {sym}.")
        if sym in {"QQQ", "ONEQ"} and ("NASDAQ" in label or "COMP" in label):
            warnings.append(f"Index level integrity: {quote.display_name or quote.symbol} is using ETF proxy symbol {sym}.")
        if sym in {"DIA"} and ("DOW" in label or "DJIA" in label):
            warnings.append(f"Index level integrity: {quote.display_name or quote.symbol} is using ETF proxy symbol {sym}.")
        if sym in {"IWM"} and ("RUSSELL" in label or "RUT" in label):
            warnings.append(f"Index level integrity: {quote.display_name or quote.symbol} is using ETF proxy symbol {sym}.")
        if key not in thresholds:
            continue
        value = float(quote.current_price or 0.0)
        if value <= 0:
            continue
        if value < thresholds[key]:
            warnings.append(
                f"Index level integrity: {quote.display_name or quote.symbol}={value:.2f} appears proxy/scaled."
            )
    return warnings


def _lint_rates_direction_conflict(briefing: MorningBriefing) -> list[str]:
    """Catch contradictory directions between setup treasury lines and macro context."""
    warnings: list[str] = []
    macro_map = {(_asset_key_from_macro(point)): point for point in briefing.macro_context}

    if briefing.market_setup.treasury_10y and "US10Y" in macro_map:
        setup = float(briefing.market_setup.treasury_10y.change or 0.0)
        macro = float(macro_map["US10Y"].change or 0.0)
        if setup * macro < 0 and abs(setup - macro) > 1e-6:
            warnings.append(
                f"Rates direction conflict: US10Y setup_change={setup:+.4f}, macro_change={macro:+.4f}."
            )
    if briefing.market_setup.treasury_2y and "US2Y" in macro_map:
        setup = float(briefing.market_setup.treasury_2y.change or 0.0)
        macro = float(macro_map["US2Y"].change or 0.0)
        if setup * macro < 0 and abs(setup - macro) > 1e-6:
            warnings.append(
                f"Rates direction conflict: US2Y setup_change={setup:+.4f}, macro_change={macro:+.4f}."
            )
    return warnings


def _normalize_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    if ":" in cleaned:
        _, tail = cleaned.split(":", 1)
        cleaned = tail.strip()
    cleaned = cleaned.lower().replace("−", "-")
    cleaned = re.sub(r"[^a-z0-9%+\-\s]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _is_duplicate(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    return overlap >= 0.88


def contract_warning_summary(warnings: list[str]) -> str:
    if not warnings:
        return "PASS"
    kinds = Counter(item.split(":", 1)[0] for item in warnings)
    return "WARN " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
