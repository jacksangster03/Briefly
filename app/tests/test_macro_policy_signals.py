from __future__ import annotations

from app.briefing.macro_policy_signals import build_policy_signals


def _payload(*, cpi=2.2, core=2.3, pce=2.2, core_pce=2.3, unrate=4.5, claims=260000, wage=2.9, two_chg=-0.05, ten_chg=-0.04):
    return {
        "central_bank_policy": {"series": {"ecb": {"status": "ok"}}},
        "inflation_tracker": {
            "series": {
                "us_cpi": {"value": cpi, "status": "ok"},
                "us_core_cpi": {"value": core, "status": "ok"},
                "us_pce": {"value": pce, "status": "ok"},
                "us_core_pce": {"value": core_pce, "status": "ok"},
            }
        },
        "labour_tracker": {
            "series": {
                "us_unemployment_rate": {"value": unrate, "status": "ok"},
                "us_initial_claims": {"value": claims, "status": "ok"},
                "us_wage_growth": {"value": wage, "status": "ok"},
                "us_payrolls": {"change": -40, "status": "ok"},
                "us_jolts_openings": {"change": -20, "status": "ok"},
            }
        },
        "rates_yield_curve_panel": {
            "series": {
                "us_2y": {"change": two_chg, "status": "ok"},
                "us_10y": {"change": ten_chg, "status": "ok"},
            }
        },
    }


def test_cut_leaning_when_easing_and_cooling():
    sig = build_policy_signals(_payload())
    assert sig["fed_bias"]["label"] == "cut_leaning"


def test_hike_leaning_when_reaccelerating_and_tight():
    sig = build_policy_signals(
        _payload(cpi=3.4, core=3.3, pce=3.1, core_pce=3.2, unrate=3.6, claims=210000, wage=4.4, two_chg=0.05, ten_chg=0.06)
    )
    assert sig["fed_bias"]["label"] in {"hike_leaning", "hold"}
    assert sig["inflation_pressure"]["label"] in {"reaccelerating", "sticky"}


def test_mixed_data_can_map_to_hold():
    sig = build_policy_signals(
        _payload(cpi=2.7, core=2.8, pce=2.4, core_pce=2.7, unrate=4.1, claims=230000, wage=3.5, two_chg=0.00, ten_chg=-0.01)
    )
    assert sig["fed_bias"]["label"] in {"hold", "uncertain"}


def test_missing_data_returns_partial_or_uncertain():
    sig = build_policy_signals({})
    assert sig["status"] in {"partial", "unavailable"}
    assert sig["fed_bias"]["label"] == "uncertain"
