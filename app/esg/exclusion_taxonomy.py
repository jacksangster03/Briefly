"""Hardcoded SRI exclusion lists: symbol-level and sector-level."""

from __future__ import annotations

# Symbols known to operate in excluded industries
_EXCLUSION_SYMBOLS: dict[str, list[str]] = {
    "tobacco": {
        "MO", "PM", "BTI", "LO", "IMBBY", "JAPAY", "VGR", "TPB",
    },
    "weapons": {
        "LMT", "RTX", "NOC", "GD", "BA", "HII", "TDG", "KTOS", "AVAV",
        "LDOS", "CACI", "SAIC", "DRS", "HEI", "TXT",
    },
    "thermal_coal": {
        "BTU", "ARCH", "CEIX", "ARLP", "FELP", "CONSOL", "NACCO",
    },
    "gambling": {
        "LVS", "WYNN", "MGM", "CZR", "PENN", "DKNG", "GENI", "RSI",
        "BALY", "EVRI", "AGS", "SGMS", "IGT",
    },
    "adult_content": {
        "PLBY",
    },
    "fossil_fuels": {
        "XOM", "CVX", "COP", "EOG", "PXD", "MPC", "VLO", "PSX",
        "OXY", "HES", "DVN", "APA", "FANG", "MRO", "HAL", "BKR", "SLB",
    },
}

# GICS sector names that map to an exclusion screen
_SECTOR_EXCLUSIONS: dict[str, list[str]] = {
    "tobacco": ["tobacco"],
    "weapons": ["aerospace & defense"],
    "thermal_coal": ["coal & consumable fuels"],
    "fossil_fuels": ["oil, gas & consumable fuels"],
    "gambling": ["casinos & gaming", "hotels, resorts & cruise lines"],
}

ALL_SCREENS = list(_EXCLUSION_SYMBOLS.keys())


def get_exclusion_flags(symbol: str, sector: str | None = None) -> list[str]:
    """Return list of screen names triggered for this symbol/sector."""
    flags: list[str] = []
    symbol_upper = symbol.upper().replace("-", "").split(".")[0]
    for screen, symbols in _EXCLUSION_SYMBOLS.items():
        if symbol_upper in symbols:
            flags.append(screen)
    if sector:
        sector_lower = sector.lower()
        for screen, sector_patterns in _SECTOR_EXCLUSIONS.items():
            if screen not in flags:
                for pattern in sector_patterns:
                    if pattern in sector_lower:
                        flags.append(screen)
                        break
    return flags
