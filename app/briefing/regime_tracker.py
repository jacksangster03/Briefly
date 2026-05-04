"""Deterministic regime tracking with SQLite persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.models import RegimeSnapshot
from app.db.session import get_session


@dataclass
class RegimeDecision:
    risk_regime: str
    trend_regime: str
    factor_regime: str
    trigger_notes: str


def classify_regime(
    *,
    setup_tags: list[str],
    vix_level: float | None,
    breadth_ratio: float | None,
    oil_delta_pct: float | None,
    ten_y_delta_bps: float | None,
) -> RegimeDecision:
    tags = {str(tag).lower().strip() for tag in setup_tags if tag}
    breadth = float(breadth_ratio) if breadth_ratio is not None else 0.5
    vix = float(vix_level) if vix_level is not None else None
    oil = float(oil_delta_pct) if oil_delta_pct is not None else 0.0
    rates = float(ten_y_delta_bps) if ten_y_delta_bps is not None else 0.0

    if "defensive" in tags or (vix is not None and vix >= 25) or breadth < 0.40:
        risk_regime = "risk_off"
    elif "risk_on" in tags or ((vix is None or vix < 18) and breadth >= 0.58):
        risk_regime = "risk_on"
    else:
        risk_regime = "mixed"

    if "regional_split" in tags or "breadth_divergence" in tags:
        trend_regime = "divergent"
    elif "risk_on" in tags and "defensive" not in tags:
        trend_regime = "continuation"
    else:
        trend_regime = "transition"

    if "oil_shock" in tags or abs(oil) >= 3.0:
        factor_regime = "oil_shock"
    elif "rates_led" in tags or abs(rates) >= 3.0:
        factor_regime = "rates_led"
    elif "breadth_divergence" in tags:
        factor_regime = "breadth_divergence"
    elif "regional_split" in tags:
        factor_regime = "regional_split"
    else:
        factor_regime = "balanced"

    notes = (
        f"tags={','.join(sorted(tags)) or 'none'} | "
        f"vix={vix if vix is not None else 'n/a'} | "
        f"breadth={breadth:.2f} | oil={oil:+.2f}% | rates={rates:+.2f}bp"
    )
    return RegimeDecision(
        risk_regime=risk_regime,
        trend_regime=trend_regime,
        factor_regime=factor_regime,
        trigger_notes=notes,
    )


def persist_regime_snapshot(
    *,
    profile_name: str,
    decision: RegimeDecision,
    setup_tags: list[str],
    vix_level: float | None,
    hy_oas: float | None = None,
    geo_risk_level: str = "",
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    with get_session() as session:
        previous = (
            session.query(RegimeSnapshot)
            .filter(RegimeSnapshot.profile_name == profile_name)
            .order_by(RegimeSnapshot.timestamp.desc(), RegimeSnapshot.id.desc())
            .first()
        )
        row = RegimeSnapshot(
            profile_name=profile_name,
            timestamp=now,
            risk_regime=decision.risk_regime,
            trend_regime=decision.trend_regime,
            factor_regime=decision.factor_regime,
            vix=vix_level,
            hy_oas=hy_oas,
            trigger_notes=decision.trigger_notes,
            setup_tags=setup_tags,
            geo_risk_level=geo_risk_level or None,
        )
        session.add(row)

    shift: dict[str, str] = {}
    if previous is not None:
        if previous.risk_regime != decision.risk_regime:
            shift["risk_regime"] = f"{previous.risk_regime}->{decision.risk_regime}"
        if previous.factor_regime != decision.factor_regime:
            shift["factor_regime"] = f"{previous.factor_regime}->{decision.factor_regime}"
        if previous.trend_regime != decision.trend_regime:
            shift["trend_regime"] = f"{previous.trend_regime}->{decision.trend_regime}"
    return shift
