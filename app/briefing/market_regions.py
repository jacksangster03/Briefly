"""Country/region inference for session templates."""

from __future__ import annotations

from dataclasses import dataclass


class MarketRegion:
    APAC = "APAC"
    EMEA = "EMEA"
    AMERICAS = "Americas"


class SubRegion:
    APAC_AU_NZ = "Australia/NZ"
    APAC_JP_KR = "Japan/Korea"
    APAC_CN_HK = "China/Hong Kong"
    APAC_SG_ASEAN = "Singapore/ASEAN"
    APAC_INDIA = "India"

    EMEA_UK = "UK"
    EMEA_EUROZONE = "Eurozone"
    EMEA_CH_NORDICS = "Switzerland/Nordics"
    EMEA_MIDDLE_EAST = "Middle East"
    EMEA_AFRICA = "Africa"

    AMER_US = "US"
    AMER_CANADA = "Canada"
    AMER_LATAM = "LatAm"


@dataclass(frozen=True)
class CountryMarketProfile:
    country: str
    timezone: str
    market_region: str
    sub_region: str
    session_template: str
    session_intensity: str = "standard"


COUNTRY_MARKET_PROFILES: dict[str, CountryMarketProfile] = {
    "spain": CountryMarketProfile("Spain", "Europe/Madrid", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global"),
    "uk": CountryMarketProfile("UK", "Europe/London", MarketRegion.EMEA, SubRegion.EMEA_UK, "emea_global"),
    "france": CountryMarketProfile("France", "Europe/Paris", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global"),
    "germany": CountryMarketProfile("Germany", "Europe/Berlin", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global"),
    "italy": CountryMarketProfile("Italy", "Europe/Rome", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global"),
    "netherlands": CountryMarketProfile("Netherlands", "Europe/Amsterdam", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global"),
    "switzerland": CountryMarketProfile("Switzerland", "Europe/Zurich", MarketRegion.EMEA, SubRegion.EMEA_CH_NORDICS, "emea_global"),
    "sweden": CountryMarketProfile("Sweden", "Europe/Stockholm", MarketRegion.EMEA, SubRegion.EMEA_CH_NORDICS, "emea_global"),
    "uae": CountryMarketProfile("UAE", "Asia/Dubai", MarketRegion.EMEA, SubRegion.EMEA_MIDDLE_EAST, "emea_global"),
    "saudi arabia": CountryMarketProfile("Saudi Arabia", "Asia/Riyadh", MarketRegion.EMEA, SubRegion.EMEA_MIDDLE_EAST, "emea_global"),
    "south africa": CountryMarketProfile("South Africa", "Africa/Johannesburg", MarketRegion.EMEA, SubRegion.EMEA_AFRICA, "emea_global"),
    "us": CountryMarketProfile("US", "America/New_York", MarketRegion.AMERICAS, SubRegion.AMER_US, "americas_global"),
    "united states": CountryMarketProfile("US", "America/New_York", MarketRegion.AMERICAS, SubRegion.AMER_US, "americas_global"),
    "canada": CountryMarketProfile("Canada", "America/Toronto", MarketRegion.AMERICAS, SubRegion.AMER_CANADA, "americas_global"),
    "mexico": CountryMarketProfile("Mexico", "America/Mexico_City", MarketRegion.AMERICAS, SubRegion.AMER_LATAM, "americas_global"),
    "brazil": CountryMarketProfile("Brazil", "America/Sao_Paulo", MarketRegion.AMERICAS, SubRegion.AMER_LATAM, "americas_global"),
    "argentina": CountryMarketProfile("Argentina", "America/Argentina/Buenos_Aires", MarketRegion.AMERICAS, SubRegion.AMER_LATAM, "americas_global"),
    "chile": CountryMarketProfile("Chile", "America/Santiago", MarketRegion.AMERICAS, SubRegion.AMER_LATAM, "americas_global"),
    "japan": CountryMarketProfile("Japan", "Asia/Tokyo", MarketRegion.APAC, SubRegion.APAC_JP_KR, "apac_global"),
    "south korea": CountryMarketProfile("South Korea", "Asia/Seoul", MarketRegion.APAC, SubRegion.APAC_JP_KR, "apac_global"),
    "china": CountryMarketProfile("China", "Asia/Shanghai", MarketRegion.APAC, SubRegion.APAC_CN_HK, "apac_global"),
    "hong kong": CountryMarketProfile("Hong Kong", "Asia/Hong_Kong", MarketRegion.APAC, SubRegion.APAC_CN_HK, "apac_global"),
    "singapore": CountryMarketProfile("Singapore", "Asia/Singapore", MarketRegion.APAC, SubRegion.APAC_SG_ASEAN, "apac_global"),
    "india": CountryMarketProfile("India", "Asia/Kolkata", MarketRegion.APAC, SubRegion.APAC_INDIA, "apac_global"),
    "australia": CountryMarketProfile("Australia", "Australia/Sydney", MarketRegion.APAC, SubRegion.APAC_AU_NZ, "apac_australia"),
    "new zealand": CountryMarketProfile("New Zealand", "Pacific/Auckland", MarketRegion.APAC, SubRegion.APAC_AU_NZ, "apac_australia"),
    "indonesia": CountryMarketProfile("Indonesia", "Asia/Jakarta", MarketRegion.APAC, SubRegion.APAC_SG_ASEAN, "apac_global"),
    "taiwan": CountryMarketProfile("Taiwan", "Asia/Taipei", MarketRegion.APAC, SubRegion.APAC_CN_HK, "apac_global"),
}


def _profile_for_timezone(timezone_name: str | None) -> CountryMarketProfile:
    tz = (timezone_name or "").strip()
    if tz.startswith("Europe/") or tz.startswith("Africa/") or tz.startswith("Asia/Dubai") or tz.startswith("Asia/Riyadh"):
        return CountryMarketProfile("Unknown", tz or "Europe/Madrid", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global")
    if tz.startswith("America/"):
        return CountryMarketProfile("Unknown", tz or "America/New_York", MarketRegion.AMERICAS, SubRegion.AMER_US, "americas_global")
    if tz.startswith("Asia/") or tz.startswith("Australia/") or tz.startswith("Pacific/"):
        template = "apac_australia" if tz.startswith("Australia/") else "apac_global"
        sub = SubRegion.APAC_AU_NZ if template == "apac_australia" else SubRegion.APAC_SG_ASEAN
        return CountryMarketProfile("Unknown", tz or "Asia/Singapore", MarketRegion.APAC, sub, template)
    return CountryMarketProfile("Unknown", tz or "Europe/Madrid", MarketRegion.EMEA, SubRegion.EMEA_EUROZONE, "emea_global")


def infer_market_profile(country: str | None, timezone: str | None) -> CountryMarketProfile:
    key = (country or "").strip().lower()
    if key and key in COUNTRY_MARKET_PROFILES:
        p = COUNTRY_MARKET_PROFILES[key]
        if timezone:
            return CountryMarketProfile(
                country=p.country,
                timezone=timezone,
                market_region=p.market_region,
                sub_region=p.sub_region,
                session_template=p.session_template,
                session_intensity=p.session_intensity,
            )
        return p
    return _profile_for_timezone(timezone)
