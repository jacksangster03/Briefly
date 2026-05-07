from __future__ import annotations

from app.briefing.market_regions import MarketRegion, infer_market_profile


def test_spain_profile_maps_to_emea() -> None:
    p = infer_market_profile("Spain", None)
    assert p.market_region == MarketRegion.EMEA
    assert p.sub_region == "Eurozone"
    assert p.session_template == "emea_global"
    assert p.timezone == "Europe/Madrid"


def test_us_profile_maps_to_americas() -> None:
    p = infer_market_profile("US", None)
    assert p.market_region == MarketRegion.AMERICAS
    assert p.sub_region == "US"
    assert p.session_template == "americas_global"
    assert p.timezone == "America/New_York"


def test_japan_profile_maps_to_apac() -> None:
    p = infer_market_profile("Japan", None)
    assert p.market_region == MarketRegion.APAC
    assert p.sub_region == "Japan/Korea"
    assert p.session_template == "apac_global"
    assert p.timezone == "Asia/Tokyo"


def test_australia_profile_maps_to_apac_australia_template() -> None:
    p = infer_market_profile("Australia", None)
    assert p.market_region == MarketRegion.APAC
    assert p.sub_region == "Australia/NZ"
    assert p.session_template == "apac_australia"
    assert p.timezone == "Australia/Sydney"
