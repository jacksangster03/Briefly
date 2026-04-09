"""Base provider interface and shared utilities for data sources."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests

from app.db.session import get_session
from app.db.models import ProviderHealthLog
from app.logger import get_logger

logger = get_logger("providers")


class ProviderError(Exception):
    """Raised when a provider call fails after retries."""
    pass


class BaseProvider(ABC):
    """Abstract base for all data providers.

    Subclasses must set ``name`` and implement ``health_check``.
    Provides shared HTTP helpers with retry, timeout, and observability.
    """

    name: str = "base"

    def __init__(self, timeout: int = 30, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if required credentials / settings are present."""
        ...

    def health_check(self) -> bool:
        """Return True if the provider is reachable and responding."""
        return self.is_configured()

    # -- HTTP helpers with observability --------------------------------------

    def _get(self, url: str, params: dict | None = None, **kwargs) -> dict | list | Any:
        """HTTP GET with retry, timeout, and provider health logging."""
        return self._request("GET", url, params=params, **kwargs)

    def _request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json: dict | None = None,
        **kwargs,
    ) -> Any:
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            start = time.monotonic()
            status_code = None
            try:
                resp = self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    timeout=self.timeout,
                    **kwargs,
                )
                status_code = resp.status_code
                latency_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code == 429:
                    wait = min(2 ** attempt, 10)
                    logger.warning(
                        "%s rate-limited on %s, waiting %ds (attempt %d/%d)",
                        self.name, url, wait, attempt, self.max_retries,
                    )
                    self._log_health(url, latency_ms, status_code, False, "rate_limited")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                self._log_health(url, latency_ms, status_code, True)
                return resp.json()

            except requests.exceptions.Timeout as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "%s timeout on %s (attempt %d/%d)",
                    self.name, url, attempt, self.max_retries,
                )
                self._log_health(url, latency_ms, status_code, False, "timeout")
                last_exc = exc

            except requests.exceptions.RequestException as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "%s error on %s: %s (attempt %d/%d)",
                    self.name, url, exc, attempt, self.max_retries,
                )
                self._log_health(url, latency_ms, status_code, False, str(exc)[:500])
                last_exc = exc

        raise ProviderError(
            f"{self.name} failed after {self.max_retries} attempts on {url}: {last_exc}"
        )

    def _log_health(
        self,
        endpoint: str,
        latency_ms: int,
        status_code: int | None,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """Persist a provider health record for observability."""
        try:
            with get_session() as session:
                record = ProviderHealthLog(
                    provider=self.name,
                    endpoint=endpoint[:255],
                    latency_ms=latency_ms,
                    status_code=status_code,
                    success=success,
                    error_message=error_message,
                    timestamp=datetime.now(timezone.utc),
                )
                session.add(record)
        except Exception:
            # Health logging must never crash the pipeline
            logger.debug("Failed to log provider health for %s", self.name, exc_info=True)
