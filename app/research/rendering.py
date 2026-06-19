"""Deterministic analyst-read rendering helpers."""

from __future__ import annotations

from app.processing.cleaners import truncate
from app.schemas.research_event import ResearchEvent


def render_analyst_read(
    events: list[ResearchEvent],
    *,
    session_key: str,
    use_llm_prose: bool = False,
) -> list[str]:
    """Return concise analyst-read lines from already-selected evidence.

    ``use_llm_prose`` is accepted for the public interface but v1 remains
    deterministic. A future bounded LLM pass can rewrite these lines after
    the evidence set is fixed.
    """
    if not events:
        since = "the prior session" if (session_key or "morning").lower() != "morning" else "this provider run"
        return [f"No material fresh portfolio-relevant news cleared the research bar since {since}."]

    lines: list[str] = []
    for event in events:
        label = _event_label(event)
        confidence = (event.confidence_label or "none").upper()
        changed = _what_changed_line(event)
        impact = _impact_line(event)
        watch = _watch_line(event)
        source = _source_citation(event)
        lines.append(
            f"- <b>{label}</b>: {truncate(changed, 130)} {truncate(impact, 145)} "
            f"<i>Confidence: {confidence}; {source}. Next: {truncate(watch, 120)}</i>"
        )
    return lines


def _event_label(event: ResearchEvent) -> str:
    if event.tickers:
        return f"{', '.join(event.tickers[:3])} / {truncate(event.title, 90)}"
    return truncate(event.title, 105)


def _what_changed_line(event: ResearchEvent) -> str:
    """Channel-specific what-changed framing."""
    if event.what_changed and event.what_changed not in {
        "Material update versus prior classification.",
        "New item in the current provider run.",
    }:
        return event.what_changed

    channel = str(event.causal_channel or "other").lower()
    is_material = event.material_update

    if channel == "earnings":
        if is_material:
            return "Earnings update: guidance, margin, or revision signal changed."
        return "Earnings: guidance, margins, and peer read-through in focus."
    if channel == "rates":
        if is_material:
            return "Rates development: duration, valuation, and FX impact updated."
        return "Rates: watch duration exposure, growth-sensitive valuations, and dollar."
    if channel == "geopolitical":
        if is_material:
            return "Geopolitical update: cross-asset confirmation picture changed."
        return "Geopolitical: oil, VIX, gold, and FX are the confirmation signals."
    if channel == "regulatory":
        if is_material:
            return "Regulatory update: market-size or cost-base picture changed."
        return "Regulatory: market-size, cost-base, and legal-overhang in scope."
    if channel == "supply_chain":
        if is_material:
            return "Supply chain update: margin, timing, or customer-impact signal changed."
        return "Supply chain: watch margin compression, delivery timing, and customer exposure."

    # Fallback to the generic fields
    return event.what_changed or event.why_now or "Fresh provider evidence."


def _impact_line(event: ResearchEvent) -> str:
    """Channel-enriched portfolio impact framing."""
    if event.portfolio_impact:
        return event.portfolio_impact

    channel = str(event.causal_channel or "other").lower()
    tickers = ", ".join(event.tickers[:3]) or "affected assets"

    if channel == "earnings":
        if event.portfolio_relevance_score >= 0.75:
            return f"Direct earnings read for {tickers}; watch guidance delta and peer contagion."
        return f"Earnings read-through for {tickers}; margins and revision risk are the primary transmission."
    if channel == "rates":
        return "Duration-sensitive growth and valuation-sensitive names face the largest re-rating risk."
    if channel == "geopolitical":
        return "Energy-linked and safe-haven assets are the primary cross-asset transmission channels."
    if channel == "regulatory":
        if event.portfolio_relevance_score >= 0.65:
            return f"Regulatory scope touches {tickers}; market-size and cost-base estimates need updating."
        return "Regulatory overhang: legal and compliance cost-base are the key transmission vectors."
    if channel == "supply_chain":
        return f"Supply chain exposure: margin and delivery risk for {tickers}."
    if event.portfolio_relevance_score >= 0.75:
        return f"Direct portfolio/watchlist relevance for {tickers}."
    if event.portfolio_relevance_score >= 0.45 or event.watchlist_relevance_score >= 0.45:
        return f"Relevant read-through for {tickers}."
    return "Market-context item; portfolio impact depends on confirmation."


def _watch_line(event: ResearchEvent) -> str:
    """Channel-specific next-watch framing, falling back to the event's own field."""
    if event.what_to_watch_next and event.what_to_watch_next not in {
        "Watch for follow-up sources or price confirmation.",
        "Watch for follow-up confirmation.",
    }:
        return event.what_to_watch_next

    channel = str(event.causal_channel or "other").lower()
    if event.price_confirmation_status in {"confirmed", "contradicted", "pending"}:
        return event.price_confirmation_detail or _channel_watch(channel)
    return _channel_watch(channel)


def _channel_watch(channel: str) -> str:
    mapping = {
        "earnings": "Watch guidance revision, margin trajectory, and peer read-through.",
        "rates": "Watch yields, dollar index, and valuation-sensitive growth response.",
        "geopolitical": "Watch oil, VIX, gold, and FX for cross-asset confirmation.",
        "regulatory": "Watch market-size estimate revisions and legal-cost guidance.",
        "supply_chain": "Watch margin guidance updates and customer exposure announcements.",
        "sentiment": "Watch flow data and positioning shifts for confirmation.",
    }
    return mapping.get(channel, "Watch for follow-up sources or price confirmation.")


def _source_citation(event: ResearchEvent) -> str:
    """Build a compact source citation with name and link where available."""
    parts: list[str] = []

    if event.evidence_items:
        item = event.evidence_items[0]
        name = (item.source_name or event.source_tier or "source").strip()
        url = (item.url or "").strip()
        if url:
            # Telegram HTML anchor: visible name links to the source
            parts.append(f'<a href="{url}">{name}</a>')
        else:
            parts.append(name)
    elif event.primary_sources:
        url = event.primary_sources[0]
        tier = (event.source_tier or "source").strip()
        parts.append(f'<a href="{url}">{tier}</a>')
    elif event.secondary_sources:
        tier = (event.source_tier or "source").strip()
        parts.append(tier)
    else:
        parts.append("source metadata only")

    # Append any additional evidence item source names (up to 2 more)
    for extra in (event.evidence_items or [])[1:3]:
        extra_name = (extra.source_name or "").strip()
        if extra_name and extra_name not in parts[0]:
            extra_url = (extra.url or "").strip()
            if extra_url:
                parts.append(f'<a href="{extra_url}">{extra_name}</a>')
            else:
                parts.append(extra_name)

    citation = " / ".join(parts)
    return f"Source: {citation}"
