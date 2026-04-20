"""Deterministic portfolio-impact mapping for briefing surfaces."""

from __future__ import annotations

from app.personalization.user_profile import UserProfile
from app.schemas.events import NormalisedEvent


def build_portfolio_impact(
    *,
    profile: UserProfile,
    global_news: list[NormalisedEvent],
    top_themes: list[NormalisedEvent],
    portfolio_focus: list[NormalisedEvent],
    setup_tags: list[str],
) -> tuple[list[str], str]:
    """Return concise portfolio-impact bullets and an action posture."""
    if not profile.portfolio_holdings:
        return ["No active holdings configured. Portfolio impact is limited to watchlist relevance."], "monitor"

    symbols = {h.symbol.upper() for h in profile.portfolio_holdings}
    top_symbols = [h.symbol.upper() for h in sorted(profile.portfolio_holdings, key=lambda x: x.weight_pct or 0.0, reverse=True)[:3]]

    bullets: list[str] = []
    if any(tag in setup_tags for tag in ("commodity_pressure", "rates_headwind")):
        bullets.append("Macro pressure is elevated (rates/commodities), so drawdown-sensitive sleeves deserve closer monitoring.")
    if any(tag in setup_tags for tag in ("risk_on", "rates_supportive", "commodity_easing")):
        bullets.append("Risk backdrop is supportive enough for growth-heavy exposures to participate if headlines stabilize.")

    linked_events = _linked_events(symbols, portfolio_focus + top_themes + global_news)
    if linked_events:
        sample = ", ".join(linked_events[:3])
        bullets.append(f"Most portfolio-linked names in today’s tape: {sample}.")
    else:
        anchors = ", ".join(top_symbols) if top_symbols else "core holdings"
        bullets.append(f"No direct single-name catalyst dominates; monitor macro transmission into {anchors}.")

    if any("hormuz" in f"{evt.title} {evt.summary}".lower() or "iran" in f"{evt.title} {evt.summary}".lower() for evt in global_news):
        bullets.append("Energy/geopolitical channel remains the primary exogenous risk path for near-term portfolio volatility.")

    posture = _action_posture(setup_tags=setup_tags, has_linked=bool(linked_events))
    return bullets[:3], posture


def _linked_events(symbols: set[str], events: list[NormalisedEvent]) -> list[str]:
    linked: list[str] = []
    for evt in events:
        hits = [ticker for ticker in evt.tickers if ticker in symbols]
        for ticker in hits:
            if ticker not in linked:
                linked.append(ticker)
    return linked


def _action_posture(*, setup_tags: list[str], has_linked: bool) -> str:
    if "risk_off" in setup_tags or "commodity_pressure" in setup_tags:
        return "review_risk"
    if has_linked and "risk_on" in setup_tags:
        return "monitor_and_simulate"
    if has_linked:
        return "monitor"
    return "review_diagnostics"

