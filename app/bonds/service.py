"""Phase 7A: Fixed income analytics service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import BondHoldingOverride, BondPortfolioSnapshot
from app.db.session import get_session
from app.logger import get_logger

logger = get_logger("bonds")

# Known bond ETF reference data: modified duration (yrs), YTM (%), credit quality
_KNOWN_BOND_ETFS: dict[str, dict[str, Any]] = {
    "AGG":   {"duration": 6.0,  "ytm": 4.6, "quality": "ig"},
    "BND":   {"duration": 6.1,  "ytm": 4.7, "quality": "ig"},
    "BNDX":  {"duration": 7.6,  "ytm": 3.5, "quality": "ig"},
    "EMB":   {"duration": 6.8,  "ytm": 6.5, "quality": "em"},
    "FLOT":  {"duration": 0.1,  "ytm": 5.5, "quality": "ig"},
    "GOVT":  {"duration": 7.0,  "ytm": 4.4, "quality": "govt"},
    "HYG":   {"duration": 3.4,  "ytm": 7.8, "quality": "hy"},
    "HYEM":  {"duration": 3.6,  "ytm": 8.5, "quality": "hy"},
    "IEF":   {"duration": 7.5,  "ytm": 4.3, "quality": "govt"},
    "JNK":   {"duration": 3.5,  "ytm": 8.0, "quality": "hy"},
    "LQD":   {"duration": 8.5,  "ytm": 5.2, "quality": "ig"},
    "MBB":   {"duration": 5.5,  "ytm": 5.0, "quality": "govt"},
    "SHYG":  {"duration": 2.4,  "ytm": 7.6, "quality": "hy"},
    "SHY":   {"duration": 1.9,  "ytm": 4.8, "quality": "govt"},
    "SJNK":  {"duration": 2.5,  "ytm": 7.9, "quality": "hy"},
    "TIP":   {"duration": 7.2,  "ytm": 2.1, "quality": "govt"},
    "TLT":   {"duration": 16.5, "ytm": 4.5, "quality": "govt"},
    "VCIT":  {"duration": 6.6,  "ytm": 5.1, "quality": "ig"},
    "VCSH":  {"duration": 2.7,  "ytm": 5.2, "quality": "ig"},
    "VGIT":  {"duration": 5.1,  "ytm": 4.4, "quality": "govt"},
    "VGLT":  {"duration": 15.8, "ytm": 4.5, "quality": "govt"},
    "VGSH":  {"duration": 1.9,  "ytm": 4.9, "quality": "govt"},
    "BSV":   {"duration": 2.7,  "ytm": 4.9, "quality": "ig"},
    "BIV":   {"duration": 6.5,  "ytm": 4.8, "quality": "ig"},
    "BLV":   {"duration": 14.9, "ytm": 5.1, "quality": "ig"},
    "USHY":  {"duration": 3.3,  "ytm": 7.8, "quality": "hy"},
}

# Fallback assumptions by credit quality bucket
_QUALITY_DEFAULTS: dict[str, dict[str, float]] = {
    "govt": {"duration": 7.0, "ytm": 4.3},
    "ig":   {"duration": 6.5, "ytm": 5.0},
    "hy":   {"duration": 3.5, "ytm": 8.0},
    "em":   {"duration": 6.0, "ytm": 6.5},
}

_QUALITY_LABELS: dict[str, str] = {
    "govt": "Government",
    "ig":   "Investment Grade",
    "hy":   "High Yield",
    "em":   "Emerging Markets",
}

# Buckets that indicate a fixed income holding
_BOND_BUCKETS = {"fixed_income", "bonds", "high_quality_bonds", "credit", "bond"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_bond_holding(symbol: str, bucket: str | None) -> bool:
    if bucket and bucket.lower().replace(" ", "_") in _BOND_BUCKETS:
        return True
    return symbol.upper() in _KNOWN_BOND_ETFS


def _classify_quality(symbol: str, override: dict[str, Any] | None) -> str:
    if override and override.get("credit_quality"):
        return str(override["credit_quality"]).lower()
    etf = _KNOWN_BOND_ETFS.get(symbol.upper())
    if etf:
        return str(etf["quality"])
    return "ig"


def _get_duration(symbol: str, override: dict[str, Any] | None) -> tuple[float, str]:
    """Return (duration_yrs, source_note)."""
    if override and override.get("modified_duration_yrs") is not None:
        return float(override["modified_duration_yrs"]), "manual override"
    etf = _KNOWN_BOND_ETFS.get(symbol.upper())
    if etf:
        return float(etf["duration"]), "reference data"
    quality = _classify_quality(symbol, override)
    default = _QUALITY_DEFAULTS.get(quality, _QUALITY_DEFAULTS["ig"])
    return float(default["duration"]), f"quality default ({quality})"


def _get_ytm(symbol: str, override: dict[str, Any] | None) -> tuple[float, str]:
    """Return (ytm_pct, source_note)."""
    if override and override.get("ytm_override_pct") is not None:
        return float(override["ytm_override_pct"]), "manual override"
    etf = _KNOWN_BOND_ETFS.get(symbol.upper())
    if etf:
        return float(etf["ytm"]), "reference data"
    quality = _classify_quality(symbol, override)
    default = _QUALITY_DEFAULTS.get(quality, _QUALITY_DEFAULTS["ig"])
    return float(default["ytm"]), f"quality default ({quality})"


def _fmt_pct(value: float, digits: int = 2) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def _fmt_val(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _unavailable_block(error: str) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "bond_holding_count": 0,
        "total_bond_weight_pct": 0.0,
        "portfolio_duration_yrs": 0.0,
        "portfolio_ytm_pct": 0.0,
        "rate_sensitivity_pct": 0.0,
        "quality_distribution": {},
        "maturity_distribution": {},
        "holdings_detail": [],
        "summary": "",
        "computed_at": "",
    }


def load_bond_overrides(profile_name: str) -> dict[str, dict[str, Any]]:
    """Load per-holding overrides keyed by symbol."""
    with get_session() as session:
        rows = (
            session.query(BondHoldingOverride)
            .filter(
                BondHoldingOverride.profile_name == profile_name,
                BondHoldingOverride.active.is_(True),
            )
            .all()
        )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[row.symbol.upper()] = {
            "modified_duration_yrs": row.modified_duration_yrs,
            "ytm_override_pct": row.ytm_override_pct,
            "coupon_pct": row.coupon_pct,
            "maturity_date": row.maturity_date.isoformat() if row.maturity_date else None,
            "credit_quality": row.credit_quality,
        }
    return result


def save_bond_override(profile_name: str, symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert a per-holding bond override."""
    now = _utcnow()
    sym = symbol.strip().upper()
    with get_session() as session:
        existing = (
            session.query(BondHoldingOverride)
            .filter(
                BondHoldingOverride.profile_name == profile_name,
                BondHoldingOverride.symbol == sym,
            )
            .first()
        )
        if existing:
            existing.modified_duration_yrs = payload.get("modified_duration_yrs")
            existing.ytm_override_pct = payload.get("ytm_override_pct")
            existing.coupon_pct = payload.get("coupon_pct")
            existing.credit_quality = payload.get("credit_quality")
            existing.active = True
            existing.updated_at = now
        else:
            session.add(
                BondHoldingOverride(
                    profile_name=profile_name,
                    symbol=sym,
                    modified_duration_yrs=payload.get("modified_duration_yrs"),
                    ytm_override_pct=payload.get("ytm_override_pct"),
                    coupon_pct=payload.get("coupon_pct"),
                    credit_quality=payload.get("credit_quality"),
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    return {"symbol": sym, "profile_name": profile_name, **payload}


def delete_bond_override(profile_name: str, symbol: str) -> None:
    """Soft-delete a bond override."""
    sym = symbol.strip().upper()
    with get_session() as session:
        session.query(BondHoldingOverride).filter(
            BondHoldingOverride.profile_name == profile_name,
            BondHoldingOverride.symbol == sym,
        ).update({"active": False, "updated_at": _utcnow()})
        session.commit()


def _classify_maturity(maturity_date_str: str | None) -> str:
    """Bucket a maturity date string into a tenor label."""
    if not maturity_date_str:
        return "unknown"
    try:
        from datetime import date
        mat = date.fromisoformat(maturity_date_str)
        today = date.today()
        years = (mat - today).days / 365.25
        if years < 1:
            return "<1yr"
        if years < 3:
            return "1-3yr"
        if years < 7:
            return "3-7yr"
        if years < 15:
            return "7-15yr"
        return "15yr+"
    except (ValueError, TypeError):
        return "unknown"


def compute_bond_analytics(
    profile_name: str,
    holdings: list[Any],  # list[PortfolioHolding]
    persist: bool = False,
) -> dict[str, Any]:
    """Compute portfolio-level bond analytics from active holdings."""
    overrides = load_bond_overrides(profile_name)

    bond_holdings = [
        h for h in holdings
        if _is_bond_holding(h.symbol, h.bucket)
    ]

    if not bond_holdings:
        return _unavailable_block(
            "No fixed income holdings found. Tag holdings with bucket 'fixed_income' "
            "or add known bond ETFs (BND, AGG, TLT, LQD, HYG, etc.)."
        )

    weighted_holdings = [h for h in bond_holdings if h.weight_pct is not None and h.weight_pct > 0]
    if not weighted_holdings:
        return _unavailable_block(
            "Bond holdings found but none have weights assigned. Add weight_pct to enable analytics."
        )

    total_bond_weight = sum(h.weight_pct for h in weighted_holdings)

    holdings_detail: list[dict[str, Any]] = []
    quality_weights: dict[str, float] = {}
    maturity_weights: dict[str, float] = {}
    weighted_duration_sum = 0.0
    weighted_ytm_sum = 0.0
    source_notes: list[str] = []

    for h in weighted_holdings:
        sym = h.symbol.upper()
        override = overrides.get(sym)
        weight = h.weight_pct / total_bond_weight  # normalised within bond sleeve

        quality = _classify_quality(sym, override)
        duration, dur_source = _get_duration(sym, override)
        ytm, ytm_source = _get_ytm(sym, override)

        maturity_label = _classify_maturity(
            override.get("maturity_date") if override else None
        )

        weighted_duration_sum += weight * duration
        weighted_ytm_sum += weight * ytm
        quality_weights[quality] = quality_weights.get(quality, 0.0) + weight
        maturity_weights[maturity_label] = maturity_weights.get(maturity_label, 0.0) + weight

        source_note = f"{sym}: duration via {dur_source}"
        if source_note not in source_notes:
            source_notes.append(source_note)

        holdings_detail.append({
            "symbol": sym,
            "weight_pct": round(h.weight_pct, 2),
            "weight_in_bond_sleeve_pct": round(weight * 100, 2),
            "duration_yrs": round(duration, 2),
            "ytm_pct": round(ytm, 2),
            "credit_quality": quality,
            "credit_quality_label": _QUALITY_LABELS.get(quality, quality.title()),
            "maturity_bucket": maturity_label,
            "duration_source": dur_source,
            "ytm_source": ytm_source,
        })

    portfolio_duration = weighted_duration_sum
    portfolio_ytm = weighted_ytm_sum
    # rate sensitivity: approximate price change for +100bps parallel shift
    rate_sensitivity_pct = -portfolio_duration * 1.0

    quality_distribution = {
        k: round(v * 100, 2) for k, v in sorted(quality_weights.items())
    }
    maturity_distribution = {
        k: round(v * 100, 2)
        for k, v in sorted(
            maturity_weights.items(),
            key=lambda x: ["<1yr", "1-3yr", "3-7yr", "7-15yr", "15yr+", "unknown"].index(x[0])
            if x[0] in ["<1yr", "1-3yr", "3-7yr", "7-15yr", "15yr+", "unknown"] else 99,
        )
    }

    primary_quality = max(quality_weights, key=quality_weights.get) if quality_weights else "ig"
    primary_label = _QUALITY_LABELS.get(primary_quality, primary_quality.title())
    summary = (
        f"Bond sleeve totals {round(total_bond_weight, 1)}% of the portfolio across "
        f"{len(weighted_holdings)} position(s). "
        f"Modified duration is {_fmt_val(portfolio_duration, 1)} years and portfolio YTM is "
        f"{_fmt_val(portfolio_ytm, 2)}%. "
        f"A +100bps parallel rate shift implies approximately {_fmt_pct(rate_sensitivity_pct, 1)} "
        f"price impact. Dominant credit quality: {primary_label}."
    )

    computed_at = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if persist:
        _persist_snapshot(
            profile_name=profile_name,
            computed_at=computed_at,
            bond_holding_count=len(weighted_holdings),
            total_bond_weight_pct=total_bond_weight,
            portfolio_duration_yrs=portfolio_duration,
            portfolio_ytm_pct=portfolio_ytm,
            rate_sensitivity_pct=rate_sensitivity_pct,
            quality_distribution=quality_distribution,
            maturity_distribution=maturity_distribution,
            holdings_detail=holdings_detail,
            source_notes=source_notes,
        )

    return {
        "available": True,
        "error": None,
        "bond_holding_count": len(weighted_holdings),
        "total_bond_weight_pct": round(total_bond_weight, 2),
        "portfolio_duration_yrs": round(portfolio_duration, 2),
        "portfolio_duration_display": _fmt_val(portfolio_duration, 1),
        "portfolio_ytm_pct": round(portfolio_ytm, 2),
        "portfolio_ytm_display": _fmt_pct(portfolio_ytm, 2),
        "rate_sensitivity_pct": round(rate_sensitivity_pct, 2),
        "rate_sensitivity_display": _fmt_pct(rate_sensitivity_pct, 1),
        "quality_distribution": quality_distribution,
        "maturity_distribution": maturity_distribution,
        "holdings_detail": holdings_detail,
        "summary": summary,
        "computed_at": computed_at,
    }


def load_bond_snapshot_history(profile_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Load recent bond analytics snapshots."""
    with get_session() as session:
        rows = (
            session.query(BondPortfolioSnapshot)
            .filter(
                BondPortfolioSnapshot.profile_name == profile_name,
                BondPortfolioSnapshot.active.is_(True),
            )
            .order_by(BondPortfolioSnapshot.id.desc())
            .limit(limit)
            .all()
        )
    result = []
    for row in rows:
        result.append({
            "computed_at": row.computed_at.strftime("%Y-%m-%d %H:%M") if row.computed_at else "",
            "portfolio_duration_yrs": round(row.portfolio_duration_yrs or 0.0, 2),
            "portfolio_ytm_pct": round(row.portfolio_ytm_pct or 0.0, 2),
            "bond_holding_count": row.bond_holding_count,
        })
    return result


def _persist_snapshot(
    profile_name: str,
    computed_at: str,
    bond_holding_count: int,
    total_bond_weight_pct: float,
    portfolio_duration_yrs: float,
    portfolio_ytm_pct: float,
    rate_sensitivity_pct: float,
    quality_distribution: dict[str, float],
    maturity_distribution: dict[str, float],
    holdings_detail: list[dict[str, Any]],
    source_notes: list[str],
) -> None:
    now = _utcnow()
    try:
        from datetime import datetime as dt
        computed_dt = dt.strptime(computed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        computed_dt = now

    with get_session() as session:
        session.add(
            BondPortfolioSnapshot(
                profile_name=profile_name,
                computed_at=computed_dt,
                bond_holding_count=bond_holding_count,
                total_bond_weight_pct=round(total_bond_weight_pct, 4),
                portfolio_duration_yrs=round(portfolio_duration_yrs, 4),
                portfolio_ytm_pct=round(portfolio_ytm_pct, 4),
                rate_sensitivity_pct=round(rate_sensitivity_pct, 4),
                quality_distribution_json=json.dumps(quality_distribution),
                maturity_distribution_json=json.dumps(maturity_distribution),
                holdings_detail_json=json.dumps(holdings_detail),
                data_source_notes="; ".join(source_notes),
                active=True,
                created_at=now,
            )
        )
        session.commit()
