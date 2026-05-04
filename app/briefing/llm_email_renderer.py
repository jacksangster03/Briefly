"""LLM render layer for richer morning/weekend email copy.

This module is intentionally render-only:
- selection/ranking stay deterministic upstream
- LLM output is validated against deterministic payload allow-lists
- deterministic formatter remains the fallback on any failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import json
import re
from typing import Any

import requests

from app.briefing.morning_charts import chart_summary_lines
from app.logger import get_logger
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import ChartAsset, EmailRenderResult
from app.schemas.events import NormalisedEvent, QuoteData
from app.settings import Settings

logger = get_logger("llm_email")

_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_PAREN_TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)")
_DOLLAR_TICKER_RE = re.compile(r"\$([A-Z]{1,6})\b")
_EXCHANGE_TICKER_RE = re.compile(r"\((?:NASDAQ|NYSE|LSE|TSX|ASX|HKEX|OTC):([A-Z0-9.\-]{1,10})\)", re.IGNORECASE)
_NUMERIC_TOKEN_RE = re.compile(r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?(?:%|bp|bps|x|k|m|b|bn|t)?", re.IGNORECASE)
_NORMALISED_NUMERIC_RE = re.compile(r"^([€$£]?)(-?\d+(?:\.\d+)?)(%|bp|bps|x|k|m|b|bn|t)?$")


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
        enabled_override: bool | None = None,
        shadow_mode_override: bool | None = None,
    ) -> LLMRenderDecision:
        """Return active email content after LLM render attempt and safeguards."""
        llm_enabled = (
            enabled_override
            if enabled_override is not None
            else self.settings.enable_llm_email_render
        )
        llm_shadow_mode = (
            shadow_mode_override
            if shadow_mode_override is not None
            else self.settings.llm_render_shadow_mode
        )

        if not llm_enabled:
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

        if llm_shadow_mode:
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
        macro_lines_with_deltas = self._format_macro_lines_with_deltas(briefing.macro_context[:8])
        # Legacy plain lines for allowlist seeding
        macro_lines_plain = [
            f"{point.name}: {point.value:.4f}"
            for point in briefing.macro_context[:8]
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
            allowed_tickers.update(self._extract_explicit_tickers(f"{event.title}\n{summary}"))
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
        numeric_source_parts.extend(macro_lines_plain)

        commodity_lines = self._format_commodity_lines(briefing.commodity_strip)
        numeric_source_parts.extend(commodity_lines)

        freshness_label, age_hours = self._quotes_freshness_label(briefing)
        yield_curve_plain = self._yield_curve_plain(briefing)
        if yield_curve_plain:
            numeric_source_parts.append(yield_curve_plain)

        prompt = {
            "session_mode": briefing.session_mode,
            "generated_at_local": briefing.generated_at.isoformat(),
            "quotes_freshness": freshness_label,
            "quotes_age_hours": age_hours,
            # --- Market context ---
            "market_setup_lines": market_lines[:10],
            "macro_context_lines": macro_lines_with_deltas[:8],
            "commodity_lines": commodity_lines[:5],
            "yield_curve_plain": yield_curve_plain,
            # --- Regime & quality signals ---
            "geo_risk_level": briefing.geo_risk_level or "UNKNOWN",
            "geo_risk_summary": briefing.geo_risk_summary or "",
            "dominant_tape_driver": briefing.dominant_tape_driver or "No single equity catalyst dominates; cross-asset pressure is the main driver.",
            "session_quality_bucket": briefing.session_quality_bucket or "MIXED",
            "session_quality_label": briefing.session_quality_label or "Mixed tape",
            "regime_snapshot": briefing.regime_snapshot,
            "regime_shift": briefing.regime_shift,
            # --- Headline density ---
            "headline_density_label": self._headline_density_label(briefing),
            # --- Chart context ---
            "chart_regime_tags": list((briefing.morning_chart_bundle or {}).get("regime_tags") or []),
            "chart_summaries": chart_summary_lines(briefing.morning_chart_bundle or {}, limit=5),
            # --- Events ---
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
                "llm_role": "prose_only",
                "deterministic_contract": (
                    "LLM cannot choose chart types, chart order, values, rankings, labels, annotations, or regime classification."
                ),
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
                            "You are the institutional narrator for a morning market briefing email.\n\n"
                            "INPUT: A JSON payload with market data, macro context, geo risk, news events, and portfolio signals.\n\n"
                            "OUTPUT: Return strict JSON with keys: subject (string), body (string), source_urls (array of strings).\n\n"
                            "NARRATIVE STRUCTURE – write the body in this sequence:\n"
                            "1. DOMINANT DRIVER (1 sentence): Name the 1-2 real drivers using payload.dominant_tape_driver as your anchor. "
                            "If it says 'No single dominant driver', write exactly that.\n"
                            "2. SETUP READ (3-5 sentences): State the regime. "
                            "If quotes_freshness is 'prior close', frame all moves as 'vs prior close' or 'vs Friday\'s close', never as 'this morning' or 'today'. "
                            "If an index move is below 0.3% or a yield move is below 0.05%, acknowledge direction but do not call it a driver.\n"
                            "3. YIELDS AND CURVE (2-3 sentences): State where the 2Y and 10Y roughly sit. "
                            "Use yield_curve_plain from the payload as your base. "
                            "Only say the curve is 'flattening' or 'steepening' if the spread change is at least 0.02 and directionally consistent; otherwise say 'little changed'.\n"
                            "4. GEO RISK (1-2 sentences): Use geo_risk_level and geo_risk_summary from the payload. "
                            "If oil is above 90 or oil change is above 2% and geo headlines are present, do not say 'low'; say 'elevated but not panic' even if VIX is moderate. "
                            "If headline_density_label contains 'unavailable', say 'headline signal is thin or stale'.\n"
                            "5. PORTFOLIO BULLETS (2-3 bullets): Link named market dynamics to holdings. One sentence per bullet.\n\n"
                            "RULES:\n"
                            "- Use only payload facts. Never invent numbers, tickers, or URLs.\n"
                            "- Never prefix numbers with currency symbols ($, €, £). Write 'WTI at 83.2', not '$83.2'.\n"
                            "- Do not repeat raw numbers already shown in deterministic tables; interpret instead.\n"
                            "- Never contradict the numeric payload.\n"
                            "- You are prose-only. Never choose or alter chart types/order/values/labels/regimes.\n"
                            "- Body must be under max_body_chars."
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
            total_tokens = int(prompt_tokens) + int(completion_tokens)
            estimated_cost = self._estimate_usage_cost(
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
            )
            if estimated_cost is None:
                logger.info(
                    "LLM email usage: prompt=%s completion=%s total=%s model=%s",
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    self.settings.llm_email_model,
                )
            else:
                logger.info(
                    "LLM email usage: prompt=%s completion=%s total=%s model=%s est_cost_usd=%.6f",
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    self.settings.llm_email_model,
                    estimated_cost,
                )
        message_content = payload["choices"][0]["message"]["content"]
        return self._extract_json_object(message_content)

    def _estimate_usage_cost(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float | None:
        """Estimate USD cost when per-1M token rates are configured."""
        input_rate = float(self.settings.llm_email_input_cost_per_1m_tokens or 0.0)
        output_rate = float(self.settings.llm_email_output_cost_per_1m_tokens or 0.0)
        if input_rate <= 0 or output_rate <= 0:
            return None
        input_cost = (max(0, int(prompt_tokens)) / 1_000_000) * input_rate
        output_cost = (max(0, int(completion_tokens)) / 1_000_000) * output_rate
        return input_cost + output_cost

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
            if not self._numeric_token_allowed(token, payload.allowed_numeric_tokens)
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
            "<html><body style=\"margin:0;padding:0;background:#030A12;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#EAF2FF;\">",
            "<div style=\"max-width:820px;margin:0 auto;padding:16px 14px;background:#06111F;\">",
            "<div style=\"background:#071423;color:#EAF2FF;padding:18px 20px;border-top:3px solid #FF7A00;\">",
            f"<div style=\"font-size:24px;font-weight:800;line-height:1.2;\">{html.escape(subject)}</div>",
            f"<div style=\"margin-top:8px;font-size:14px;line-height:1.45;color:#A9B8C8;\">{html.escape(lead)}</div>",
            "</div>",
        ]

        if inline_assets:
            parts.append("<div style=\"margin-top:12px;\">")
            for asset in inline_assets:
                parts.append(
                    "<div style=\"background:#03101D;border:1px solid #153047;padding:14px 14px 12px 14px;margin-bottom:10px;\">"
                    f"<div style=\"font-size:15px;font-weight:800;color:#EAF2FF;margin-bottom:7px;\">{html.escape(asset.title)}</div>"
                    f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                    "style=\"display:block;width:100%;max-width:760px;height:auto;\">"
                    f"<div style=\"margin-top:8px;font-size:12px;line-height:1.4;color:#A9B8C8;\">{html.escape(asset.caption)}</div>"
                    "</div>"
                )
            parts.append("</div>")

        parts.append("<div style=\"margin-top:12px;background:#071423;border:1px solid #153047;padding:14px 16px;\">")
        for paragraph in [chunk.strip() for chunk in body.split("\n\n") if chunk.strip()]:
            lines = "<br>".join(html.escape(line.strip()) for line in paragraph.splitlines() if line.strip())
            if not lines:
                continue
            parts.append(
                "<p style=\"margin:0 0 10px 0;font-size:13px;line-height:1.4;color:#EAF2FF;\">"
                f"{lines}</p>"
            )
        if source_urls:
            parts.append("<div style=\"margin-top:10px;font-size:12px;color:#A9B8C8;font-weight:700;\">Sources</div>")
            parts.append("<ul style=\"margin:6px 0 0 18px;padding:0;\">")
            for url in source_urls:
                safe = html.escape(url)
                parts.append(
                    "<li style=\"margin:0 0 6px 0;font-size:12px;line-height:1.4;\">"
                    f"<a href=\"{safe}\" style=\"color:#6FA8E8;text-decoration:none;\">{safe}</a>"
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

    def _format_macro_lines_with_deltas(self, points: list) -> list[str]:
        lines: list[str] = []
        for p in points:
            if not p.name:
                continue
            if p.change is not None:
                lines.append(f"{p.name}: {p.value:.4f} (delta {p.change:+.4f})")
            else:
                lines.append(f"{p.name}: {p.value:.4f}")
        return lines

    def _format_commodity_lines(self, strip: list) -> list[str]:
        lines: list[str] = []
        for p in strip:
            name = p.name or p.series_id or ""
            if not name:
                continue
            if p.change_percent is not None:
                sign = "+" if p.change_percent >= 0 else ""
                lines.append(f"{name}: {p.value:.2f} ({sign}{p.change_percent:.2f}%)")
            else:
                lines.append(f"{name}: {p.value:.2f}")
        return lines

    def _quotes_freshness_label(self, briefing: MorningBriefing) -> tuple[str, float]:
        from datetime import timezone
        now = datetime.now(timezone.utc)
        generated = briefing.generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_hours = (now - generated).total_seconds() / 3600
        label = "prior close" if age_hours > 8 or briefing.session_mode in {"saturday", "sunday"} else "live"
        return label, round(age_hours, 1)

    def _yield_curve_plain(self, briefing: MorningBriefing) -> str:
        macro = briefing.macro_context or []
        ten_y = next((p for p in macro if "10y treasury" in (p.name or "").lower() or "dgs10" in (p.series_id or "").lower()), None)
        two_y = next((p for p in macro if "2y treasury" in (p.name or "").lower() or "dgs2" in (p.series_id or "").lower()), None)
        spread = next((p for p in macro if "10y-2y" in (p.name or "").lower() or "t10y2y" in (p.series_id or "").lower()), None)

        parts: list[str] = []
        if two_y:
            parts.append(f"2Y at {two_y.value:.2f}%")
        if ten_y:
            parts.append(f"10Y at {ten_y.value:.2f}%")
        if not parts:
            return ""

        base = ", ".join(parts)
        if not spread:
            return base

        spread_val = float(spread.value or 0.0)
        spread_change = float(spread.change or 0.0)
        ten_y_change = float(ten_y.change or 0.0) if ten_y else None
        two_y_change = float(two_y.change or 0.0) if two_y else None

        if abs(spread_change) < 0.02:
            curve_desc = "little changed"
        elif spread_change > 0 and ten_y_change is not None and two_y_change is not None and ten_y_change > two_y_change:
            curve_desc = "steepening slightly"
        elif spread_change < 0 and ten_y_change is not None and two_y_change is not None and two_y_change > ten_y_change:
            curve_desc = "flattening slightly"
        else:
            curve_desc = "little changed"

        if spread_val > 0.15:
            shape = "normal upward-sloping"
        elif spread_val > -0.05:
            shape = "flat"
        else:
            shape = "inverted"
        return f"{base}; 10Y-2Y spread ~{spread_val:.2f}% ({shape} curve, {curve_desc})"

    def _headline_density_label(self, briefing: MorningBriefing) -> str:
        events = list(briefing.global_news or []) + list(briefing.top_themes or [])
        if not events:
            return "unavailable (no events passed filters)"
        count = len(events)
        if count <= 2:
            return f"low ({count} market-relevant stories)"
        if count <= 8:
            return f"moderate ({count} stories)"
        return f"elevated ({count}+ stories)"

    def _format_market_lines(self, index_quotes: list[QuoteData], macro_quotes: list[QuoteData]) -> list[str]:
        lines: list[str] = []
        for quote in (index_quotes[:12] + macro_quotes[:8]):
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
        match = _NORMALISED_NUMERIC_RE.match(normalized)
        if not match:
            return normalized
        currency, number_part, suffix = match.groups()
        if "." in number_part:
            number_part = number_part.rstrip("0").rstrip(".")
        suffix = suffix or ""
        return f"{currency}{number_part}{suffix}"
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
    def _coerce_numeric_value(token: str) -> float | None:
        """Extract numeric value from a normalized token for equivalence checks."""
        stripped = token.strip().lower()
        if stripped.startswith("+"):
            stripped = stripped[1:]
        if stripped.startswith("$") or stripped.startswith("€") or stripped.startswith("£"):
            stripped = stripped[1:]
        if stripped.endswith("%"):
            stripped = stripped[:-1]
        suffixes = ("bps", "bp", "bn", "k", "m", "b", "t", "x")
        for suffix in suffixes:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)]
                break
        try:
            return float(stripped)
        except ValueError:
            return None

    def _numeric_token_allowed(self, token: str, allowed_tokens: set[str]) -> bool:
        """Allow strict matches plus safe percent/no-percent equivalents for rates."""
        if token in allowed_tokens:
            return True
        numeric_value = self._coerce_numeric_value(token)
        if numeric_value is None:
            return False
        # Permit 4.29 <-> 4.29% style swaps for yield/rate-scale values only.
        if token.endswith("%"):
            alt = token[:-1]
            if alt in allowed_tokens:
                return True
        else:
            alt = f"{token}%"
            if alt in allowed_tokens:
                return True

        # Accept rounded deterministic values (e.g. 17.74 -> 17.7).
        # Keep this narrow to avoid admitting invented numbers.
        for allowed in allowed_tokens:
            allowed_value = self._coerce_numeric_value(allowed)
            if allowed_value is None:
                continue
            if (allowed.endswith("%") != token.endswith("%")) and abs(numeric_value) > 30:
                # Only allow percent/no-percent swaps for rate-scale numbers.
                continue
            tolerance = 0.05 if abs(allowed_value) < 1 else 0.10 if abs(allowed_value) < 10 else 0.25
            if abs(numeric_value - allowed_value) <= tolerance:
                return True
        return False

    @staticmethod
    def _extract_explicit_tickers(text: str) -> set[str]:
        tickers = set(_PAREN_TICKER_RE.findall(text or ""))
        tickers.update(_DOLLAR_TICKER_RE.findall(text or ""))
        tickers.update(_EXCHANGE_TICKER_RE.findall(text or ""))
        return {ticker.upper() for ticker in tickers if ticker}

    @staticmethod
    def _extract_urls(text: str) -> set[str]:
        return {url.strip().rstrip(").,") for url in _URL_RE.findall(text or "")}
