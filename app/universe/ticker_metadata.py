"""Lightweight ticker-to-name metadata for user-facing display labels.

This keeps message formatting readable without introducing provider calls
inside the formatter path. Also exposes a company-name → ticker extractor
used upstream by ``_resolve_event_tickers`` to recover tickers when a
provider (typically Finnhub's ``related`` field) returns nothing.
"""

from __future__ import annotations

import re

TICKER_DISPLAY_NAMES: dict[str, str] = {
    "AAPL": "Apple",
    "ABBV": "AbbVie",
    "ABT": "Abbott",
    "ACN": "Accenture",
    "ADBE": "Adobe",
    "AMD": "Advanced Micro Devices",
    "AEP": "American Electric Power",
    "AMAT": "Applied Materials",
    "AMGN": "Amgen",
    "AMT": "American Tower",
    "AMZN": "Amazon",
    "ASML": "ASML",
    "AVB": "AvalonBay",
    "AVGO": "Broadcom",
    "AXP": "American Express",
    "BA": "Boeing",
    "BAC": "Bank of America",
    "BB": "BlackBerry",
    "BIIB": "Biogen",
    "BKNG": "Booking",
    "BLK": "BlackRock",
    "BMY": "Bristol Myers Squibb",
    "CAT": "Caterpillar",
    "CCI": "Crown Castle",
    "CEG": "Constellation Energy",
    "CHTR": "Charter",
    "CL": "Colgate-Palmolive",
    "CMCSA": "Comcast",
    "CMG": "Chipotle",
    "COP": "ConocoPhillips",
    "COST": "Costco",
    "CRM": "Salesforce",
    "CRWD": "CrowdStrike",
    "CSCO": "Cisco",
    "CVX": "Chevron",
    "D": "Dominion Energy",
    "DAL": "Delta Air Lines",
    "DE": "Deere",
    "DHR": "Danaher",
    "DIS": "Disney",
    "DLR": "Digital Realty",
    "DOW": "Dow",
    "DUK": "Duke Energy",
    "EA": "Electronic Arts",
    "ECL": "Ecolab",
    "ED": "Consolidated Edison",
    "EOG": "EOG Resources",
    "EQIX": "Equinix",
    "EXC": "Exelon",
    "FCX": "Freeport-McMoRan",
    "GE": "GE Aerospace",
    "GILD": "Gilead",
    "GOOG": "Alphabet",
    "GOOGL": "Alphabet",
    "GS": "Goldman Sachs",
    "HD": "Home Depot",
    "HON": "Honeywell",
    "IBM": "IBM",
    "INTC": "Intel",
    "INTU": "Intuit",
    "JNJ": "Johnson & Johnson",
    "JPM": "JPMorgan",
    "KHC": "Kraft Heinz",
    "KO": "Coca-Cola",
    "LIN": "Linde",
    "LLY": "Eli Lilly",
    "LMT": "Lockheed Martin",
    "LOW": "Lowe's",
    "LRCX": "Lam Research",
    "MCD": "McDonald's",
    "MDLZ": "Mondelez",
    "META": "Meta",
    "MMM": "3M",
    "MO": "Altria",
    "MPC": "Marathon Petroleum",
    "MRK": "Merck",
    "MS": "Morgan Stanley",
    "MSFT": "Microsoft",
    "MU": "Micron",
    "NEE": "NextEra Energy",
    "NFLX": "Netflix",
    "NKE": "Nike",
    "NOW": "ServiceNow",
    "NUE": "Nucor",
    "NVDA": "Nvidia",
    "O": "Realty Income",
    "ORCL": "Oracle",
    "OXY": "Occidental Petroleum",
    "PANW": "Palo Alto Networks",
    "PEP": "PepsiCo",
    "PFE": "Pfizer",
    "PG": "Procter & Gamble",
    "PLD": "Prologis",
    "PLTR": "Palantir",
    "PM": "Philip Morris",
    "PSA": "Public Storage",
    "PSX": "Phillips 66",
    "QCOM": "Qualcomm",
    "REGN": "Regeneron",
    "RTX": "RTX",
    "SBUX": "Starbucks",
    "SCHW": "Charles Schwab",
    "SHOP": "Shopify",
    "SHW": "Sherwin-Williams",
    "SLB": "Schlumberger",
    "SNPS": "Synopsys",
    "SO": "Southern",
    "SPG": "Simon Property Group",
    "SRE": "Sempra",
    "T": "AT&T",
    "TJX": "TJX",
    "TMO": "Thermo Fisher",
    "TMUS": "T-Mobile",
    "TSLA": "Tesla",
    "TXN": "Texas Instruments",
    "UNH": "UnitedHealth",
    "UNP": "Union Pacific",
    "UPS": "UPS",
    "USB": "U.S. Bancorp",
    "VRTX": "Vertex",
    "VZ": "Verizon",
    "WELL": "Welltower",
    "WFC": "Wells Fargo",
    "WMT": "Walmart",
    "XEL": "Xcel Energy",
    "XOM": "Exxon Mobil",
}


# Aliases: alternate company names/abbreviations that should resolve to
# a ticker that already exists in TICKER_DISPLAY_NAMES. Populate only with
# unambiguous mappings; anything that could collide with a common English
# word or another company belongs in _AMBIGUOUS_SINGLE_WORD below.
TICKER_ALIASES: dict[str, str] = {
    "TSMC": "TSM",
    "TAIWAN SEMICONDUCTOR": "TSM",
    "ALPHABET": "GOOGL",
    "GOOGLE": "GOOGL",
    "FACEBOOK": "META",
    "META PLATFORMS": "META",
    "EXXON": "XOM",
    "EXXONMOBIL": "XOM",
    "EXXON MOBIL": "XOM",
    "COCA-COLA": "KO",
    "COCA COLA": "KO",
    "PROCTER AND GAMBLE": "PG",
    "PROCTER & GAMBLE": "PG",
    "P&G": "PG",
    "JOHNSON AND JOHNSON": "JNJ",
    "JOHNSON & JOHNSON": "JNJ",
    "J&J": "JNJ",
    "BANK OF AMERICA": "BAC",
    "JPMORGAN CHASE": "JPM",
    "JP MORGAN": "JPM",
    "JPMORGAN": "JPM",
    "GOLDMAN SACHS": "GS",
    "MORGAN STANLEY": "MS",
    "WELLS FARGO": "WFC",
    "ELI LILLY": "LLY",
    "LILLY AND COMPANY": "LLY",
    "BRISTOL MYERS SQUIBB": "BMY",
    "BRISTOL-MYERS SQUIBB": "BMY",
    "LOCKHEED MARTIN": "LMT",
    "HOME DEPOT": "HD",
    "DELTA AIR LINES": "DAL",
    "UNITED HEALTH": "UNH",
    "UNITEDHEALTH": "UNH",
    "THERMO FISHER": "TMO",
    "PHILIP MORRIS": "PM",
    "UNION PACIFIC": "UNP",
    "AMERICAN EXPRESS": "AXP",
    "AMEX": "AXP",
    "SIMON PROPERTY": "SPG",
    "EQUINIX": "EQIX",
    "PROLOGIS": "PLD",
    "MASTERCARD": "MA",
    "VISA INC": "V",
    "BERKSHIRE HATHAWAY": "BRK.B",
    "CROWN CASTLE": "CCI",
    "AMERICAN TOWER": "AMT",
    "BLACKROCK": "BLK",
    "PALO ALTO NETWORKS": "PANW",
    "CROWDSTRIKE": "CRWD",
    "PALANTIR": "PLTR",
    "SERVICENOW": "NOW",
    "APPLIED MATERIALS": "AMAT",
    "LAM RESEARCH": "LRCX",
    "TEXAS INSTRUMENTS": "TXN",
    "QUALCOMM": "QCOM",
    "MICRON": "MU",
    "NVIDIA CORP": "NVDA",
    "NVIDIA CORPORATION": "NVDA",
    "ADVANCED MICRO": "AMD",
    "APPLE INC": "AAPL",
    "MICROSOFT CORP": "MSFT",
    "MICROSOFT CORPORATION": "MSFT",
    "META INC": "META",
    "AMAZON.COM": "AMZN",
}

# Single-word company names where the word is a common English word
# (or a short token that would produce false positives). These are only
# matched via the TICKER_ALIASES map with explicit disambiguating context,
# never matched bare from a title/summary scan.
_AMBIGUOUS_SINGLE_WORD: set[str] = {
    "dow",     # Dow Inc vs Dow Jones index
    "ford",    # common surname
    "oracle",  # generic word
    "visa",    # generic word
    "target",  # generic word
    "gap",     # generic word
    "under",   # (Under Armour)
    "square",  # generic word
}

# Uppercase ticker symbols that would collide with common English words
# or abbreviations if matched bare from a headline. These are still valid
# tickers; they just can't be auto-extracted from freeform text.
_BARE_SYMBOL_EXCLUDE: set[str] = {
    "ALL",
    "ARE",
    "END",
    "HOME",
    "IT",
    "KEY",
    "LOW",
    "NEW",
    "NICE",
    "NOW",
    "ON",
    "ONE",
    "RE",
    "RUN",
    "SO",
    "WELL",
}


def _build_name_lookup() -> list[tuple[str, str]]:
    """Build the ordered (name, ticker) list used by extract_tickers_from_text.

    Rules:
      * Aliases take priority over display names.
      * Multi-word names always included.
      * Single-word names must be >= 4 characters and not in
        ``_AMBIGUOUS_SINGLE_WORD``.
      * Sorted longest-first so greedy matching prefers "eli lilly"
        over "lilly", "bank of america" over "bank".
    """
    lookup: dict[str, str] = {}

    for alias, ticker in TICKER_ALIASES.items():
        key = alias.lower().strip()
        if key:
            lookup[key] = ticker

    for ticker, name in TICKER_DISPLAY_NAMES.items():
        key = (name or "").lower().strip()
        if not key:
            continue
        if key == ticker.lower():
            # Name equals ticker (e.g., "IBM": "IBM") — never do bare-word
            # matching on the ticker itself; that would false-match acronyms.
            continue
        if " " not in key:
            if len(key) < 4 or key in _AMBIGUOUS_SINGLE_WORD:
                continue
        lookup.setdefault(key, ticker)

    return sorted(lookup.items(), key=lambda kv: (-len(kv[0]), kv[0]))


_NAME_LOOKUP: list[tuple[str, str]] = _build_name_lookup()


def extract_tickers_from_text(text: str) -> list[str]:
    """Return tickers whose canonical company name or uppercased symbol
    appears in ``text``.

    Pass 1 matches canonical company names (case-insensitive), e.g. "Nvidia"
    → NVDA, "JPMorgan Chase" → JPM, "TSMC" → TSM.

    Pass 2 matches bare ticker symbols written in uppercase in the original
    text, e.g. "AMD jumps 5%" → AMD. Symbols shorter than 3 characters or
    listed in ``_BARE_SYMBOL_EXCLUDE`` are not matched to avoid collisions
    with common English words.

    Returns tickers in order of first appearance. Duplicates are suppressed.
    """
    if not text:
        return []
    lowered = text.lower()
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    for name, ticker in _NAME_LOOKUP:
        if ticker in seen:
            continue
        # Regex word boundaries handle most cases, but "&" and "." fail
        # \b; use lookaround on non-word chars instead.
        pattern = rf"(?<![\w]){re.escape(name)}(?![\w])"
        match = re.search(pattern, lowered)
        if match:
            hits.append((match.start(), ticker))
            seen.add(ticker)

    for ticker in TICKER_DISPLAY_NAMES:
        if ticker in seen:
            continue
        if len(ticker) < 3 or ticker in _BARE_SYMBOL_EXCLUDE:
            continue
        pattern = rf"(?<![\w]){re.escape(ticker)}(?![\w])"
        match = re.search(pattern, text)
        if match:
            hits.append((match.start(), ticker))
            seen.add(ticker)

    hits.sort()
    return [ticker for _, ticker in hits]


def company_name_for_ticker(ticker: str) -> str:
    """Return a readable company name when known, else the raw ticker."""
    symbol = ticker.upper().strip()
    return TICKER_DISPLAY_NAMES.get(symbol, symbol)


def format_company_ticker(ticker: str) -> str:
    """Format a single display label like 'Amazon (AMZN)'."""
    symbol = ticker.upper().strip()
    company = company_name_for_ticker(symbol)
    if company == symbol:
        return symbol
    return f"{company} ({symbol})"


def format_company_ticker_list(tickers: list[str], max_items: int = 2) -> str:
    """Format a compact list of company/ticker labels."""
    if not tickers:
        return ""

    labels = [format_company_ticker(t) for t in tickers[:max_items]]
    if len(tickers) > max_items:
        labels.append(f"+{len(tickers) - max_items} more")
    return ", ".join(labels)
