from app.briefing.regime_context import compute_geo_risk_level
from app.briefing.regime_tracker import classify_regime, persist_regime_snapshot
from app.db.models import RegimeSnapshot
from app.db.session import get_session


def test_classify_regime_risk_off_and_oil_shock():
    decision = classify_regime(
        setup_tags=["defensive", "oil_shock"],
        vix_level=27.2,
        breadth_ratio=0.31,
        oil_delta_pct=4.8,
        ten_y_delta_bps=1.0,
    )
    assert decision.risk_regime == "risk_off"
    assert decision.factor_regime == "oil_shock"
    assert decision.trend_regime in {"divergent", "transition"}


def test_compute_geo_risk_level_extreme():
    level, summary = compute_geo_risk_level(
        vix_level=28.0,
        oil_delta_pct=5.2,
        safe_haven_strength=2.8,
        news_keyword_density=0.70,
    )
    assert level == "EXTREME"
    assert "Geo risk EXTREME" in summary


def test_persist_regime_snapshot_detects_shift(validation_isolated_db):
    base = classify_regime(
        setup_tags=["mixed"],
        vix_level=19.0,
        breadth_ratio=0.50,
        oil_delta_pct=0.8,
        ten_y_delta_bps=0.5,
    )
    shift = persist_regime_snapshot(
        profile_name="default_user",
        decision=base,
        setup_tags=["mixed"],
        vix_level=19.0,
        geo_risk_level="MODERATE",
    )
    assert shift == {}

    changed = classify_regime(
        setup_tags=["risk_on"],
        vix_level=15.2,
        breadth_ratio=0.71,
        oil_delta_pct=0.2,
        ten_y_delta_bps=0.4,
    )
    shift = persist_regime_snapshot(
        profile_name="default_user",
        decision=changed,
        setup_tags=["risk_on"],
        vix_level=15.2,
        geo_risk_level="LOW",
    )
    assert shift.get("risk_regime") == "mixed->risk_on"

    with get_session() as session:
        rows = (
            session.query(RegimeSnapshot)
            .filter(RegimeSnapshot.profile_name == "default_user")
            .order_by(RegimeSnapshot.id.asc())
            .all()
        )
    assert len(rows) == 2
    assert rows[-1].geo_risk_level == "LOW"
