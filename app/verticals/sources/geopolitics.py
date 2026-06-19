"""API/RSS-first geopolitics source collector."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data_sources.providers.gdelt import GDELTProvider
from app.settings import Settings
from app.verticals.events import VerticalEvent

_GEO_KEYWORDS: dict[str, str] = {
    "sanction": "sanctions",
    "embargo": "trade_controls",
    "strait": "shipping_disruption",
    "shipping": "shipping_disruption",
    "blockade": "shipping_disruption",
    "missile": "military_escalation",
    "drone": "military_escalation",
    "troops": "military_escalation",
    "election": "election_policy_risk",
    "tariff": "trade_controls",
    "supply chain": "supply_chain_disruption",
    "central bank pressure": "central_bank_political_pressure",
    "pipeline": "energy_chokepoint",
}


def _classify_geo_event_type(title: str, summary: str) -> str:
    blob = f"{title} {summary}".lower()
    for token, event_type in _GEO_KEYWORDS.items():
        if token in blob:
            return event_type
    return "geopolitics_general"


def collect_geopolitics_events(
    *,
    settings: Settings,
    watchlist: list[str] | None = None,
    max_records: int = 40,
) -> tuple[list[VerticalEvent], dict[str, dict[str, Any]]]:
    """Collect geopolitics events with fail-soft diagnostics."""
    source_health: dict[str, dict[str, Any]] = {
        "gdelt": {
            "status": "disabled" if not settings.enable_gdelt else "unavailable",
            "fetched_count": 0,
            "accepted_count": 0,
            "suppressed_count": 0,
            "last_error": "",
            "last_success_at": "",
        },
        "newsapi": {"status": "stub_inactive", "fetched_count": 0, "accepted_count": 0, "suppressed_count": 0, "last_error": "", "last_success_at": ""},
        "finnhub": {"status": "stub_inactive", "fetched_count": 0, "accepted_count": 0, "suppressed_count": 0, "last_error": "", "last_success_at": ""},
    }
    if not settings.enable_gdelt:
        return [], source_health

    provider = GDELTProvider(
        base_url=settings.gdelt_base_url,
        timeout=settings.provider_timeout,
        max_retries=settings.provider_max_retries,
    )
    events: list[VerticalEvent] = []
    try:
        raw = provider.get_market_news(
            query=settings.gdelt_global_query,
            max_records=max_records,
        )
        source_health["gdelt"]["fetched_count"] = len(raw)
        now = datetime.now(timezone.utc).isoformat()
        source_health["gdelt"]["last_success_at"] = now
        source_health["gdelt"]["status"] = "ok"
        wl = {str(t).upper() for t in (watchlist or [])}
        for item in raw:
            event_type = _classify_geo_event_type(item.title or "", item.summary or "")
            tickers = [t.upper() for t in (item.tickers or []) if str(t).strip()]
            overlap = len(set(tickers) & wl)
            evt = VerticalEvent(
                vertical="geopolitics",
                source_name="gdelt",
                source_tier="primary",
                source_url=item.url or "",
                published_at=item.published_at,
                fetched_at=datetime.now(timezone.utc),
                title=item.title or "",
                summary=item.summary or "",
                entities=[],
                tickers=tickers,
                regions=[str(r).lower() for r in (item.regions or []) if str(r).strip()],
                countries=[],
                asset_classes=["commodities", "equities", "fx"],
                event_type=event_type,
                causal_channel="risk_sentiment",
                source_count=1,
                novelty_score=float(item.novelty_score or 0.5),
                relevance_score=0.6,
                portfolio_relevance=min(1.0, 0.4 + 0.2 * overlap) if wl else 0.3,
                market_relevance=0.7,
                confidence=0.6,
                diagnostics={
                    "source": "gdelt",
                    "headline_density_only": True,
                    "price_confirmation": 0.0,
                    "freshness_score": 0.8,
                },
            ).with_computed_hash()
            events.append(evt)
        source_health["gdelt"]["accepted_count"] = len(events)
    except Exception as exc:
        source_health["gdelt"]["status"] = "error"
        source_health["gdelt"]["last_error"] = str(exc)
    return events, source_health
