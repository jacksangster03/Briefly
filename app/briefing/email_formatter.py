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
_LABEL_RE = re.compile(
    r"(?P<label>Dominant driver|Setup read|Portfolio impact|Action posture|Regional skew|Watchlist|Earnings Calendar):"
)
_EMAIL_FONT_STACK = "Aptos,'Segoe UI',Arial,sans-serif"


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
        lead = "Deterministic market stack, portfolio lens, and high-signal narrative."
        freshness = self._freshness_summary(briefing)
        generated_local = self._format_local(briefing.generated_at)
        title, date_label = self._split_subject(subject)
        regime_text = " / ".join(str(tag).replace("_", " ").upper() for tag in regime_tags) or "MIXED"

        parts = [
            f"<html><body style=\"margin:0;padding:0;background:#071421;font-family:{_EMAIL_FONT_STACK};color:#F3F7FB;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#071629;\">",
            "<tr><td align=\"center\" style=\"padding:10px 6px;\">",
            "<table role=\"presentation\" width=\"780\" cellspacing=\"0\" cellpadding=\"0\" style=\"width:780px;max-width:780px;background:#091B2B;border:1px solid #23384D;\">",
            "<tr><td style=\"height:4px;line-height:4px;font-size:0;background:#FF7A00;\">&nbsp;</td></tr>",
            "<tr><td style=\"padding:18px 20px 14px 20px;border-bottom:1px solid #23384D;background:#0B1D30;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            "<td valign=\"bottom\" style=\"width:62%;\">",
            f"<div style=\"font-size:30px;line-height:1.02;font-weight:800;color:#F3F7FB;letter-spacing:-0.035em;\">{html.escape(title)}</div>",
            f"<div style=\"margin-top:7px;font-size:13px;line-height:1.45;color:#9FB3C8;\">{html.escape(lead)}</div>",
            "</td>",
            "<td valign=\"bottom\" align=\"right\" style=\"width:38%;\">",
            f"<div style=\"font-size:16px;line-height:1.2;font-weight:700;color:#F3F7FB;letter-spacing:0.01em;\">{html.escape(date_label)}</div>",
            f"<div style=\"margin-top:8px;font-size:11px;line-height:1.35;color:#9FB3C8;\">REGIME <span style=\"color:#FF7A00;font-weight:800;\">{html.escape(regime_text)}</span></div>",
            "</td></tr></table>",
            "</td></tr>",
            "<tr><td style=\"padding:9px 20px;border-bottom:1px solid #23384D;background:#091B2B;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:12px;line-height:1.45;color:#9FB3C8;\"><strong style=\"color:#F3F7FB;\">Generated</strong> {html.escape(generated_local)}</td>",
            f"<td align=\"center\" style=\"font-size:12px;line-height:1.45;color:#9FB3C8;\"><strong style=\"color:#F3F7FB;\">Profile</strong> {html.escape(profile_name)}</td>",
            f"<td align=\"right\" style=\"font-size:12px;line-height:1.45;color:#9FB3C8;\"><strong style=\"color:#F3F7FB;\">Mode</strong> {html.escape(delivery_mode)} · LLM shadow {'on' if llm_shadow else 'off'} · <strong style=\"color:#F3F7FB;\">Confidence</strong> {html.escape(confidence)}</td>",
            "</tr></table>",
            "</td></tr>",
            "<tr><td style=\"padding:8px 20px;border-bottom:1px solid #23384D;background:#091B2B;\">",
            f"<div style=\"font-size:12px;line-height:1.45;color:#9FB3C8;\"><strong style=\"color:#F3F7FB;\">Source freshness</strong> {html.escape(freshness)}</div>",
            "</td></tr>",
        ]

        if briefing.chart_assets:
            parts.append("<tr><td style=\"padding:13px 20px 0 20px;background:#091B2B;\">")
            parts.append(self._chart_modules(briefing))
            parts.append("</td></tr>")

        parts.append("<tr><td style=\"padding:2px 20px 20px 20px;background:#091B2B;\">")
        parts.append(self._brief_modules(full_html))
        parts.append("</td></tr>")

        parts.extend(["</table>", "</td></tr></table>", "</body></html>"])
        return "".join(parts)

    def _chart_modules(self, briefing: MorningBriefing) -> str:
        roles = {row.get("chart_key"): row.get("role") for row in (briefing.morning_chart_selection or [])}
        modules = [
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">"
        ]
        for idx, asset in enumerate(briefing.chart_assets):
            role = str(roles.get(asset.key) or ("hero" if idx == 0 else "support"))
            is_hero = role == "hero" or idx == 0
            is_micro = role.startswith("micro")
            title_size = "20px" if is_hero else ("14px" if is_micro else "16px")
            pad_top = "0" if idx == 0 else ("11px" if is_micro else "14px")
            pad_bottom = "16px" if is_hero else ("10px" if is_micro else "13px")
            image_border = "1px solid #23384D" if is_hero else "1px solid #1D3145"
            modules.append(
                f"<tr><td style=\"padding:{pad_top} 0 {pad_bottom} 0;border-bottom:1px solid #23384D;\">"
            )
            modules.append(
                f"<div style=\"font-size:{title_size};line-height:1.18;font-weight:800;color:#F3F7FB;letter-spacing:-0.015em;padding:0 0 7px 0;\">{html.escape(asset.title)}</div>"
            )
            modules.append(
                f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                f"style=\"display:block;width:100%;max-width:738px;margin-top:0;border:{image_border};\">"
            )
            if asset.caption:
                modules.append(
                    f"<div style=\"margin-top:7px;font-size:12.5px;line-height:1.45;color:#9FB3C8;\"><span style=\"color:#FF7A00;font-weight:800;\">READ</span> {html.escape(asset.caption)}</div>"
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
            body = self._emphasize_market_labels("<br>".join(line for line in lines[1:] if line is not None))
            return (
                "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
                "<tr><td style=\"padding:14px 0 11px 0;font-size:15px;color:#DCE7F3;line-height:1.62;border-top:1px solid #23384D;\">"
                f"<div style=\"font-size:18px;line-height:1.2;font-weight:800;letter-spacing:-0.015em;color:#F3F7FB;margin:0 0 8px 0;\">{html.escape(header_text)}</div>"
                f"{body}"
                "</td></tr></table>"
            )
        body = self._emphasize_market_labels("<br>".join(lines))
        return (
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
            "<tr><td style=\"padding:14px 0 11px 0;font-size:15px;color:#DCE7F3;line-height:1.62;border-top:1px solid #23384D;\">"
            f"{body}"
            "</td></tr></table>"
        )

    @staticmethod
    def _split_subject(subject: str) -> tuple[str, str]:
        if "|" not in subject:
            return subject, ""
        title, date_label = subject.split("|", 1)
        return title.strip(), date_label.strip()

    @staticmethod
    def _emphasize_market_labels(body: str) -> str:
        def repl(match: re.Match[str]) -> str:
            label = html.escape(match.group("label"))
            return f'<strong style="color:#FF7A00;font-weight:800;">{label}:</strong>'

        return _LABEL_RE.sub(repl, body)

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
