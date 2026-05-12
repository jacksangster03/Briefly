"""Profile-aware FX basket builder.

Selects the appropriate set of currency pairs based on the user's
home region, base currency, and optional market context (e.g. oil shock).
All logic is deterministic: no LLM calls, no forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FXInstrument:
    """Represents a single FX pair or index instrument."""

    label: str           # e.g. "EUR/USD"
    symbol: str          # e.g. "EURUSD=X" (yfinance) or "DTWEXBGS" (FRED)
    source: str          # "yfinance" | "fred" | "ecb"
    base: str            # base currency, e.g. "EUR"
    quote: str           # quote currency, e.g. "USD"
    is_dxy_proxy: bool   # True for trade-weighted USD / DXY proxy
    optional: bool       # True for conditionally included pairs
    condition: str | None  # e.g. "oil_shock" for trigger condition


# ---------------------------------------------------------------------------
# Pre-built instrument definitions
# ---------------------------------------------------------------------------

_EUR_USD = FXInstrument(
    label="EUR/USD", symbol="EURUSD=X", source="yfinance",
    base="EUR", quote="USD", is_dxy_proxy=False, optional=False, condition=None,
)
_GBP_USD = FXInstrument(
    label="GBP/USD", symbol="GBPUSD=X", source="yfinance",
    base="GBP", quote="USD", is_dxy_proxy=False, optional=False, condition=None,
)
_EUR_GBP = FXInstrument(
    label="EUR/GBP", symbol="EURGBP=X", source="yfinance",
    base="EUR", quote="GBP", is_dxy_proxy=False, optional=False, condition=None,
)
_USD_JPY = FXInstrument(
    label="USD/JPY", symbol="USDJPY=X", source="yfinance",
    base="USD", quote="JPY", is_dxy_proxy=False, optional=False, condition=None,
)
_USD_CNH = FXInstrument(
    label="USD/CNH", symbol="USDCNH=X", source="yfinance",
    base="USD", quote="CNH", is_dxy_proxy=False, optional=False, condition=None,
)
_AUD_USD = FXInstrument(
    label="AUD/USD", symbol="AUDUSD=X", source="yfinance",
    base="AUD", quote="USD", is_dxy_proxy=False, optional=False, condition=None,
)
_DXY_PROXY = FXInstrument(
    label="Trade-weighted USD", symbol="DTWEXBGS", source="fred",
    base="USD", quote="BASKET", is_dxy_proxy=True, optional=False, condition=None,
)
_USD_CAD = FXInstrument(
    label="USD/CAD", symbol="USDCAD=X", source="yfinance",
    base="USD", quote="CAD", is_dxy_proxy=False, optional=True, condition=None,
)
_EUR_CHF = FXInstrument(
    label="EUR/CHF", symbol="EURCHF=X", source="yfinance",
    base="EUR", quote="CHF", is_dxy_proxy=False, optional=True, condition=None,
)
_USD_NOK = FXInstrument(
    label="USD/NOK", symbol="USDNOK=X", source="yfinance",
    base="USD", quote="NOK", is_dxy_proxy=False, optional=True, condition="oil_shock",
)
_AUD_USD_OPT = FXInstrument(
    label="AUD/USD", symbol="AUDUSD=X", source="yfinance",
    base="AUD", quote="USD", is_dxy_proxy=False, optional=True, condition="commodity_shock",
)
_GBP_JPY = FXInstrument(
    label="GBP/JPY", symbol="GBPJPY=X", source="yfinance",
    base="GBP", quote="JPY", is_dxy_proxy=False, optional=True, condition=None,
)


def _resolve_optional(instrument: FXInstrument, context: dict | None) -> bool:
    """Return True if an optional instrument should be included given context."""
    ctx = context or {}
    if instrument.condition is None:
        # Soft-optional: always include by default
        return True
    if instrument.condition == "oil_shock":
        return ctx.get("oil_shock") is True or ctx.get("commodity_shock") == "oil"
    if instrument.condition == "commodity_shock":
        return ctx.get("commodity_shock") is not None
    return False


def build_fx_basket(
    profile: dict,
    settings: dict,
    context: dict | None = None,
) -> list[FXInstrument]:
    """Build a profile-aware FX instrument basket.

    Parameters
    ----------
    profile:
        User profile dict. Reads home_region, base_currency, market_focus,
        market_region.
    settings:
        Application settings dict. Not currently used but reserved for
        future provider-availability checks.
    context:
        Optional market context dict. May contain commodity_shock or
        oil_shock keys that unlock optional instruments.

    Returns
    -------
    list[FXInstrument]
        Ordered list of instruments to fetch, with optional instruments
        resolved according to current context.
    """
    home_region = (
        profile.get("home_region")
        or profile.get("region")
        or "default"
    ).lower().strip()

    _SPAIN_EUROZONE_REGIONS = {"spain", "eurozone", "emea", "europe", "euro area"}
    _US_REGIONS = {"us", "united states", "americas", "north america"}
    _UK_REGIONS = {"uk", "united kingdom", "great britain"}
    _APAC_REGIONS = {"apac", "asia", "japan", "australia", "asia-pacific"}

    if home_region in _SPAIN_EUROZONE_REGIONS:
        core = [_EUR_USD, _DXY_PROXY, _EUR_GBP, _USD_JPY, _USD_CNH]
        optional_candidates = [_EUR_CHF, _USD_NOK, _AUD_USD_OPT]

    elif home_region in _US_REGIONS:
        core = [_DXY_PROXY, _EUR_USD, _USD_JPY, _GBP_USD, _USD_CNH]
        optional_candidates = [_USD_CAD]

    elif home_region in _UK_REGIONS:
        core = [_GBP_USD, _EUR_GBP, _DXY_PROXY, _USD_JPY]
        optional_candidates = [_GBP_JPY]

    elif home_region in _APAC_REGIONS:
        core = [_USD_JPY, _USD_CNH, _AUD_USD, _DXY_PROXY]
        optional_candidates = [_EUR_USD]

    else:
        # Default fallback basket
        core = [_EUR_USD, _DXY_PROXY, _USD_JPY, _GBP_USD]
        optional_candidates = []

    result = list(core)
    for instrument in optional_candidates:
        if _resolve_optional(instrument, context):
            result.append(instrument)

    return result
