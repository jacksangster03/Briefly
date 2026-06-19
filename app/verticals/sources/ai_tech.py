"""API/RSS-first AI/Tech source collectors (fail-soft)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.data_sources.providers.sec_provider import SECProvider
from app.settings import Settings
from app.verticals.events import VerticalEvent

AI_WATCHLIST = {
    "NVDA", "MSFT", "GOOGL", "META", "AMZN", "AMD", "AVGO", "PLTR", "CRM", "ORCL", "ARM", "TSM", "ASML", "MU",
}


def _classify_ai_event_type(title: str, summary: str) -> str:
    blob = f"{title} {summary}".lower()
    if "export control" in blob or "sanction" in blob:
        return "export_controls"
    if "data center" in blob or "datacenter" in blob:
        return "data_center_infrastructure"
    if "ai capex" in blob or "capital expenditure" in blob or "capex" in blob:
        return "ai_capex"
    if "model" in blob and "release" in blob:
        return "model_release"
    if "cyber" in blob:
        return "cybersecurity"
    if "antitrust" in blob:
        return "antitrust"
    if "semiconductor" in blob or "chip" in blob:
        return "semiconductor_supply"
    if "regulation" in blob or "regulatory" in blob:
        return "ai_regulation"
    return "earnings_ai_commentary"


def _collect_sec_ai_events(settings: Settings, watchlist: set[str], limit: int = 25) -> tuple[list[VerticalEvent], dict[str, Any]]:
    health = {"status": "disabled", "fetched_count": 0, "accepted_count": 0, "suppressed_count": 0, "last_error": "", "last_success_at": ""}
    if not settings.sec_user_agent:
        health["last_error"] = "missing SEC_USER_AGENT"
        return [], health
    provider = SECProvider(user_agent=settings.sec_user_agent, timeout=settings.provider_timeout, max_retries=settings.provider_max_retries)
    query = "AI OR artificial intelligence OR datacenter OR semiconductor OR GPU OR inference"
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=5)
    try:
        filings = provider.search_filings(query=query, date_range=f"{start.isoformat()}:{end.isoformat()}", limit=limit)
        health["status"] = "ok"
        health["fetched_count"] = len(filings)
        health["last_success_at"] = datetime.now(timezone.utc).isoformat()
        out: list[VerticalEvent] = []
        for filing in filings:
            tickers = [str(t).upper() for t in (filing.tickers or [])]
            overlap = len(set(tickers) & watchlist)
            evt = VerticalEvent(
                vertical="ai_tech",
                source_name="sec_edgar",
                source_tier="official",
                source_url=filing.url or "",
                published_at=filing.published_at,
                fetched_at=datetime.now(timezone.utc),
                title=filing.title,
                summary=filing.summary,
                entities=[],
                tickers=tickers,
                regions=["us"],
                countries=["us"],
                asset_classes=["equities"],
                event_type=_classify_ai_event_type(filing.title, filing.summary),
                causal_channel="capex_cycle",
                source_count=1,
                novelty_score=float(filing.novelty_score or 0.5),
                relevance_score=0.7,
                portfolio_relevance=min(1.0, 0.35 + 0.25 * overlap) if watchlist else 0.35,
                market_relevance=0.7,
                confidence=0.8,
                diagnostics={"source": "sec_edgar", "freshness_score": 0.85, "price_confirmation": 0.0},
            ).with_computed_hash()
            out.append(evt)
        health["accepted_count"] = len(out)
        return out, health
    except Exception as exc:
        health["status"] = "error"
        health["last_error"] = str(exc)
        return [], health


def _collect_arxiv_events(settings: Settings, watchlist: set[str], limit: int = 15) -> tuple[list[VerticalEvent], dict[str, Any]]:
    health = {"status": "stub_inactive", "fetched_count": 0, "accepted_count": 0, "suppressed_count": 0, "last_error": "", "last_success_at": ""}
    try:
        url = "http://export.arxiv.org/api/query"
        params = {"search_query": "all:artificial+intelligence+OR+all:transformer+OR+all:semiconductor", "start": 0, "max_results": max(1, min(limit, 50))}
        resp = requests.get(url, params=params, timeout=min(15, int(settings.provider_timeout or 15)))
        if resp.status_code >= 400:
            health["status"] = "error"
            health["last_error"] = f"status={resp.status_code}"
            return [], health
        text = resp.text or ""
        # lightweight parse: split entries without external dependency
        entries = [chunk for chunk in text.split("<entry>") if "</entry>" in chunk]
        health["status"] = "ok"
        health["fetched_count"] = len(entries)
        health["last_success_at"] = datetime.now(timezone.utc).isoformat()
        out: list[VerticalEvent] = []
        for chunk in entries[:limit]:
            title = _xml_tag(chunk, "title")
            summary = _xml_tag(chunk, "summary")
            link = _xml_attr(chunk, "link", "href")
            evt = VerticalEvent(
                vertical="ai_tech",
                source_name="arxiv",
                source_tier="primary",
                source_url=link,
                published_at=None,
                fetched_at=datetime.now(timezone.utc),
                title=title,
                summary=summary,
                entities=[],
                tickers=[t for t in AI_WATCHLIST if t in watchlist][:2],
                regions=["global"],
                countries=[],
                asset_classes=["equities"],
                event_type="open_source_momentum",
                causal_channel="innovation_cycle",
                source_count=1,
                novelty_score=0.7,
                relevance_score=0.45,
                portfolio_relevance=0.2,
                market_relevance=0.35,
                confidence=0.5,
                diagnostics={"source": "arxiv", "freshness_score": 0.6, "price_confirmation": 0.0},
            ).with_computed_hash()
            out.append(evt)
        health["accepted_count"] = len(out)
        return out, health
    except Exception as exc:
        health["status"] = "error"
        health["last_error"] = str(exc)
        return [], health


def _xml_tag(chunk: str, tag: str) -> str:
    start = chunk.find(f"<{tag}>")
    end = chunk.find(f"</{tag}>")
    if start == -1 or end == -1 or end <= start:
        return ""
    return " ".join(chunk[start + len(tag) + 2 : end].strip().split())


def _xml_attr(chunk: str, tag: str, attr: str) -> str:
    pos = chunk.find(f"<{tag} ")
    if pos == -1:
        return ""
    end = chunk.find(">", pos)
    if end == -1:
        return ""
    fragment = chunk[pos:end]
    marker = f'{attr}="'
    a = fragment.find(marker)
    if a == -1:
        return ""
    b = fragment.find('"', a + len(marker))
    if b == -1:
        return ""
    return fragment[a + len(marker) : b]


def collect_ai_tech_events(
    *,
    settings: Settings,
    watchlist: list[str] | None = None,
) -> tuple[list[VerticalEvent], dict[str, dict[str, Any]]]:
    wl = {str(t).upper() for t in (watchlist or [])}
    events: list[VerticalEvent] = []
    sec_events, sec_health = _collect_sec_ai_events(settings, wl)
    events.extend(sec_events)
    arxiv_events, arxiv_health = _collect_arxiv_events(settings, wl)
    events.extend(arxiv_events)
    github_health = {
        "status": "stub_inactive",
        "fetched_count": 0,
        "accepted_count": 0,
        "suppressed_count": 0,
        "last_error": "",
        "last_success_at": "",
    }
    return events, {"sec": sec_health, "arxiv": arxiv_health, "github": github_health}
