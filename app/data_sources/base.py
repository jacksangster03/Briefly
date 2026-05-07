"""Base provider interface and shared utilities for data sources."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests

from app.db.session import get_session
from app.db.models import ProviderHealthLog
from app.logger import get_logger

logger = get_logger("providers")

_SECRET_QUERY_KEYS = {"api_key", "apikey", "access_key", "token", "key", "authorization"}


def redact_url_secrets(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        redacted = []
        for k, v in pairs:
            if k.lower() in _SECRET_QUERY_KEYS:
                redacted.append((k, "***"))
            else:
                redacted.append((k, v))
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(redacted, quote_via=quote, safe="*"),
                parts.fragment,
            )
        )
    except Exception:
        return url


class ProviderError(Exception):
    """Raised when a provider call fails after retries."""
    pass


class _ProviderCircuitBreaker:
    """Session-level circuit breaker for providers.

    When a provider fails repeatedly within a single process, further calls
    short-circuit immediately instead of burning the full retry + timeout
    budget on every subsequent request. Keyed by provider name so that
    multiple instances of the same provider (e.g. one in MarketDataService,
    one in NewsDataService) share a single breaker state.

    This is a process-level construct: state resets when the process exits,
    so manual CLI runs get a clean slate each invocation. Within a
    long-running scheduler process, breakers auto-close after
    ``COOLDOWN_SECONDS`` to give the provider a chance to recover.
    """

    # provider name -> (opened_at_monotonic, consecutive_terminal_failures)
    _state: dict[str, tuple[float, int]] = {}

    TRIP_AFTER_FAILURES: int = 2
    COOLDOWN_SECONDS: float = 300.0

    @classmethod
    def is_open(cls, name: str) -> bool:
        entry = cls._state.get(name)
        if not entry:
            return False
        opened_at, failures = entry
        if failures < cls.TRIP_AFTER_FAILURES:
            return False
        if time.monotonic() - opened_at >= cls.COOLDOWN_SECONDS:
            cls._state.pop(name, None)
            return False
        return True

    @classmethod
    def record_failure(cls, name: str) -> None:
        entry = cls._state.get(name)
        failures = (entry[1] if entry else 0) + 1
        cls._state[name] = (time.monotonic(), failures)
        if failures == cls.TRIP_AFTER_FAILURES:
            logger.warning(
                "%s circuit breaker tripped after %d consecutive failures; "
                "short-circuiting further calls for up to %ds",
                name, failures, int(cls.COOLDOWN_SECONDS),
            )

    @classmethod
    def record_success(cls, name: str) -> None:
        cls._state.pop(name, None)

    @classmethod
    def record_unauthorized(cls, name: str) -> None:
        cls._state[name] = (time.monotonic(), cls.TRIP_AFTER_FAILURES)

    @classmethod
    def reset(cls, name: str | None = None) -> None:
        if name is None:
            cls._state.clear()
        else:
            cls._state.pop(name, None)


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
        redacted_url = redact_url_secrets(url)
        if _ProviderCircuitBreaker.is_open(self.name):
            logger.info(
                "%s circuit breaker open; skipping %s without network call",
                self.name, redacted_url,
            )
            raise ProviderError(
                f"{self.name} circuit breaker open; skipping {redacted_url}"
            )

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
                        self.name, redacted_url, wait, attempt, self.max_retries,
                    )
                    self._log_health(redacted_url, latency_ms, status_code, False, "rate_limited")
                    time.sleep(wait)
                    continue

                if resp.status_code == 401:
                    logger.warning(
                        "%s unauthorized on %s; disabling provider for cooldown window",
                        self.name, redacted_url,
                    )
                    self._log_health(redacted_url, latency_ms, status_code, False, "unauthorized")
                    _ProviderCircuitBreaker.record_unauthorized(self.name)
                    raise ProviderError(f"{self.name} unauthorized on {redacted_url}")

                resp.raise_for_status()
                self._log_health(redacted_url, latency_ms, status_code, True)
                _ProviderCircuitBreaker.record_success(self.name)
                return resp.json()

            except requests.exceptions.Timeout as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "%s timeout on %s (attempt %d/%d)",
                    self.name, redacted_url, attempt, self.max_retries,
                )
                self._log_health(redacted_url, latency_ms, status_code, False, "timeout")
                last_exc = exc

            except requests.exceptions.RequestException as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "%s error on %s: %s (attempt %d/%d)",
                    self.name, redacted_url, exc, attempt, self.max_retries,
                )
                self._log_health(redacted_url, latency_ms, status_code, False, str(exc)[:500])
                last_exc = exc

        _ProviderCircuitBreaker.record_failure(self.name)
        raise ProviderError(
            f"{self.name} failed after {self.max_retries} attempts on {redacted_url}: {last_exc}"
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
