from __future__ import annotations

from app.briefing.market_regions import MarketRegion, infer_market_profile
from app.briefing.session_templates import get_session_template_for_profile
from app.personalization.user_profile import UserProfile


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


def test_unknown_country_with_europe_timezone_defaults_to_emea() -> None:
    p = infer_market_profile(None, "Europe/Madrid")
    assert p.market_region == MarketRegion.EMEA
    assert p.session_template == "emea_global"


def test_spain_no_override_uses_emea_template() -> None:
    profile = UserProfile(country="Spain", timezone="Europe/Madrid", session_template="emea_global")
    name, _ = get_session_template_for_profile(profile)
    assert name == "emea_global"


def test_spain_override_can_use_americas_template() -> None:
    profile = UserProfile(
        country="Spain",
        timezone="Europe/Madrid",
        session_template="emea_global",
        session_template_override="americas_global",
    )
    name, _ = get_session_template_for_profile(profile)
    assert name == "americas_global"


def test_us_no_override_uses_americas_template() -> None:
    profile = UserProfile(country="US", timezone="America/New_York", session_template="americas_global")
    name, _ = get_session_template_for_profile(profile)
    assert name == "americas_global"


def test_japan_no_override_uses_apac_global() -> None:
    profile = UserProfile(country="Japan", timezone="Asia/Tokyo", session_template="apac_global")
    name, _ = get_session_template_for_profile(profile)
    assert name == "apac_global"


def test_australia_no_override_uses_apac_australia() -> None:
    profile = UserProfile(country="Australia", timezone="Australia/Sydney", session_template="apac_australia")
    name, _ = get_session_template_for_profile(profile)
    assert name == "apac_australia"
