"""LLM render layer for richer morning/weekend email copy.

This module is intentionally render-only:
- selection/ranking stay deterministic upstream
- LLM output is validated against deterministic payload allow-lists
- deterministic formatter remains the fallback on any failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
import re
from typing import Any

import requests

from app.logger import get_logger
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import ChartAsset, EmailRenderResult
from app.schemas.events import NormalisedEvent, QuoteData
from app.settings import Settings

logger = get_logger("llm_email")

_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_PAREN_TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)")
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{1,6})\b")
_NUMERIC_TOKEN_RE = re.compile(r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:%|bp|bps|x|k|m|b|bn|t)?", re.IGNORECASE)


@dataclass
class LLMRenderPayload:
    """Deterministic payload + validation allow-lists for render-time checks."""

    prompt: dict[str, Any]
    ordered_allowed_urls: list[str] = field(default_factory=list)
    allowed_url_set: set[str] = field(default_factory=set)
    allowed_tickers: set[str] = field(default_factory=set)
    allowed_numeric_tokens: set[str] = field(default_factory=set)


@dataclass
class LLMRenderDecision:
    """Result of the render decision after shadow/live/fallback handling."""

    active_email: EmailRenderResult
    mode: str
    reason: str = ""
    validation_errors: list[str] = field(default_factory=list)
    shadow_preview: EmailRenderResult | None = None


class LLMEmailRenderer:
    """Render morning email prose via LLM with strict guardrails and fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def render_morning(
        self,
        *,
        briefing: MorningBriefing,
        deterministic_email: EmailRenderResult,
        selected_events: list[NormalisedEvent],
    ) -> LLMRenderDecision:
        """Return active email content after LLM render attempt and safeguards."""
        if not self.settings.enable_llm_email_render:
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="disabled",
                reason="LLM email render disabled",
            )
        if not self.settings.openai_api_key:
            logger.warning("LLM email render enabled but OPENAI_API_KEY missing; using deterministic email.")
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="fallback",
                reason="OPENAI_API_KEY missing",
            )

        payload = self._build_payload(briefing, selected_events)
        if not payload.prompt.get("events"):
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="fallback",
                reason="No events available for LLM render payload",
            )

        try:
            raw_candidate = self._request_llm(payload.prompt)
        except Exception as exc:
            logger.warning("LLM email render request failed; using deterministic fallback (%s)", exc)
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="fallback",
                reason=f"LLM request failed: {exc}",
            )

        candidate = self._coerce_candidate(raw_candidate)
        errors, source_urls = self._validate_candidate(candidate, payload)
        if errors:
            logger.warning(
                "LLM email render validation failed; using deterministic fallback (%s)",
                "; ".join(errors),
            )
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="fallback",
                reason="Validation failed",
                validation_errors=errors,
            )

        rendered = self._build_render_result(
            subject=candidate["subject"],
            body=candidate["body"],
            source_urls=source_urls,
            inline_assets=deterministic_email.inline_assets,
            session_mode=briefing.session_mode,
        )

        if self.settings.llm_render_shadow_mode:
            logger.info("LLM email render succeeded in shadow mode; deterministic email remains active.")
            return LLMRenderDecision(
                active_email=deterministic_email,
                mode="shadow",
                reason="Shadow mode enabled",
                shadow_preview=rendered,
            )

        logger.info("LLM email render succeeded in live mode; rendered email is active.")
        return LLMRenderDecision(
            active_email=rendered,
            mode="live",
            reason="LLM render active",
        )

    def _build_payload(
        self,
        briefing: MorningBriefing,
        selected_events: list[NormalisedEvent],
    ) -> LLMRenderPayload:
        section_hints = self._section_hints(briefing)
        market_lines = self._format_market_lines(briefing.market_setup.index_quotes, briefing.market_setup.macro_quotes)
        macro_lines = [
            f"{point.name}: {point.value:.4f}"
            for point in briefing.macro_context[:6]
            if point.name
        ]

        event_rows: list[dict[str, Any]] = []
        ordered_urls: list[str] = []
        allowed_urls: set[str] = set()
        allowed_tickers: set[str] = set()
        numeric_source_parts: list[str] = []

        for event in selected_events[:24]:
            tracking_id = event.cluster_id or event.content_hash or event.event_id
            section = section_hints.get(tracking_id, "general")
            summary = self._clean_summary(event.summary)
            row = {
                "id": tracking_id,
                "section": section,
                "title": event.title,
                "summary": summary,
                "tickers": event.tickers[:6],
                "source": event.source,
                "event_type": event.event_type,
                "published_at": event.published_at.isoformat() if event.published_at else "",
                "url": event.url or "",
            }
            event_rows.append(row)

            allowed_tickers.update(event.tickers)
            if event.url and event.url not in allowed_urls:
                ordered_urls.append(event.url)
                allowed_urls.add(event.url)
            numeric_source_parts.append(event.title)
            if summary:
                numeric_source_parts.append(summary)

        allowed_tickers.update(quote.symbol for quote in briefing.market_setup.index_quotes)
        allowed_tickers.update(quote.symbol for quote in briefing.market_setup.macro_quotes)
        allowed_tickers.update(quote.symbol for quote in briefing.watchlist_quotes)
        allowed_tickers.update(quote.symbol for quote in briefing.portfolio_quotes)
        numeric_source_parts.extend(market_lines)
        numeric_source_parts.extend(macro_lines)

        prompt = {
            "session_mode": briefing.session_mode,
            "generated_at_local": briefing.generated_at.isoformat(),
            "market_setup_lines": market_lines[:10],
            "macro_context_lines": macro_lines[:6],
            "events": event_rows,
            "portfolio_focus_ids": [
                event.cluster_id or event.content_hash or event.event_id
                for event in briefing.portfolio_focus
            ],
            "top_theme_ids": [
                event.cluster_id or event.content_hash or event.event_id
                for event in briefing.top_themes
            ],
            "watchlist_ids": [
                event.cluster_id or event.content_hash or event.event_id
                for event in briefing.watchlist_events
            ],
            "allowed_source_urls": ordered_urls,
            "rules": {
                "max_body_chars": self.settings.llm_email_max_chars,
                "min_source_urls": min(3, len(ordered_urls)),
            },
        }

        return LLMRenderPayload(
            prompt=prompt,
            ordered_allowed_urls=ordered_urls,
            allowed_url_set=allowed_urls,
            allowed_tickers={ticker.upper() for ticker in allowed_tickers if ticker},
            allowed_numeric_tokens=self._numeric_allowlist("\n".join(numeric_source_parts)),
        )

    def _request_llm(self, prompt_payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self.settings.llm_api_base_url.rstrip('/')}/chat/completions"
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.llm_email_model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are editing a market-intelligence morning email. "
                            "Use only the supplied payload facts. "
                            "Never invent events, tickers, numbers, or URLs. "
                            "Return strict JSON with keys: subject (string), body (string), source_urls (array of strings). "
                            "Body should be concise, readable on mobile, and under max_body_chars."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii=False),
                    },
                ],
            },
            timeout=self.settings.llm_email_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage", {})
        if usage:
            prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            logger.info(
                "LLM email usage: prompt=%s completion=%s model=%s",
                prompt_tokens,
                completion_tokens,
                self.settings.llm_email_model,
            )
        message_content = payload["choices"][0]["message"]["content"]
        return self._extract_json_object(message_content)

    def _extract_json_object(self, content: str) -> dict[str, Any]:
        raw = (content or "").strip()
        if raw.startswith("```"):
            first_brace = raw.find("{")
            last_brace = raw.rfind("}")
            if first_brace != -1 and last_brace != -1:
                raw = raw[first_brace : last_brace + 1]
        return json.loads(raw)

    def _coerce_candidate(self, raw_candidate: dict[str, Any]) -> dict[str, Any]:
        subject = str(raw_candidate.get("subject", "")).strip()
        body = str(raw_candidate.get("body", "")).strip()
        urls = raw_candidate.get("source_urls")
        source_urls = urls if isinstance(urls, list) else []
        return {
            "subject": subject,
            "body": body,
            "source_urls": [str(url).strip() for url in source_urls if str(url).strip()],
        }

    def _validate_candidate(
        self,
        candidate: dict[str, Any],
        payload: LLMRenderPayload,
    ) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        subject = candidate["subject"]
        body = candidate["body"]
        text = f"{subject}\n{body}".strip()

        if not subject:
            errors.append("Subject is empty")
        if not body:
            errors.append("Body is empty")
        if len(body) > self.settings.llm_email_max_chars:
            errors.append(
                f"Body exceeds max length ({len(body)} > {self.settings.llm_email_max_chars})"
            )

        mentioned_tickers = self._extract_explicit_tickers(text)
        unknown_tickers = sorted(ticker for ticker in mentioned_tickers if ticker not in payload.allowed_tickers)
        if unknown_tickers:
            errors.append(f"Unknown ticker references: {', '.join(unknown_tickers[:6])}")

        body_urls = self._extract_urls(text)
        unknown_body_urls = sorted(url for url in body_urls if url not in payload.allowed_url_set)
        if unknown_body_urls:
            errors.append(f"Unknown body URLs: {', '.join(unknown_body_urls[:3])}")

        normalized_source_urls: list[str] = []
        for url in candidate["source_urls"]:
            if url in payload.allowed_url_set and url not in normalized_source_urls:
                normalized_source_urls.append(url)
            elif url not in payload.allowed_url_set:
                errors.append(f"Unknown source URL: {url}")

        if not normalized_source_urls and payload.ordered_allowed_urls:
            normalized_source_urls = payload.ordered_allowed_urls[: min(3, len(payload.ordered_allowed_urls))]

        required_sources = max(
            0,
            min(
                int(self.settings.llm_email_min_source_urls),
                len(payload.ordered_allowed_urls),
            ),
        )
        if required_sources > 0:
            if len(normalized_source_urls) < required_sources:
                errors.append(
                    f"Insufficient source URLs ({len(normalized_source_urls)} < {required_sources})"
                )

        unknown_numeric_tokens = sorted(
            token
            for token in self._extract_numeric_tokens(text)
            if token not in payload.allowed_numeric_tokens
        )
        if unknown_numeric_tokens:
            errors.append(f"Unknown numeric tokens: {', '.join(unknown_numeric_tokens[:5])}")

        return errors, normalized_source_urls

    def _build_render_result(
        self,
        *,
        subject: str,
        body: str,
        source_urls: list[str],
        inline_assets: list[ChartAsset],
        session_mode: str,
    ) -> EmailRenderResult:
        safe_subject = subject[:120] or "Market Briefing"
        plain_text = body.strip()
        if source_urls:
            plain_text = (
                f"{plain_text}\n\nSources\n"
                + "\n".join(f"- {url}" for url in source_urls)
            )

        html_body = self._build_html_body(
            subject=safe_subject,
            body=body.strip(),
            source_urls=source_urls,
            inline_assets=inline_assets,
            session_mode=session_mode,
        )

        return EmailRenderResult(
            subject=safe_subject,
            plain_text=plain_text,
            html_body=html_body,
            inline_assets=inline_assets,
        )

    def _build_html_body(
        self,
        *,
        subject: str,
        body: str,
        source_urls: list[str],
        inline_assets: list[ChartAsset],
        session_mode: str,
    ) -> str:
        lead = (
            "Weekend mode is active: this note prioritizes Friday close context, portfolio relevance, and next-open setup."
            if session_mode in {"saturday", "sunday"}
            else "This note prioritizes the most material developments from the deterministic morning selection."
        )
        parts = [
            "<html><body style=\"margin:0;padding:0;background:#eef3f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#102a43;\">",
            "<div style=\"max-width:820px;margin:0 auto;padding:24px;\">",
            "<div style=\"background:#0f172a;color:#f8fafc;border-radius:18px;padding:24px 28px;\">",
            f"<div style=\"font-size:28px;font-weight:700;line-height:1.2;\">{html.escape(subject)}</div>",
            f"<div style=\"margin-top:10px;font-size:15px;line-height:1.6;color:#dbeafe;\">{html.escape(lead)}</div>",
            "</div>",
        ]

        if inline_assets:
            parts.append("<div style=\"margin-top:18px;display:grid;gap:18px;\">")
            for asset in inline_assets:
                parts.append(
                    "<div style=\"background:#ffffff;border:1px solid #d8e2ed;border-radius:18px;padding:18px;\">"
                    f"<div style=\"font-size:18px;font-weight:700;color:#102a43;margin-bottom:8px;\">{html.escape(asset.title)}</div>"
                    f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                    "style=\"display:block;width:100%;max-width:760px;border-radius:14px;\">"
                    f"<div style=\"margin-top:10px;font-size:13px;line-height:1.5;color:#486581;\">{html.escape(asset.caption)}</div>"
                    "</div>"
                )
            parts.append("</div>")

        parts.append("<div style=\"margin-top:18px;background:#ffffff;border:1px solid #d8e2ed;border-radius:18px;padding:20px 22px;\">")
        for paragraph in [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]:
            lines = "<br>".join(html.escape(line.strip()) for line in paragraph.splitlines() if line.strip())
            if not lines:
                continue
            parts.append(
                "<p style=\"margin:0 0 14px 0;font-size:15px;line-height:1.65;color:#334e68;\">"
                f"{lines}</p>"
            )
        if source_urls:
            parts.append("<div style=\"margin-top:10px;font-size:13px;color:#486581;font-weight:600;\">Sources</div>")
            parts.append("<ul style=\"margin:6px 0 0 18px;padding:0;\">")
            for url in source_urls:
                safe = html.escape(url)
                parts.append(
                    "<li style=\"margin:0 0 6px 0;font-size:13px;line-height:1.5;\">"
                    f"<a href=\"{safe}\" style=\"color:#0b7285;text-decoration:none;\">{safe}</a>"
                    "</li>"
                )
            parts.append("</ul>")
        parts.append("</div>")
        parts.append("</div></body></html>")
        return "".join(parts)

    def _section_hints(self, briefing: MorningBriefing) -> dict[str, str]:
        hints: dict[str, str] = {}

        def seed(events: list[NormalisedEvent], section: str):
            for event in events:
                tracking_id = event.cluster_id or event.content_hash or event.event_id
                hints.setdefault(tracking_id, section)

        seed(briefing.portfolio_focus, "portfolio_focus")
        seed(briefing.top_themes, "top_themes")
        seed(briefing.watchlist_events, "watchlist")
        for snapshot in briefing.sector_scan:
            seed(snapshot.top_events, f"sector:{snapshot.sector_key}")
        return hints

    def _format_market_lines(self, index_quotes: list[QuoteData], macro_quotes: list[QuoteData]) -> list[str]:
        lines: list[str] = []
        for quote in (index_quotes[:4] + macro_quotes[:4]):
            sign = "+" if quote.change_percent >= 0 else ""
            lines.append(
                f"{quote.display_name or quote.symbol}: {quote.current_price:.2f} ({sign}{quote.change_percent:.2f}%)"
            )
        return lines

    @staticmethod
    def _clean_summary(summary: str) -> str:
        cleaned = (summary or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:280]

    def _numeric_allowlist(self, text: str) -> set[str]:
        return {
            token
            for token in self._extract_numeric_tokens(text)
            if token
        }

    def _extract_numeric_tokens(self, text: str) -> set[str]:
        tokens: set[str] = set()
        for raw in _NUMERIC_TOKEN_RE.findall(text or ""):
            token = self._normalise_numeric_token(raw)
            if not token:
                continue
            if self._is_significant_numeric_token(token):
                tokens.add(token)
        return tokens

    @staticmethod
    def _normalise_numeric_token(token: str) -> str:
        normalized = token.strip().lower().replace(" ", "").replace(",", "")
        if normalized.startswith("+"):
            normalized = normalized[1:]
        return normalized

    @staticmethod
    def _is_significant_numeric_token(token: str) -> bool:
        if "%" in token or any(sign in token for sign in ("$", "€", "£")):
            return True
        if "." in token:
            return True
        if any(token.endswith(suffix) for suffix in ("bp", "bps", "x", "k", "m", "b", "bn", "t")):
            return True
        return False

    @staticmethod
    def _extract_explicit_tickers(text: str) -> set[str]:
        tickers = set(_PAREN_TICKER_RE.findall(text or ""))
        tickers.update(_DOLLAR_TICKER_RE.findall(text or ""))
        return {ticker.upper() for ticker in tickers if ticker}

    @staticmethod
    def _extract_urls(text: str) -> set[str]:
        return {url.strip().rstrip(").,") for url in _URL_RE.findall(text or "")}
