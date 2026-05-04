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


_GEO_LEVEL_ORDER = ["LOW", "MODERATE", "ELEVATED", "HIGH", "EXTREME", "N/A"]


def compute_geo_risk_level(
    *,
    vix_level: float | None,
    oil_delta_pct: float | None,
    safe_haven_strength: float | None,
    news_keyword_density: float | None,
) -> tuple[str, str]:
    """Return (geo_risk_level, summary) using deterministic scalar thresholds.

    Pass news_keyword_density=None when no event data is available; the function
    will skip the density component and label the signal as stale rather than 'low'.
    """
    score = 0.0
    if vix_level is not None:
        if vix_level >= 26:
            score += 2.2
        elif vix_level >= 20:
            score += 1.4
        elif vix_level >= 16:
            score += 0.7

    oil_move = abs(float(oil_delta_pct or 0.0))
    if oil_move >= 5.0:
        score += 2.0
    elif oil_move >= 3.0:
        score += 1.3
    elif oil_move >= 1.5:
        score += 0.6

    haven = abs(float(safe_haven_strength or 0.0))
    if haven >= 2.5:
        score += 1.4
    elif haven >= 1.2:
        score += 0.8
    elif haven >= 0.6:
        score += 0.4

    density_stale = news_keyword_density is None
    density = float(news_keyword_density) if not density_stale else 0.0
    if not density_stale:
        if density >= 0.60:
            score += 1.8
        elif density >= 0.35:
            score += 1.0
        elif density >= 0.15:
            score += 0.5

    if score >= 5.5:
        level = "EXTREME"
    elif score >= 4.0:
        level = "HIGH"
    elif score >= 2.6:
        level = "ELEVATED"
    elif score >= 1.4:
        level = "MODERATE"
    else:
        level = "LOW"

    density_note = "headline signal stale (no events)" if density_stale else f"density {density:.2f}"
    summary = (
        f"Geo risk {level}: VIX {vix_level if vix_level is not None else 'n/a'}, "
        f"oil {float(oil_delta_pct or 0.0):+.2f}%, haven {float(safe_haven_strength or 0.0):+.2f}, "
        f"{density_note}."
    )
    return level, summary
