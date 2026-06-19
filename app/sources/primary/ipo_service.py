"""IPO and private-company intelligence aggregator.

Orchestrates three sub-providers:
  1. IpoEdgarProvider     — SEC EDGAR public filings (S-1, F-1, 424B4, etc.)
  2. PrivateCompanyNewsroomProvider — official newsroom monitors
  3. IpoCalendarProvider  — Nasdaq/FMP estimated IPO calendars

Also generates read-through NormalisedEvents for public-market peers mapped
in the private_companies.yaml registry.

Integration point: NewsDataService instantiates IpoIntelligenceService and
calls fetch_all(watchlist) inside its own fetch_all() pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.schemas.ipo_event import IpoEvent, PublicPeerRelationship, RelationshipType
from app.sources.primary import ipo_store
from app.sources.primary.ipo_calendar import IpoCalendarProvider
from app.sources.primary.ipo_edgar import IpoEdgarProvider
from app.sources.primary.private_company_newsroom import PrivateCompanyNewsroomProvider

logger = get_logger("primary.ipo_service")

_REGISTRY_PATH = Path("configs/private_companies.yaml")


def _load_registry(path: Path = _REGISTRY_PATH) -> dict:
    if not path.exists():
        logger.warning("ipo_service: registry not found at %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data.get("companies", {}) if isinstance(data, dict) else {}
    except Exception as exc:
        logger.error("ipo_service: failed to load registry: %s", exc)
        return {}


class IpoIntelligenceService:
    """Aggregates all IPO intelligence providers and returns enriched events."""

    def __init__(self, settings, data_dir: str = "data"):
        self._settings = settings
        self._data_dir = data_dir
        self._registry = _load_registry()

        # Initialise persistent store
        ipo_store.init_store(data_dir)

        user_agent = getattr(settings, "sec_user_agent", "Briefly jacksangster.033@gmail.com")
        fmp_key = getattr(settings, "fmp_api_key", None)
        timeout = getattr(settings, "provider_timeout", 30)
        max_retries = getattr(settings, "provider_max_retries", 2)

        self._edgar = IpoEdgarProvider(
            user_agent=user_agent, registry=self._registry,
            timeout=timeout, max_retries=max_retries,
        )
        self._newsroom = PrivateCompanyNewsroomProvider(
            user_agent=user_agent, registry=self._registry,
            timeout=timeout, max_retries=max_retries,
        )
        self._calendar = IpoCalendarProvider(
            fmp_api_key=fmp_key,
            timeout=timeout, max_retries=max_retries,
        )

    def is_available(self) -> bool:
        enabled = getattr(self._settings, "enable_ipo_intelligence", True)
        return enabled and bool(self._registry)

    def fetch_all(self, watchlist: list[str] | None = None) -> list[NormalisedEvent]:
        """Fetch all IPO events and generate read-through events for public peers."""
        if not self.is_available():
            return []

        events: list[NormalisedEvent] = []

        # 1. EDGAR public filings
        try:
            edgar_events = self._edgar.fetch_ipo_filings(days_back=7)
            events.extend(edgar_events)
            logger.info("ipo_service: %d EDGAR events", len(edgar_events))
        except Exception as exc:
            logger.error("ipo_service: EDGAR fetch failed: %s", exc)

        # 2. Official newsroom announcements
        monitor_newsrooms = getattr(self._settings, "ipo_monitor_newsrooms", True)
        if monitor_newsrooms and self._newsroom.is_configured():
            try:
                newsroom_events = self._newsroom.fetch_announcements()
                events.extend(newsroom_events)
                logger.info("ipo_service: %d newsroom events", len(newsroom_events))
            except Exception as exc:
                logger.error("ipo_service: newsroom fetch failed: %s", exc)

        # 3. IPO calendar (lower confidence)
        try:
            calendar_events = self._calendar.fetch_calendar()
            events.extend(calendar_events)
            logger.info("ipo_service: %d calendar events", len(calendar_events))
        except Exception as exc:
            logger.error("ipo_service: calendar fetch failed: %s", exc)

        # 4. Generate read-through events from registry peer mappings
        readthrough = self._generate_readthrough_events(events, watchlist)
        events.extend(readthrough)
        logger.info("ipo_service: %d read-through events, %d total", len(readthrough), len(events))

        return events

    # -- Read-through generation ----------------------------------------------

    def _generate_readthrough_events(
        self,
        base_events: list[NormalisedEvent],
        watchlist: list[str] | None,
    ) -> list[NormalisedEvent]:
        """For each private-company event, emit one NormalisedEvent per public peer
        that has a confirmed relationship in the registry.
        """
        readthrough_events: list[NormalisedEvent] = []

        for evt in base_events:
            ipo_meta_raw = evt.raw_data.get("ipo_metadata") if evt.raw_data else None
            if not ipo_meta_raw:
                continue

            try:
                ipo_meta = IpoEvent.from_raw_data(ipo_meta_raw)
            except Exception:
                continue

            company_id = ipo_meta.private_company_id or ""
            if not company_id:
                continue

            cfg = self._registry.get(company_id, {})
            public_peers = cfg.get("public_peers", {})

            for ticker, peer_cfg in public_peers.items():
                if not isinstance(peer_cfg, dict):
                    continue
                rel_type: RelationshipType = peer_cfg.get("relationship", "sector_peer")
                evidence = peer_cfg.get("evidence", "")

                # Skip speculative relationships for lower-confidence events
                if rel_type == "speculative_readthrough" and ipo_meta.status_confidence == "low":
                    continue

                # If watchlist provided, only emit for tickers in watchlist
                if watchlist and ticker not in watchlist:
                    continue

                rt_meta = IpoEvent(
                    private_company_id=company_id,
                    canonical_company_name=ipo_meta.canonical_company_name,
                    ipo_status=ipo_meta.ipo_status,
                    filing_type=ipo_meta.filing_type,
                    terms=ipo_meta.terms,
                    status_confidence=ipo_meta.status_confidence,
                    official_source_urls=ipo_meta.official_source_urls,
                    last_material_update_at=ipo_meta.last_material_update_at,
                    is_readthrough_event=True,
                    readthrough_relationship=rel_type,
                    source_company_id=company_id,
                    public_peer_relationships=[
                        PublicPeerRelationship(
                            ticker=ticker,
                            relationship_type=rel_type,
                            evidence=evidence,
                        )
                    ],
                )

                rt_evt = NormalisedEvent(
                    source="ipo_readthrough",
                    source_type="derived",
                    published_at=evt.published_at or datetime.now(timezone.utc),
                    title=f"{ipo_meta.canonical_company_name} IPO read-through: {ticker}",
                    summary=(
                        f"{ipo_meta.canonical_company_name} IPO activity ({ipo_meta.ipo_status})"
                        f" may affect {ticker} ({rel_type.replace('_', ' ')})."
                        + (f" Evidence: {evidence}" if evidence else "")
                    ),
                    url=evt.url,
                    tickers=[ticker],
                    event_type="ipo_readthrough",
                    factual_confidence_score=max(0.5, evt.factual_confidence_score - 0.15),
                    importance_score=0.68,
                    raw_data={"ipo_metadata": rt_meta.to_raw_data_dict()},
                )
                rt_evt.compute_hash()
                readthrough_events.append(rt_evt)

        return readthrough_events

    # -- Diagnostics ----------------------------------------------------------

    def status_report(self) -> dict:
        """Return a structured status report for the ipo-status CLI command."""
        companies: list[dict] = []
        for cid, cfg in self._registry.items():
            diag = ipo_store.get_diagnostics(cid)
            companies.append({
                "company_id": cid,
                "canonical_name": cfg.get("canonical_name", cid),
                "registry_status": cfg.get("status", "unknown"),
                "stored_status": diag.get("current_status"),
                "status_confidence": cfg.get("status_confidence", "unknown"),
                "known_filings": len(diag.get("known_accessions", [])),
                "last_filing_events": diag.get("filing_events_count", 0),
                "last_polls": diag.get("last_polls", {}),
            })
        return {
            "companies": companies,
            "total": len(companies),
            "registry_path": str(_REGISTRY_PATH),
        }
