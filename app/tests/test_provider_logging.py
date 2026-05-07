from __future__ import annotations

import pytest
import requests

import app.data_sources.base as base_module
from app.data_sources.base import BaseProvider, ProviderError, redact_secret_tokens, redact_url_secrets


def test_redact_url_secrets_masks_sensitive_query_params():
    url = (
        "https://example.com/news?"
        "api_key=abc123&apikey=def456&api_token=tok123&api-token=tok456&apiToken=tok789"
        "&access_key=ghi789&access-token=acc111&access_token=acc222&token=zzz&key=kkk"
        "&authorization=bearer123&plain=x"
    )
    out = redact_url_secrets(url)
    assert "api_key=***" in out
    assert "apikey=***" in out
    assert "api_token=***" in out
    assert "api-token=***" in out
    assert "apiToken=***" in out
    assert "access_key=***" in out
    assert "access-token=***" in out
    assert "access_token=***" in out
    assert "token=***" in out
    assert "key=***" in out
    assert "authorization=***" in out
    assert "plain=x" in out
    assert "abc123" not in out
    assert "def456" not in out
    assert "tok123" not in out
    assert "bearer123" not in out


def test_redact_secret_tokens_masks_inline_pairs():
    text = "failed request ... api_key=abc token=xyz api_token=qwe access_key=123"
    out = redact_secret_tokens(text)
    assert "api_key=***" in out
    assert "token=***" in out
    assert "api_token=***" in out
    assert "access_key=***" in out
    assert "abc" not in out
    assert "xyz" not in out


class _DummyProvider(BaseProvider):
    name = "dummy_provider"

    def is_configured(self) -> bool:
        return True


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code not in (401, 429):
            raise requests.exceptions.HTTPError(
                f"{self.status_code} for https://provider.example/v1/path?api_key=abc&api_token=tok&token=zzz&access_key=acc"
            )

    def json(self) -> dict:
        return {"ok": True}


def test_provider_logs_use_redacted_urls(
    monkeypatch: pytest.MonkeyPatch,
):
    provider = _DummyProvider(timeout=1, max_retries=2)
    monkeypatch.setattr(provider, "_log_health", lambda *args, **kwargs: None)
    warnings: list[str] = []

    def _capture_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else str(msg))

    monkeypatch.setattr(base_module.logger, "warning", _capture_warning)
    calls = {"n": 0}

    def _request(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Response(429)
        return _Response(401)

    monkeypatch.setattr(provider._session, "request", _request)

    with pytest.raises(ProviderError):
        provider._get(
            "https://provider.example/v1/path?api_key=abc&api_token=tok&token=zzz&access_key=acc&plain=ok"
        )

    logged = "\n".join(warnings)
    assert "provider.example/v1/path" in logged
    assert "plain=ok" in logged
    assert "api_key=***" in logged
    assert "api_token=***" in logged
    assert "token=***" in logged
    assert "access_key=***" in logged
    assert "api_key=abc" not in logged
    assert "api_token=tok" not in logged
    assert "token=zzz" not in logged
    assert "access_key=acc" not in logged
