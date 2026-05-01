"""Email rendering.

The Telegram formatter is the single source of truth for brief content.
Email wraps that text in styled section cards and stacks chart assets at
the top so charts are the only thing email adds beyond what Telegram
already delivers.
"""

from __future__ import annotations

import html
import re

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import EmailRenderResult

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_HEADER_RE = re.compile(r"^<b>([^<]+)</b>\s*$")


class EmailFormatter:
    """Render an email body that mirrors the Telegram brief plus charts."""

    def __init__(self, timezone_name: str = "UTC"):
        self.telegram_formatter = TelegramFormatter(timezone_name)

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
        is_weekend = briefing.session_mode in {"saturday", "sunday"}
        lead = (
            "Weekend mode: Friday close context, portfolio relevance, and what to watch into the next reopen."
            if is_weekend
            else "Highest-signal developments from the latest cycle, with charts up top."
        )

        parts = [
            "<html><body style=\"margin:0;padding:0;background:#eef3f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#102a43;\">",
            "<div style=\"max-width:820px;margin:0 auto;padding:24px;\">",
            "<div style=\"background:#0f172a;color:#f8fafc;border-radius:18px;padding:24px 28px;\">",
            f"<div style=\"font-size:28px;font-weight:700;line-height:1.2;\">{html.escape(subject)}</div>",
            f"<div style=\"margin-top:10px;font-size:15px;line-height:1.6;color:#dbeafe;\">{html.escape(lead)}</div>",
            "</div>",
        ]

        if briefing.chart_assets:
            parts.append(self._chart_cards(briefing))

        parts.append(self._brief_as_cards(full_html))

        parts.append("</div></body></html>")
        return "".join(parts)

    def _chart_cards(self, briefing: MorningBriefing) -> str:
        cards = ["<div style=\"margin-top:18px;display:grid;gap:18px;\">"]
        for asset in briefing.chart_assets:
            cards.append(
                "<div style=\"background:#ffffff;border:1px solid #d8e2ed;border-radius:18px;padding:18px;\">"
                f"<div style=\"font-size:18px;font-weight:700;color:#102a43;margin-bottom:8px;\">{html.escape(asset.title)}</div>"
                f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                "style=\"display:block;width:100%;max-width:760px;border-radius:14px;\">"
                f"<div style=\"margin-top:10px;font-size:13px;line-height:1.5;color:#486581;\">{html.escape(asset.caption)}</div>"
                "</div>"
            )
        cards.append("</div>")
        return "".join(cards)

    def _brief_as_cards(self, full_html: str) -> str:
        sections = [s.strip() for s in full_html.split("\n\n") if s.strip()]
        return "".join(self._section_card(s) for s in sections)

    def _section_card(self, section_html: str) -> str:
        lines = section_html.split("\n")
        first = lines[0] if lines else ""
        header_match = _BOLD_HEADER_RE.match(first)
        if header_match and len(lines) > 1:
            header_text = header_match.group(1)
            body = "<br>".join(line for line in lines[1:] if line is not None)
            return (
                "<div style=\"margin-top:18px;background:#ffffff;border:1px solid #d8e2ed;"
                "border-radius:18px;padding:20px 22px;font-size:14px;color:#102a43;line-height:1.6;\">"
                f"<div style=\"font-size:16px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;color:#0f172a;margin-bottom:10px;\">{html.escape(header_text)}</div>"
                f"{body}"
                "</div>"
            )
        body = "<br>".join(lines)
        return (
            "<div style=\"margin-top:18px;background:#ffffff;border:1px solid #d8e2ed;"
            "border-radius:18px;padding:20px 22px;font-size:14px;color:#102a43;line-height:1.6;\">"
            f"{body}"
            "</div>"
        )
