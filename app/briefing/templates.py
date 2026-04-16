"""Message section headers and format constants.

Keeps all copy/layout decisions in one place so they are easy to adjust.
"""

# Sparse emoji for section headers (mobile-friendly visual anchors)
SECTION_HEADERS = {
    "morning_title": "MORNING BRIEFING",
    "weekend_title_saturday": "WEEKEND BRIEFING — SATURDAY",
    "weekend_title_sunday": "WEEKEND BRIEFING — SUNDAY",
    "market_setup": "MARKET SETUP",
    "weekend_setup": "LAST CLOSE (FRIDAY)",
    "macro": "MACRO CONTEXT",
    "global_news": "GLOBAL NEWS & GEOPOLITICS",
    "themes": "TOP THEMES",
    "weekend_themes": "WEEKEND DEVELOPMENTS",
    "portfolio_focus": "PORTFOLIO FOCUS",
    "sectors": "SECTOR SCAN",
    "earnings": "EARNINGS CALENDAR",
    "watchlist": "WATCHLIST",
    "week_ahead": "WHAT TO WATCH NEXT WEEK",
    "intraday_title": "INTRADAY UPDATE",
    "weekend_intraday_title": "WEEKEND UPDATE",
    "global_risk_update": "GLOBAL RISK UPDATE",
    "breaking_title": "BREAKING",
    "footer": "Briefly",
}

# Directional arrows for price changes
def direction_arrow(change: float) -> str:
    if change > 0:
        return "+"
    if change < 0:
        return ""  # negative sign is already in the number
    return "~"


def format_change(change: float, change_pct: float) -> str:
    """Format a price change with direction, e.g. '+1.23 (+0.45%)'."""
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.2f} ({sign}{change_pct:.2f}%)"


def format_price_line(name: str, price: float, change: float, change_pct: float) -> str:
    """One-line price summary: 'S&P 500: 5,234.50 +12.30 (+0.24%)'."""
    return f"{name}: {price:,.2f} {format_change(change, change_pct)}"


def format_compact_price(name: str, change_pct: float) -> str:
    """Ultra-compact: 'S&P 500 +0.24%'."""
    sign = "+" if change_pct >= 0 else ""
    return f"{name} {sign}{change_pct:.2f}%"


def format_compact_price_with_level(name: str, price: float, change_pct: float) -> str:
    """Compact with level: 'S&P 500 5,234.50 (+0.24%)'."""
    sign = "+" if change_pct >= 0 else ""
    return f"{name} {price:,.2f} ({sign}{change_pct:.2f}%)"


def format_context_price(
    name: str,
    symbol: str,
    current: float,
    previous: float,
    change_pct: float,
    reference_label: str = "prior close",
) -> str:
    """Readable context line with full instrument name and reference close.

    Example (weekday): 'S&P 500 (SPY): 679.48, -0.06% vs prior close 679.91'
    Example (weekend): 'S&P 500 (SPY): 679.48, -0.06% vs Friday close 679.91'
    """
    sign = "+" if change_pct >= 0 else ""
    return (
        f"{name} ({symbol}): {current:,.2f}, {sign}{change_pct:.2f}% "
        f"vs {reference_label} {previous:,.2f}"
    )


# Telegram character limit per message
TELEGRAM_MAX_LENGTH = 4096

# Maximum events per section in morning briefing
MAX_THEMES = 5
MAX_SECTOR_EVENTS = 3
MAX_WATCHLIST_EVENTS = 8
MAX_EARNINGS_DISPLAY = 10
MAX_INTRADAY_EVENTS = 7
