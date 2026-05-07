from __future__ import annotations

from app.data_sources.base import redact_url_secrets


def test_redact_url_secrets_masks_sensitive_query_params():
    url = (
        "https://example.com/news?"
        "api_key=abc123&apikey=def456&access_key=ghi789&token=zzz&key=kkk&plain=x"
    )
    out = redact_url_secrets(url)
    assert "api_key=***" in out
    assert "apikey=***" in out
    assert "access_key=***" in out
    assert "token=***" in out
    assert "key=***" in out
    assert "plain=x" in out
    assert "abc123" not in out
    assert "def456" not in out

