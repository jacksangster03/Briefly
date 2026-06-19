"""IPO and private-company state persistence.

Stores per-company state as JSON in data/cache/ipo/{company_id}.json.
Tracks: IPO status history, content hashes (for change detection), known
SEC accession numbers (for dedup), last poll timestamps per URL.

All file I/O is isolated here. Providers read/write through this module only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("briefly.primary.ipo_store")

_STORE_DIR: Path | None = None  # set by init_store()

# Minimum seconds between polls of the same URL (4 hours)
MIN_POLL_INTERVAL_S: int = 4 * 60 * 60


def init_store(data_dir: str) -> None:
    """Initialise the store directory. Call once at service startup."""
    global _STORE_DIR
    _STORE_DIR = Path(data_dir) / "cache" / "ipo"
    _STORE_DIR.mkdir(parents=True, exist_ok=True)


def _store_path(company_id: str) -> Path:
    if _STORE_DIR is None:
        raise RuntimeError("ipo_store.init_store() must be called before use")
    return _STORE_DIR / f"{company_id}.json"


def _load(company_id: str) -> dict[str, Any]:
    path = _store_path(company_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("ipo_store: failed to load %s: %s", company_id, exc)
        return {}


def _save(company_id: str, data: dict[str, Any]) -> None:
    try:
        path = _store_path(company_id)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("ipo_store: failed to save %s: %s", company_id, exc)


# -- Content fingerprinting ---------------------------------------------------

def is_content_new(company_id: str, url: str, fingerprint: str) -> bool:
    """Return True if content at url has changed since last store."""
    data = _load(company_id)
    hashes = data.get("content_hashes", {})
    return hashes.get(url) != fingerprint


def record_content(company_id: str, url: str, fingerprint: str) -> None:
    """Store the latest fingerprint for url."""
    data = _load(company_id)
    hashes = data.setdefault("content_hashes", {})
    hashes[url] = fingerprint
    data["content_hashes"] = hashes
    _save(company_id, data)


# -- Poll rate limiting -------------------------------------------------------

def is_poll_due(company_id: str, url: str) -> bool:
    """Return True if enough time has passed since the last poll of url."""
    data = _load(company_id)
    last_polls = data.get("last_poll", {})
    last_str = last_polls.get(url)
    if not last_str:
        return True
    try:
        last_dt = datetime.fromisoformat(last_str)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return elapsed >= MIN_POLL_INTERVAL_S
    except Exception:
        return True


def record_poll(company_id: str, url: str) -> None:
    """Record a successful poll of url."""
    data = _load(company_id)
    polls = data.setdefault("last_poll", {})
    polls[url] = datetime.now(timezone.utc).isoformat()
    _save(company_id, data)


# -- Filing dedup -------------------------------------------------------------

def is_accession_new(company_id: str, accession: str) -> bool:
    """Return True if this accession number has not been seen before."""
    data = _load(company_id)
    return accession not in data.get("known_accessions", [])


def record_accession(company_id: str, accession: str) -> None:
    """Mark an accession number as seen."""
    data = _load(company_id)
    seen = data.setdefault("known_accessions", [])
    if accession not in seen:
        seen.append(accession)
    _save(company_id, data)


# -- Status history -----------------------------------------------------------

def record_status_change(
    company_id: str,
    new_status: str,
    source: str,
    note: str = "",
) -> None:
    """Append a status-change entry to the company's history."""
    data = _load(company_id)
    history = data.setdefault("status_history", [])
    history.append({
        "status": new_status,
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "note": note,
    })
    data["current_status"] = new_status
    _save(company_id, data)


def get_current_status(company_id: str) -> str | None:
    """Return the most recently recorded status, or None if not set."""
    data = _load(company_id)
    return data.get("current_status")


def get_status_history(company_id: str) -> list[dict[str, Any]]:
    """Return the full status history for a company."""
    return _load(company_id).get("status_history", [])


# -- Filing events log --------------------------------------------------------

def record_filing_event(company_id: str, event: dict[str, Any]) -> None:
    """Append a filing event to the log (bounded at 100 entries)."""
    data = _load(company_id)
    events = data.setdefault("filing_events", [])
    events.append(event)
    data["filing_events"] = events[-100:]
    _save(company_id, data)


def get_filing_events(company_id: str) -> list[dict[str, Any]]:
    return _load(company_id).get("filing_events", [])


# -- Diagnostics --------------------------------------------------------------

def get_diagnostics(company_id: str) -> dict[str, Any]:
    """Return all stored state for diagnostics display."""
    data = _load(company_id)
    return {
        "company_id": company_id,
        "current_status": data.get("current_status"),
        "known_accessions": data.get("known_accessions", []),
        "content_hashes_count": len(data.get("content_hashes", {})),
        "last_polls": data.get("last_poll", {}),
        "status_history": data.get("status_history", []),
        "filing_events_count": len(data.get("filing_events", [])),
    }


def list_monitored_companies() -> list[str]:
    """Return all company IDs that have a stored state file."""
    if _STORE_DIR is None:
        return []
    return [p.stem for p in _STORE_DIR.glob("*.json")]
