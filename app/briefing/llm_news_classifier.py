"""Optional LLM-assisted news classifier (shadow-first, deterministic-safe)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import request

from app.llm.usage_tracker import log_llm_usage, query_monthly_spend
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.settings import Settings

logger = get_logger("llm.news")


def run_llm_news_classifier_shadow(
    *,
    settings: Settings,
    profile_name: str,
    session_key: str,
    local_date,
    events: list[NormalisedEvent],
) -> dict[str, Any]:
    """Run optional LLM news classification in shadow mode.

    Never raises and never changes deterministic output. Used for audit only.
    """
    if not settings.enable_llm_news_classifier:
        return {"enabled": False, "reason": "disabled"}
    if not settings.openai_api_key:
        return {"enabled": True, "executed": False, "reason": "missing_api_key"}
    if settings.llm_news_classifier_max_items_per_run <= 0:
        return {"enabled": True, "executed": False, "reason": "max_items_zero"}

    budget = float(settings.llm_news_classifier_monthly_budget_usd or 0.0)
    if budget > 0:
        now = datetime.now(timezone.utc)
        spent = query_monthly_spend(profile_name, now.year, now.month)
        if spent is not None and spent >= budget:
            return {"enabled": True, "executed": False, "reason": "budget_exceeded", "spent": spent}

    candidates = events[: settings.llm_news_classifier_max_items_per_run]
    payload = [
        {
            "event_id": e.event_id,
            "title": e.title,
            "summary": e.summary,
            "event_type": e.event_type,
            "tickers": e.tickers,
        }
        for e in candidates
    ]
    try:
        result = _call_llm_news_classifier(settings, payload)
        usage = result.get("usage", {})
        log_llm_usage(
            profile_name=profile_name,
            session_key=session_key,
            local_date=local_date,
            model=settings.llm_news_classifier_model,
            call_type="news_classifier",
            mode="shadow" if settings.llm_news_classifier_shadow_mode else "live_noop",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            estimated_cost_usd=None,
        )
        return {
            "enabled": True,
            "executed": True,
            "shadow_mode": bool(settings.llm_news_classifier_shadow_mode),
            "items": result.get("items", []),
        }
    except Exception as exc:
        logger.warning("LLM news classifier failed; deterministic path unchanged (%s)", exc)
        return {"enabled": True, "executed": False, "reason": f"error:{exc}"}


def _call_llm_news_classifier(settings: Settings, payload: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = {
        "task": "Classify market-news stories into hard catalyst vs commentary and likely story_type.",
        "items": payload,
    }
    req_body = {
        "model": settings.llm_news_classifier_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Classify each item and return compact JSON."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
    }
    req = request.Request(
        f"{settings.llm_api_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=max(5, int(settings.llm_email_timeout_seconds))) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    parsed = json.loads(content) if isinstance(content, str) else {}
    parsed["usage"] = raw.get("usage", {})
    return parsed
