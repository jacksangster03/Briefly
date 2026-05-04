"""Phase 7D: Multi-currency portfolio support."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import CurrencyExposure, FXConfig
from app.db.session import get_session
from app.fx.rates import fetch_fx_rates, get_latest_rate, infer_currency
from app.logger import get_logger

logger = get_logger("fx")

SUPPORTED_CURRENCIES = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "JPY", "HKD", "CNY"]
HEDGE_POLICIES = ["unhedged", "partial", "full"]

_HEDGE_INSTRUMENTS: dict[str, str] = {
    "EUR": "EUO (ProShares UltraShort Euro) or FX forward",
    "GBP": "FXB (Invesco CurrencyShares British Pound) or FX forward",
    "CHF": "FXF (Invesco CurrencyShares Swiss Franc) or FX forward",
    "JPY": "YCS (ProShares UltraShort Yen) or FX forward",
    "CAD": "FXC (Invesco CurrencyShares Canadian Dollar) or FX forward",
    "AUD": "FXA (Invesco CurrencyShares Australian Dollar) or FX forward",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _unavailable_block(error: str) -> dict[str, Any]:
    return {"available": False, "error": error}


# ── FX Config ─────────────────────────────────────────────────────────────────

def load_fx_config(profile_name: str) -> dict[str, Any]:
    """Load FX configuration for a profile."""
    with get_session() as session:
        row = (
            session.query(FXConfig)
            .filter(FXConfig.profile_name == profile_name, FXConfig.active.is_(True))
            .first()
        )
    if not row:
        return {
            "home_currency": "USD",
            "hedge_policy": "unhedged",
            "supported_currencies": SUPPORTED_CURRENCIES,
            "hedge_policies": HEDGE_POLICIES,
        }
    return {
        "home_currency": row.home_currency,
        "hedge_policy": row.hedge_policy,
        "supported_currencies": SUPPORTED_CURRENCIES,
        "hedge_policies": HEDGE_POLICIES,
    }


def save_fx_config(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist FX config (upsert)."""
    home_currency = payload.get("home_currency", "USD").upper()
    if home_currency not in SUPPORTED_CURRENCIES:
        home_currency = "USD"
    hedge_policy = payload.get("hedge_policy", "unhedged")
    if hedge_policy not in HEDGE_POLICIES:
        hedge_policy = "unhedged"
    now = _utcnow()
    with get_session() as session:
        existing = (
            session.query(FXConfig)
            .filter(FXConfig.profile_name == profile_name)
            .first()
        )
        if existing:
            existing.home_currency = home_currency
            existing.hedge_policy = hedge_policy
            existing.updated_at = now
        else:
            session.add(FXConfig(
                profile_name=profile_name,
                home_currency=home_currency,
                hedge_policy=hedge_policy,
                active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()
    return {"saved": True, "home_currency": home_currency, "hedge_policy": hedge_policy}


# ── Exposure computation ──────────────────────────────────────────────────────

def compute_fx_exposure(
    profile_name: str,
    holdings: list[Any],
    home_currency: str | None = None,
    lookback_days: int = 30,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Compute currency exposure for a portfolio.

    holdings: list of PortfolioHolding-like objects with .symbol, .weight_pct.
    Returns per-currency breakdown and home-currency conversion summary.
    """
    if not holdings:
        return _unavailable_block("No holdings.")

    cfg = load_fx_config(profile_name)
    if not home_currency:
        home_currency = cfg["home_currency"]
    home_currency = home_currency.upper()
    hedge_policy = cfg["hedge_policy"]

    total_weight = sum(h.weight_pct for h in holdings if h.weight_pct)
    if total_weight <= 0:
        return _unavailable_block("No valid weights.")

    # Identify foreign currencies needed
    currency_map: dict[str, str] = {}
    for h in holdings:
        ccy = infer_currency(h.symbol)
        currency_map[h.symbol.upper()] = ccy

    foreign_currencies = {c for c in currency_map.values() if c != home_currency}

    # Fetch FX rates for all foreign pairs
    pairs = [(ccy, home_currency) for ccy in foreign_currencies]
    if pairs:
        rate_history = fetch_fx_rates(pairs, lookback_days=lookback_days, force_refresh=force_refresh)
    else:
        rate_history = {}

    def latest_rate(foreign_ccy: str) -> float:
        if foreign_ccy == home_currency:
            return 1.0
        rows = rate_history.get((foreign_ccy, home_currency), [])
        if rows:
            return rows[-1]["rate"]
        # fallback: try DB
        r = get_latest_rate(foreign_ccy, home_currency)
        return r if r else 1.0

    # Build per-currency aggregate
    currency_weights: dict[str, float] = {}
    holdings_detail: list[dict[str, Any]] = []

    for h in holdings:
        sym = h.symbol.upper()
        w = h.weight_pct or 0.0
        foreign_ccy = currency_map.get(sym, "USD")
        rate = latest_rate(foreign_ccy)

        currency_weights[foreign_ccy] = currency_weights.get(foreign_ccy, 0.0) + w
        holdings_detail.append({
            "symbol": sym,
            "weight_pct": w,
            "currency": foreign_ccy,
            "rate_to_home": round(rate, 4),
            "is_foreign": foreign_ccy != home_currency,
        })

    # Normalise to pct
    currency_exposure = [
        {
            "currency": ccy,
            "weight_pct": round(w, 2),
            "is_foreign": ccy != home_currency,
        }
        for ccy, w in sorted(currency_weights.items(), key=lambda x: -x[1])
    ]

    foreign_weight = sum(
        e["weight_pct"] for e in currency_exposure if e["is_foreign"]
    )

    hedge_recommendations = _build_hedge_recommendations(
        currency_exposure, home_currency, hedge_policy
    )

    _persist_currency_exposures(profile_name, holdings_detail)

    return {
        "available": True,
        "home_currency": home_currency,
        "hedge_policy": hedge_policy,
        "foreign_weight_pct": round(foreign_weight, 2),
        "currency_exposure": currency_exposure,
        "holdings_detail": holdings_detail,
        "hedge_recommendations": hedge_recommendations,
        "computed_at": _utcnow().isoformat(),
    }


def _build_hedge_recommendations(
    currency_exposure: list[dict[str, Any]],
    home_currency: str,
    hedge_policy: str,
) -> list[dict[str, Any]]:
    if hedge_policy == "unhedged":
        return []
    recommendations = []
    for entry in currency_exposure:
        ccy = entry["currency"]
        w = entry["weight_pct"]
        if ccy == home_currency or w < 2.0:
            continue
        hedge_pct = w if hedge_policy == "full" else w * 0.5
        instrument = _HEDGE_INSTRUMENTS.get(ccy, f"FX forward on {ccy}/{home_currency}")
        recommendations.append({
            "currency": ccy,
            "exposure_pct": w,
            "recommended_hedge_pct": round(hedge_pct, 2),
            "instrument": instrument,
        })
    return recommendations


def _persist_currency_exposures(profile_name: str, holdings_detail: list[dict[str, Any]]) -> None:
    now = _utcnow()
    for h in holdings_detail:
        try:
            with get_session() as session:
                existing = (
                    session.query(CurrencyExposure)
                    .filter(
                        CurrencyExposure.profile_name == profile_name,
                        CurrencyExposure.symbol == h["symbol"],
                    )
                    .first()
                )
                if existing:
                    existing.foreign_currency = h["currency"]
                    existing.weight_pct_home_currency = h["weight_pct"]
                    existing.updated_at = now
                else:
                    session.add(CurrencyExposure(
                        profile_name=profile_name,
                        symbol=h["symbol"],
                        foreign_currency=h["currency"],
                        weight_pct_home_currency=h["weight_pct"],
                        created_at=now,
                        updated_at=now,
                    ))
                session.commit()
        except Exception as exc:
            logger.debug("CurrencyExposure persist failed for %s: %s", h["symbol"], exc)


def load_currency_exposures(profile_name: str) -> list[dict[str, Any]]:
    """Return cached currency exposures for a profile."""
    with get_session() as session:
        rows = (
            session.query(CurrencyExposure)
            .filter(CurrencyExposure.profile_name == profile_name)
            .all()
        )
    return [
        {
            "symbol": r.symbol,
            "currency": r.foreign_currency,
            "weight_pct": r.weight_pct_home_currency,
        }
        for r in rows
    ]
