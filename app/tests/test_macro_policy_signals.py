from __future__ import annotations

from app.briefing.macro_policy_signals import build_policy_signals


def _payload(*, cpi=2.2, core=2.3, pce=2.2, core_pce=2.3, unrate=4.5, claims=260000, wage=2.9, two_chg=-0.05, ten_chg=-0.04):
    return {
        "central_bank_policy": {"series": {"ecb": {"status": "ok", "value": 2.0}}},
        "inflation_tracker": {
            "series": {
                "us_cpi": {"value": cpi, "status": "ok"},
                "us_core_cpi": {"value": core, "status": "ok"},
                "us_pce": {"value": pce, "status": "ok"},
                "us_core_pce": {"value": core_pce, "status": "ok"},
                "eurozone_hicp": {"value": 2.1, "status": "ok"},
            }
        },
        "labour_tracker": {
            "series": {
                "us_unemployment_rate": {"value": unrate, "status": "ok"},
                "us_initial_claims": {"value": claims, "status": "ok"},
                "us_wage_growth": {"value": wage, "status": "ok"},
                "us_payrolls": {"change": -40, "status": "ok"},
                "us_jolts_openings": {"change": -20, "status": "ok"},
                "euro_area_unemployment_rate": {"value": 6.7, "status": "ok"},
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


def test_fed_and_ecb_drivers_not_identical_with_us_only_rich():
    payload = _payload()
    payload["inflation_tracker"]["series"].pop("eurozone_hicp", None)
    payload["labour_tracker"]["series"].pop("euro_area_unemployment_rate", None)
    sig = build_policy_signals(payload)
    fed_drivers = sig["fed_bias"]["drivers"]
    ecb_drivers = sig["ecb_bias"]["drivers"]
    assert fed_drivers != ecb_drivers
    assert sig["ecb_bias"]["label"] == "uncertain"
    assert "eurozone_hicp" in sig["ecb_bias"]["missing"]


def test_ecb_uses_eurozone_hicp_when_available():
    sig = build_policy_signals(_payload())
    joined = " ".join(sig["ecb_bias"]["drivers"]).lower()
    assert "eurozone hicp" in joined
    assert sig["ecb_bias"]["label"] in {"cut_leaning", "hold", "hike_leaning", "uncertain"}


def test_ecb_missing_labour_does_not_claim_softer_labour_backdrop():
    payload = _payload()
    payload["labour_tracker"]["series"].pop("euro_area_unemployment_rate", None)
    sig = build_policy_signals(payload)
    joined = " ".join(sig["ecb_bias"]["drivers"]).lower()
    assert "softer labour backdrop" not in joined
    assert "labour confirmation is unavailable" in joined
    assert "euro_area_unemployment_rate" in sig["ecb_bias"]["missing"]
    assert sig["status"] == "partial"
    assert sig["ecb_bias"]["confidence"] == "low"


def test_regions_schema_includes_placeholders_and_spain_country_lens():
    sig = build_policy_signals(_payload())
    regions = sig["regions"]
    assert set(("us", "eurozone", "uk", "japan", "china", "spain")).issubset(set(regions.keys()))
    assert regions["uk"]["status"] == "unavailable"
    assert regions["japan"]["status"] == "unavailable"
    assert regions["china"]["status"] == "unavailable"
    assert regions["spain"]["scope"] == "country_lens"
    assert regions["spain"]["country_lens"]["label"] == "country_macro_lens"


def test_backward_compatibility_top_level_bias_fields_remain():
    sig = build_policy_signals(_payload())
    assert "fed_bias" in sig
    assert "ecb_bias" in sig
    assert "regions" in sig
    assert "global_summary" in sig


def test_uk_region_uses_uk_inputs_and_not_us_as_direct_driver():
    payload = _payload(cpi=3.9, core=4.0, pce=3.8, core_pce=3.9, unrate=3.6, claims=210000, wage=4.2)
    payload["inflation_tracker"]["series"]["uk_cpi"] = {"value": 2.2, "status": "ok"}
    payload["labour_tracker"]["series"]["uk_unemployment_rate"] = {"value": 4.9, "status": "ok"}
    payload["central_bank_policy"]["series"]["boe"] = {"value": 4.5, "status": "ok"}
    sig = build_policy_signals(payload)
    uk = sig["regions"]["uk"]
    assert uk["status"] in {"ok", "partial"}
    joined = " ".join(uk.get("drivers", [])).lower()
    assert "uk" in joined
    assert "us unemployment" not in joined


def test_uk_partial_when_labour_and_policy_missing():
    payload = _payload()
    payload["inflation_tracker"]["series"]["uk_cpi"] = {"value": 2.1, "status": "ok"}
    payload["labour_tracker"]["series"].pop("uk_unemployment_rate", None)
    payload["central_bank_policy"]["series"].pop("boe", None)
    sig = build_policy_signals(payload)
    uk = sig["regions"]["uk"]
    assert uk["status"] == "partial"
    assert "uk_unemployment_rate" in uk["missing"]
    assert "boe_policy_rate" in uk["missing"]


def test_spain_is_country_lens_with_ecb_anchor():
    sig = build_policy_signals(_payload())
    spain = sig["regions"]["spain"]
    assert spain["scope"] == "country_lens"
    assert spain["policy_anchor"] == "ECB"
    assert "policy_bias" not in spain
    assert spain["country_lens"]["label"] == "country_macro_lens"


def test_japan_china_placeholders_do_not_crash_and_are_explicit():
    sig = build_policy_signals(_payload())
    assert sig["regions"]["japan"]["status"] in {"unavailable", "partial", "ok"}
    assert sig["regions"]["china"]["status"] in {"unavailable", "partial", "ok"}
