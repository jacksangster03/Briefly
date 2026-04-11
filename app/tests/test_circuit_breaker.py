"""Tests for the provider circuit breaker.

These tests pin the session-level short-circuit behaviour that prevents
a degraded provider from burning through every batch's timeout budget on
a manual run. The breaker:

- Stays closed until ``TRIP_AFTER_FAILURES`` consecutive terminal failures.
- Once open, short-circuits subsequent calls with ``ProviderError`` without
  ever touching the network.
- Resets on any success so a flaky-but-alive provider is not kept out.
- Auto-resets after ``COOLDOWN_SECONDS`` so long-running schedulers
  eventually give the provider another chance.
- Is keyed by provider name, so two ``FinnhubProvider`` instances (one
  in ``MarketDataService``, one in ``NewsDataService``) share state and a
  trip in the first immediately protects the second.
"""

from __future__ import annotations

import time

import pytest
import requests

from app.data_sources.base import (
    BaseProvider,
    ProviderError,
    _ProviderCircuitBreaker,
)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


class _FakeSession:
    """Scriptable requests.Session replacement."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        if not self._outcomes:
            raise AssertionError(f"Unexpected extra request to {url}")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeProvider(BaseProvider):
    name = "fake"

    def __init__(self, outcomes, max_retries: int = 1):
        super().__init__(timeout=1, max_retries=max_retries)
        self._session = _FakeSession(outcomes)

    def is_configured(self) -> bool:
        return True

    def call(self):
        return self._get("https://example.test/endpoint")


@pytest.fixture(autouse=True)
def _reset_breaker():
    _ProviderCircuitBreaker.reset()
    yield
    _ProviderCircuitBreaker.reset()


def test_breaker_starts_closed():
    assert _ProviderCircuitBreaker.is_open("fake") is False


def test_single_failure_does_not_open_breaker():
    _ProviderCircuitBreaker.record_failure("fake")
    assert _ProviderCircuitBreaker.is_open("fake") is False


def test_two_failures_open_breaker():
    _ProviderCircuitBreaker.record_failure("fake")
    _ProviderCircuitBreaker.record_failure("fake")
    assert _ProviderCircuitBreaker.is_open("fake") is True


def test_success_resets_breaker_state():
    _ProviderCircuitBreaker.record_failure("fake")
    _ProviderCircuitBreaker.record_failure("fake")
    assert _ProviderCircuitBreaker.is_open("fake") is True
    _ProviderCircuitBreaker.record_success("fake")
    assert _ProviderCircuitBreaker.is_open("fake") is False


def test_breaker_is_keyed_by_provider_name():
    _ProviderCircuitBreaker.record_failure("finnhub")
    _ProviderCircuitBreaker.record_failure("finnhub")
    assert _ProviderCircuitBreaker.is_open("finnhub") is True
    assert _ProviderCircuitBreaker.is_open("newsapi") is False


def test_cooldown_auto_closes_breaker(monkeypatch):
    base = time.monotonic()
    fake_now = {"t": base}

    def _clock():
        return fake_now["t"]

    monkeypatch.setattr("app.data_sources.base.time.monotonic", _clock)

    _ProviderCircuitBreaker.record_failure("fake")
    _ProviderCircuitBreaker.record_failure("fake")
    assert _ProviderCircuitBreaker.is_open("fake") is True

    fake_now["t"] = base + _ProviderCircuitBreaker.COOLDOWN_SECONDS + 1
    assert _ProviderCircuitBreaker.is_open("fake") is False


def test_request_records_failure_after_exhausting_retries():
    provider = _FakeProvider(
        outcomes=[requests.exceptions.Timeout()],
        max_retries=1,
    )
    with pytest.raises(ProviderError):
        provider.call()
    # One terminal failure recorded, but breaker not yet open (needs 2).
    assert _ProviderCircuitBreaker.is_open("fake") is False

    provider2 = _FakeProvider(
        outcomes=[requests.exceptions.Timeout()],
        max_retries=1,
    )
    with pytest.raises(ProviderError):
        provider2.call()
    assert _ProviderCircuitBreaker.is_open("fake") is True


def test_open_breaker_short_circuits_without_network_call():
    _ProviderCircuitBreaker.record_failure("fake")
    _ProviderCircuitBreaker.record_failure("fake")

    provider = _FakeProvider(outcomes=[], max_retries=1)
    with pytest.raises(ProviderError, match="circuit breaker open"):
        provider.call()
    # Zero network calls because the breaker short-circuited.
    assert provider._session.calls == 0


def test_request_success_clears_prior_failure():
    _ProviderCircuitBreaker.record_failure("fake")
    provider = _FakeProvider(
        outcomes=[_FakeResponse({"ok": True})],
        max_retries=1,
    )
    result = provider.call()
    assert result == {"ok": True}
    # A single recovery success wipes the prior failure count.
    assert "fake" not in _ProviderCircuitBreaker._state


def test_breaker_state_shared_across_instances_of_same_provider():
    """Two FinnhubProvider instances must share breaker state so a trip
    in MarketDataService's instance also protects NewsDataService's.
    """
    first = _FakeProvider(
        outcomes=[
            requests.exceptions.Timeout(),
            requests.exceptions.Timeout(),
        ],
        max_retries=1,
    )
    with pytest.raises(ProviderError):
        first.call()
    with pytest.raises(ProviderError):
        first.call()
    assert _ProviderCircuitBreaker.is_open("fake") is True

    # Second instance (different object, same provider name) inherits the
    # open breaker and short-circuits without making a request.
    second = _FakeProvider(outcomes=[], max_retries=1)
    with pytest.raises(ProviderError, match="circuit breaker open"):
        second.call()
    assert second._session.calls == 0
