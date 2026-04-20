"""Recent-regime continuity summaries for morning briefing."""

from __future__ import annotations

from app.db.models import RiskMetricsSnapshot
from app.db.session import get_session


def build_regime_context(
    *,
    profile_name: str,
    current_setup_tags: list[str],
) -> tuple[str, str]:
    """Return (regime_context, positioning_alignment)."""
    latest, previous = _latest_two_risk_snapshots(profile_name)
    if latest is None:
        regime = "Regime context: insufficient risk history; using current setup signals only."
    elif previous is None:
        regime = "Regime context: first risk snapshot recorded; treat today as baseline."
    else:
        vol_delta = (latest.volatility_pct or 0.0) - (previous.volatility_pct or 0.0)
        dd_delta = (latest.max_drawdown_pct or 0.0) - (previous.max_drawdown_pct or 0.0)
        if abs(vol_delta) < 0.25 and abs(dd_delta) < 0.25:
            regime = "Regime context: broad continuation versus recent sessions."
        else:
            regime = (
                f"Regime context: divergence versus recent sessions "
                f"(vol {vol_delta:+.2f}pp, drawdown {dd_delta:+.2f}pp)."
            )

    alignment = _alignment_label(current_setup_tags=current_setup_tags, latest=latest)
    return regime, alignment


def _latest_two_risk_snapshots(profile_name: str) -> tuple[RiskMetricsSnapshot | None, RiskMetricsSnapshot | None]:
    with get_session() as session:
        rows = (
            session.query(RiskMetricsSnapshot)
            .filter(RiskMetricsSnapshot.profile_name == profile_name)
            .order_by(RiskMetricsSnapshot.computed_at.desc(), RiskMetricsSnapshot.id.desc())
            .limit(2)
            .all()
        )
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    return latest, previous


def _alignment_label(*, current_setup_tags: list[str], latest: RiskMetricsSnapshot | None) -> str:
    if latest is None:
        return "Positioning alignment: mixed (not enough history yet)."
    high_risk = (latest.volatility_pct or 0.0) > 18.0 or (latest.max_drawdown_pct or 0.0) < -12.0
    if high_risk and "risk_on" in current_setup_tags:
        return "Positioning alignment: mixed — risk metrics remain elevated despite supportive tape."
    if high_risk:
        return "Positioning alignment: defensive conditions still visible in risk metrics."
    if "risk_on" in current_setup_tags:
        return "Positioning alignment: broadly aligned with a constructive regime."
    if "risk_off" in current_setup_tags:
        return "Positioning alignment: cautious posture remains appropriate."
    return "Positioning alignment: mixed — monitor confirmation from risk and breadth."
