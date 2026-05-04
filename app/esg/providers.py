"""yfinance sustainability data fetch + normalisation."""

from __future__ import annotations

from typing import Any

from app.logger import get_logger

logger = get_logger("esg.providers")

# Fallback scores by sector when yfinance returns nothing
_SECTOR_FALLBACK: dict[str, dict[str, float]] = {
    "technology": {"overall": 55.0, "e": 60.0, "s": 52.0, "g": 55.0},
    "healthcare": {"overall": 58.0, "e": 55.0, "s": 60.0, "g": 58.0},
    "financials": {"overall": 50.0, "e": 45.0, "s": 50.0, "g": 55.0},
    "energy": {"overall": 35.0, "e": 25.0, "s": 38.0, "g": 42.0},
    "materials": {"overall": 40.0, "e": 32.0, "s": 42.0, "g": 45.0},
    "industrials": {"overall": 48.0, "e": 45.0, "s": 48.0, "g": 50.0},
    "consumer": {"overall": 50.0, "e": 50.0, "s": 52.0, "g": 48.0},
    "utilities": {"overall": 45.0, "e": 38.0, "s": 48.0, "g": 48.0},
    "real_estate": {"overall": 52.0, "e": 55.0, "s": 50.0, "g": 50.0},
    "communication": {"overall": 50.0, "e": 48.0, "s": 50.0, "g": 52.0},
}

# Known bond/fixed-income ETFs that don't have equity ESG scores
_FIXED_INCOME_ETFS = {
    "BND", "AGG", "LQD", "HYG", "TLT", "IEF", "SHY", "VCIT",
    "VCSH", "BSV", "BIV", "BLV", "BNDX", "EMB", "MUB", "VTEB",
    "GOVT", "SCHO", "SCHR", "SCHZ", "SPAB", "SPSB", "SPTL",
    "IGLB", "IGIB", "IGSB",
}


def fetch_esg_for_symbol(symbol: str, sector_override: str | None = None) -> dict[str, Any]:
    """
    Fetch ESG data for a single symbol via yfinance.

    Returns dict with keys: overall, e_score, s_score, g_score,
    controversy_level, provider, confidence, source_note.
    All score fields may be None if unavailable.
    """
    sym_upper = symbol.upper().replace("-", "").split(".")[0]

    if sym_upper in _FIXED_INCOME_ETFS:
        return _no_data(symbol, "fixed_income_etf")

    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        sustainability = ticker.sustainability
    except Exception as exc:
        logger.debug("yfinance ESG fetch failed for %s: %s", symbol, exc)
        return _sector_fallback(symbol, sector_override)

    if sustainability is None or sustainability.empty:
        return _sector_fallback(symbol, sector_override)

    # yfinance returns a DataFrame indexed by metric name
    def _get(key: str) -> float | None:
        try:
            val = sustainability.loc[key, "Value"]  # type: ignore[arg-type]
            if val is None:
                return None
            return float(val)
        except (KeyError, TypeError, ValueError):
            return None

    overall = _get("totalEsg")
    e_score = _get("environmentScore")
    s_score = _get("socialScore")
    g_score = _get("governanceScore")
    controversy = _get("highestControversy")

    if overall is None and e_score is None:
        return _sector_fallback(symbol, sector_override)

    return {
        "overall": overall,
        "e_score": e_score,
        "s_score": s_score,
        "g_score": g_score,
        "controversy_level": int(controversy) if controversy is not None else None,
        "provider": "yfinance",
        "confidence": "high",
        "source_note": "yfinance sustainability",
    }


def _no_data(symbol: str, reason: str = "") -> dict[str, Any]:
    return {
        "overall": None,
        "e_score": None,
        "s_score": None,
        "g_score": None,
        "controversy_level": None,
        "provider": "none",
        "confidence": "none",
        "source_note": reason or "no data",
    }


def _sector_fallback(symbol: str, sector: str | None) -> dict[str, Any]:
    if not sector:
        return _no_data(symbol, "no_sector")
    sector_lower = sector.lower()
    for key, scores in _SECTOR_FALLBACK.items():
        if key in sector_lower:
            return {
                "overall": scores["overall"],
                "e_score": scores["e"],
                "s_score": scores["s"],
                "g_score": scores["g"],
                "controversy_level": None,
                "provider": "sector_fallback",
                "confidence": "low",
                "source_note": f"sector default ({key})",
            }
    return _no_data(symbol, "sector_unknown")
