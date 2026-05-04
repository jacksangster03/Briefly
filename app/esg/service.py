"""Phase 7C: ESG/SRI scoring overlay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import ESGConfig, ESGScore, PortfolioESGSnapshot
from app.db.session import get_session
from app.esg.exclusion_taxonomy import ALL_SCREENS, get_exclusion_flags
from app.esg.providers import fetch_esg_for_symbol
from app.logger import get_logger

logger = get_logger("esg")

_DEFAULT_ACTIVE_SCREENS = ["tobacco", "weapons", "thermal_coal"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _unavailable_block(error: str) -> dict[str, Any]:
    return {"available": False, "error": error}


# ── ESG config ────────────────────────────────────────────────────────────────

def load_esg_config(profile_name: str) -> dict[str, Any]:
    """Load ESG/SRI screening preferences for a profile."""
    with get_session() as session:
        row = (
            session.query(ESGConfig)
            .filter(ESGConfig.profile_name == profile_name, ESGConfig.active.is_(True))
            .first()
        )
    if not row:
        return {
            "active_screens": _DEFAULT_ACTIVE_SCREENS,
            "all_screens": ALL_SCREENS,
        }
    try:
        screens = json.loads(row.enabled_screens_json or "[]")
    except (ValueError, TypeError):
        screens = _DEFAULT_ACTIVE_SCREENS
    return {
        "active_screens": screens,
        "all_screens": ALL_SCREENS,
    }


def save_esg_config(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist ESG screening preferences (upsert)."""
    active_screens = [s for s in payload.get("active_screens", []) if s in ALL_SCREENS]
    now = _utcnow()
    with get_session() as session:
        existing = (
            session.query(ESGConfig)
            .filter(ESGConfig.profile_name == profile_name)
            .first()
        )
        if existing:
            existing.enabled_screens_json = json.dumps(active_screens)
            existing.updated_at = now
        else:
            session.add(ESGConfig(
                profile_name=profile_name,
                enabled_screens_json=json.dumps(active_screens),
                active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()
    return {"saved": True, "active_screens": active_screens}


# ── Per-symbol ESG fetch/cache ─────────────────────────────────────────────────

def fetch_esg_scores(
    symbols_sectors: list[tuple[str, str | None]],
    profile_name: str,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Return ESG scores for each symbol. Caches in ESGScore table.

    symbols_sectors: list of (symbol, sector_override) tuples.
    """
    today = _utcnow().date()
    results: dict[str, dict[str, Any]] = {}

    for symbol, sector in symbols_sectors:
        sym_upper = symbol.upper()

        if not force_refresh:
            with get_session() as session:
                cached = (
                    session.query(ESGScore)
                    .filter(
                        ESGScore.profile_name == profile_name,
                        ESGScore.symbol == sym_upper,
                    )
                    .order_by(ESGScore.as_of_date.desc())
                    .first()
                )
            if cached and str(cached.as_of_date) == str(today):
                try:
                    excl = json.loads(cached.exclusion_flags_json or "[]")
                except (ValueError, TypeError):
                    excl = []
                results[sym_upper] = {
                    "overall": cached.overall_score,
                    "e_score": cached.e_score,
                    "s_score": cached.s_score,
                    "g_score": cached.g_score,
                    "controversy_level": cached.controversy_level,
                    "exclusion_flags": excl,
                    "provider": cached.provider,
                    "confidence": cached.confidence,
                }
                continue

        raw = fetch_esg_for_symbol(symbol, sector)
        excl_flags = get_exclusion_flags(symbol, sector)

        with get_session() as session:
            existing_today = (
                session.query(ESGScore)
                .filter(
                    ESGScore.profile_name == profile_name,
                    ESGScore.symbol == sym_upper,
                    ESGScore.as_of_date == today,
                )
                .first()
            )
            if existing_today:
                existing_today.overall_score = raw["overall"]
                existing_today.e_score = raw["e_score"]
                existing_today.s_score = raw["s_score"]
                existing_today.g_score = raw["g_score"]
                existing_today.exclusion_flags_json = json.dumps(excl_flags)
                existing_today.controversy_level = raw["controversy_level"]
                existing_today.provider = raw["provider"]
                existing_today.confidence = raw["confidence"]
            else:
                session.add(ESGScore(
                    profile_name=profile_name,
                    symbol=sym_upper,
                    as_of_date=today,
                    overall_score=raw["overall"],
                    e_score=raw["e_score"],
                    s_score=raw["s_score"],
                    g_score=raw["g_score"],
                    exclusion_flags_json=json.dumps(excl_flags),
                    controversy_level=raw["controversy_level"],
                    provider=raw["provider"],
                    confidence=raw["confidence"],
                    created_at=_utcnow(),
                ))
            session.commit()

        results[sym_upper] = {
            "overall": raw["overall"],
            "e_score": raw["e_score"],
            "s_score": raw["s_score"],
            "g_score": raw["g_score"],
            "controversy_level": raw["controversy_level"],
            "exclusion_flags": excl_flags,
            "provider": raw["provider"],
            "confidence": raw["confidence"],
        }

    return results


# ── Portfolio-level ESG computation ───────────────────────────────────────────

def compute_portfolio_esg(
    profile_name: str,
    holdings: list[Any],
    persist: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Compute weighted portfolio ESG aggregate.

    holdings: list of PortfolioHolding-like objects with .symbol, .weight_pct,
              .bucket, .sector_override.
    """
    if not holdings:
        return _unavailable_block("No holdings.")

    config = load_esg_config(profile_name)
    active_screens: list[str] = config["active_screens"]

    symbols_sectors = [
        (h.symbol, getattr(h, "sector_override", None))
        for h in holdings
    ]

    esg_scores = fetch_esg_scores(symbols_sectors, profile_name, force_refresh=force_refresh)

    total_weight = sum(h.weight_pct for h in holdings if h.weight_pct)
    if total_weight <= 0:
        return _unavailable_block("No valid weights.")

    weighted_overall = 0.0
    weighted_e = 0.0
    weighted_s = 0.0
    weighted_g = 0.0
    covered_weight = 0.0
    holdings_detail: list[dict[str, Any]] = []
    exclusion_count = 0

    for h in holdings:
        sym = h.symbol.upper()
        w = (h.weight_pct or 0.0) / 100.0
        scores = esg_scores.get(sym, {})
        overall = scores.get("overall")
        e_score = scores.get("e_score")
        s_score = scores.get("s_score")
        g_score = scores.get("g_score")
        excl_flags = scores.get("exclusion_flags", [])
        active_flags = [f for f in excl_flags if f in active_screens]

        if active_flags:
            exclusion_count += 1

        if overall is not None:
            weighted_overall += overall * w
            covered_weight += h.weight_pct or 0.0
        if e_score is not None:
            weighted_e += e_score * w
        if s_score is not None:
            weighted_s += s_score * w
        if g_score is not None:
            weighted_g += g_score * w

        holdings_detail.append({
            "symbol": sym,
            "weight_pct": h.weight_pct,
            "overall": overall,
            "e_score": e_score,
            "s_score": s_score,
            "g_score": g_score,
            "exclusion_flags": active_flags,
            "all_flags": excl_flags,
            "provider": scores.get("provider", "none"),
            "confidence": scores.get("confidence", "none"),
        })

    coverage_pct = (covered_weight / total_weight * 100.0) if total_weight > 0 else 0.0

    sri_label = _sri_alignment_label(
        weighted_overall if covered_weight > 0 else None,
        exclusion_count,
        coverage_pct,
        active_screens,
    )

    result: dict[str, Any] = {
        "available": True,
        "weighted_overall": round(weighted_overall, 1) if covered_weight > 0 else None,
        "weighted_e": round(weighted_e, 1) if covered_weight > 0 else None,
        "weighted_s": round(weighted_s, 1) if covered_weight > 0 else None,
        "weighted_g": round(weighted_g, 1) if covered_weight > 0 else None,
        "coverage_pct": round(coverage_pct, 1),
        "exclusion_count": exclusion_count,
        "sri_alignment_label": sri_label,
        "active_screens": active_screens,
        "holdings_detail": holdings_detail,
        "computed_at": _utcnow().isoformat(),
    }

    if persist:
        _persist_snapshot(profile_name, result)

    return result


def _sri_alignment_label(
    weighted_overall: float | None,
    exclusion_count: int,
    coverage_pct: float,
    active_screens: list[str],
) -> str:
    if coverage_pct < 25.0:
        return "Insufficient Data"
    if exclusion_count > 0:
        return "Weak"
    if weighted_overall is None:
        return "Insufficient Data"
    if weighted_overall >= 60.0 and len(active_screens) >= 3:
        return "Strong"
    if weighted_overall >= 45.0:
        return "Partial"
    return "Weak"


def _persist_snapshot(profile_name: str, metrics: dict[str, Any]) -> None:
    now = _utcnow()
    with get_session() as session:
        row = PortfolioESGSnapshot(
            profile_name=profile_name,
            computed_at=now,
            weighted_overall=metrics.get("weighted_overall"),
            weighted_e=metrics.get("weighted_e"),
            weighted_s=metrics.get("weighted_s"),
            weighted_g=metrics.get("weighted_g"),
            coverage_pct=metrics.get("coverage_pct"),
            exclusion_count=metrics.get("exclusion_count", 0),
            sri_alignment_label=metrics.get("sri_alignment_label"),
            assessment_json=json.dumps(metrics.get("holdings_detail", [])),
            created_at=now,
        )
        session.add(row)
        session.commit()


def load_esg_snapshot_history(profile_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent ESG snapshots for a profile."""
    with get_session() as session:
        rows = (
            session.query(PortfolioESGSnapshot)
            .filter(PortfolioESGSnapshot.profile_name == profile_name)
            .order_by(PortfolioESGSnapshot.id.desc())
            .limit(limit)
            .all()
        )
    return [
        {
            "snapshot_id": r.id,
            "computed_at": r.computed_at,
            "weighted_overall": r.weighted_overall,
            "weighted_e": r.weighted_e,
            "weighted_s": r.weighted_s,
            "weighted_g": r.weighted_g,
            "coverage_pct": r.coverage_pct,
            "exclusion_count": r.exclusion_count,
            "sri_alignment_label": r.sri_alignment_label,
        }
        for r in rows
    ]
