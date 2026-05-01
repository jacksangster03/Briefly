"""Email rendering for institutional-style morning briefs."""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime

from zoneinfo import ZoneInfo

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import EmailRenderResult
from app.schemas.events import QuoteData

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_HEADER_RE = re.compile(r"^<b>([^<]+)</b>\s*$")


class EmailFormatter:
    """Render Outlook-safe HTML email while preserving Telegram narrative."""

    def __init__(self, timezone_name: str = "UTC"):
        self.telegram_formatter = TelegramFormatter(timezone_name)
        self.timezone_name = timezone_name

    def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
        messages = self.telegram_formatter.format_morning_briefing(briefing)
        full_html = "\n\n".join(messages)
        subject = self._subject(briefing)
        return EmailRenderResult(
            subject=subject,
            plain_text=_HTML_TAG_RE.sub("", full_html),
            html_body=self._html_body(briefing, full_html, subject),
            inline_assets=briefing.chart_assets,
        )

    def _subject(self, briefing: MorningBriefing) -> str:
        date_str = briefing.generated_at.strftime("%a %d %b")
        if briefing.session_mode in {"saturday", "sunday"}:
            return f"Weekend Briefing | {date_str}"
        return f"Morning Briefing | {date_str}"

    def _html_body(self, briefing: MorningBriefing, full_html: str, subject: str) -> str:
        regime_tags = list((briefing.morning_chart_bundle or {}).get("regime_tags") or [])
        bundle_meta = dict((briefing.morning_chart_bundle or {}).get("meta") or {})
        delivery_mode = str(bundle_meta.get("delivery_mode") or "deterministic")
        llm_shadow = bool(bundle_meta.get("llm_shadow_mode", True))
        profile_name = str(bundle_meta.get("profile_name") or "default_user")
        confidence = str(bundle_meta.get("data_confidence") or "medium").upper()
        lead = "Desk-note view: deterministic chart stack + high-signal narrative."
        freshness = self._freshness_summary(briefing)
        generated_local = self._format_local(briefing.generated_at)

        parts = [
            "<html><body style=\"margin:0;padding:0;background:#ECEFF3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#0E2438;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#ECEFF3;\">",
            "<tr><td align=\"center\" style=\"padding:16px;\">",
            "<table role=\"presentation\" width=\"780\" cellspacing=\"0\" cellpadding=\"0\" style=\"width:780px;max-width:780px;background:#F8FAFC;border:1px solid #CED6E1;border-radius:8px;overflow:hidden;\">",
            "<tr><td style=\"padding:14px 18px;border-bottom:1px solid #D6DEE8;background:#F2F6FB;\">",
            f"<div style=\"font-size:13px;font-weight:600;color:#2A415A;letter-spacing:0.02em;\">{html.escape(subject)}</div>",
            f"<div style=\"margin-top:2px;font-size:11.5px;color:#4E627A;\">{html.escape(lead)}</div>",
            "</td></tr>",
            "<tr><td style=\"padding:10px 18px;border-bottom:1px solid #D6DEE8;background:#FFFFFF;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:11.5px;color:#3C546D;\">Generated: <strong>{html.escape(generated_local)}</strong></td>",
            f"<td align=\"right\" style=\"font-size:11.5px;color:#3C546D;\">Profile: <strong>{html.escape(profile_name)}</strong></td>",
            "</tr><tr>",
            f"<td style=\"padding-top:4px;font-size:11.5px;color:#3C546D;\">Mode: <strong>{html.escape(delivery_mode)}</strong> · LLM shadow: <strong>{'on' if llm_shadow else 'off'}</strong></td>",
            f"<td align=\"right\" style=\"padding-top:4px;font-size:11.5px;color:#3C546D;\">Data confidence: <strong>{html.escape(confidence)}</strong></td>",
            "</tr></table>",
            "</td></tr>",
            "<tr><td style=\"padding:10px 18px;border-bottom:1px solid #D6DEE8;background:#FFFFFF;\">",
            f"<div style=\"font-size:11.5px;color:#3C546D;\">{html.escape(freshness)}</div>",
            "</td></tr>",
        ]

        if regime_tags:
            parts.append("<tr><td style=\"padding:8px 18px;border-bottom:1px solid #D6DEE8;background:#FFFFFF;\">")
            parts.append("<div style=\"font-size:11px;color:#3C546D;\">Regime tags: ")
            for tag in regime_tags:
                parts.append(
                    "<span style=\"display:inline-block;margin:0 4px 4px 0;padding:2px 6px;border:1px solid #CAD5E2;border-radius:999px;background:#F2F6FB;font-size:10.5px;color:#2A415A;\">"
                    f"{html.escape(tag)}</span>"
                )
            parts.append("</div></td></tr>")

        if briefing.chart_assets:
            parts.append("<tr><td style=\"padding:12px 16px 4px 16px;background:#F8FAFC;\">")
            parts.append(self._chart_modules(briefing))
            parts.append("</td></tr>")

        parts.append("<tr><td style=\"padding:8px 16px 16px 16px;background:#F8FAFC;\">")
        parts.append(self._brief_modules(full_html))
        parts.append("</td></tr>")

        parts.extend(["</table>", "</td></tr></table>", "</body></html>"])
        return "".join(parts)

    def _chart_modules(self, briefing: MorningBriefing) -> str:
        modules = [
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:separate;border-spacing:0 10px;\">"
        ]
        for asset in briefing.chart_assets:
            modules.append("<tr><td style=\"background:#FFFFFF;border:1px solid #D4DCE7;border-radius:8px;padding:10px 12px;\">")
            modules.append(
                f"<div style=\"font-size:13px;font-weight:700;color:#18324D;letter-spacing:0.01em;\">{html.escape(asset.title)}</div>"
            )
            modules.append(
                f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                "style=\"display:block;width:100%;max-width:748px;margin-top:8px;border:1px solid #E1E7F0;border-radius:4px;\">"
            )
            if asset.caption:
                modules.append(
                    f"<div style=\"margin-top:7px;font-size:11.5px;line-height:1.5;color:#4D6078;\">{html.escape(asset.caption)}</div>"
                )
            modules.append("</td></tr>")
        modules.append("</table>")
        return "".join(modules)

    def _brief_modules(self, full_html: str) -> str:
        sections = [s.strip() for s in full_html.split("\n\n") if s.strip()]
        return "".join(self._section_module(s) for s in sections)

    def _section_module(self, section_html: str) -> str:
        lines = section_html.split("\n")
        first = lines[0] if lines else ""
        header_match = _BOLD_HEADER_RE.match(first)
        if header_match and len(lines) > 1:
            header_text = header_match.group(1)
            body = "<br>".join(line for line in lines[1:] if line is not None)
            return (
                "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
                "<tr><td style=\"background:#FFFFFF;border:1px solid #D4DCE7;border-radius:8px;padding:10px 12px;font-size:13px;color:#112C44;line-height:1.55;\">"
                f"<div style=\"font-size:12px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:#294561;margin-bottom:6px;\">{html.escape(header_text)}</div>"
                f"{body}"
                "</td></tr></table>"
            )
        body = "<br>".join(lines)
        return (
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
            "<tr><td style=\"background:#FFFFFF;border:1px solid #D4DCE7;border-radius:8px;padding:10px 12px;font-size:13px;color:#112C44;line-height:1.55;\">"
            f"{body}"
            "</td></tr></table>"
        )

    def _freshness_summary(self, briefing: MorningBriefing) -> str:
        quotes = self._freshness_quotes(briefing)
        if not quotes:
            return "Quotes as of unavailable; sources: none"
        latest_ts = max((quote.timestamp for quote in quotes if quote.timestamp), default=None)
        timestamp = self._format_local(latest_ts) if latest_ts else "unavailable"
        counter = Counter((quote.source or "unknown").strip().lower() or "unknown" for quote in quotes)
        src = ", ".join(f"{name}({count})" for name, count in sorted(counter.items()))
        return f"Quotes as of {timestamp} · sources: {src}"

    def _freshness_quotes(self, briefing: MorningBriefing) -> list[QuoteData]:
        preferred = list(briefing.watchlist_quotes or briefing.portfolio_quotes)
        if preferred:
            return preferred
        return list(briefing.market_setup.index_quotes or [])

    def _format_local(self, dt: datetime | None) -> str:
        if dt is None:
            return "unavailable"
        tz = ZoneInfo(self.timezone_name)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        local = dt.astimezone(tz)
        return local.strftime("%Y-%m-%d %H:%M %Z")
