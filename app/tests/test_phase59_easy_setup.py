from __future__ import annotations

from app.onboarding.easy_setup import EasySetupInputs, build_plan, default_inputs


def test_easy_setup_plan_contains_all_required_payloads():
    inputs = default_inputs()
    plan = build_plan(inputs, holdings=[])
    assert plan["policy"]["target_return_percent"] is not None
    assert len(plan["allocation"]) >= 5
    assert plan["benchmark"]["base_symbol"]
    assert len(plan["cma_entries"]) >= 5
    assert len(plan["cma_correlations"]) >= 3
    assert "risk.lookback_days" in plan["risk_preferences"]
    assert "risk.risk_free_rate_pct" in plan["risk_preferences"]
    assert plan["rebalancing"]["method"] in {"drift_threshold", "calendar"}


def test_easy_setup_loss_averse_tightens_policy_constraints():
    base = EasySetupInputs(
        investor_type="long_term_individual",
        horizon_bucket="7_15",
        risk_comfort="medium",
        base_currency="EUR",
        home_region="global",
        has_holdings_file=False,
        infer_from_holdings=False,
        starting_mix="growth",
        loss_averse=False,
        concentration_tolerant=False,
        auto_rebalancing=True,
    )
    cautious = EasySetupInputs(**{**base.__dict__, "loss_averse": True})
    plan_base = build_plan(base, holdings=[])
    plan_cautious = build_plan(cautious, holdings=[])
    assert plan_cautious["policy"]["max_volatility_percent"] <= plan_base["policy"]["max_volatility_percent"]
    assert plan_cautious["policy"]["max_drawdown_percent"] <= plan_base["policy"]["max_drawdown_percent"]


def test_easy_setup_concentration_toggle_relaxes_single_name_cap():
    base = default_inputs()
    strict = build_plan(EasySetupInputs(**{**base.__dict__, "concentration_tolerant": False}), holdings=[])
    relaxed = build_plan(EasySetupInputs(**{**base.__dict__, "concentration_tolerant": True}), holdings=[])
    assert relaxed["policy"]["single_name_limit_percent"] > strict["policy"]["single_name_limit_percent"]
