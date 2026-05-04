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
    r"(?P<label>Dominant driver|Setup read|Portfolio impact|Action posture|Regional skew|Watchlist|Earnings Calendar|Geo risk meter|Regime shift):"
)
_EMAIL_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"

# Regime → (background tint hex, accent hex, display label)
_REGIME_STYLES: dict[str, tuple[str, str, str]] = {
    "risk_on":          ("#071629", "#00D4AA", "RISK ON"),
    "defensive":        ("#071629", "#FF6B6B", "DEFENSIVE"),
    "oil_shock":        ("#071629", "#FF6B00", "OIL SHOCK"),
    "rates_led":        ("#071629", "#4A90E2", "RATES LED"),
    "regional_split":   ("#071629", "#B08EFF", "REGIONAL SPLIT"),
    "breadth_divergence": ("#071629", "#4A90E2", "BREADTH DIVERGENCE"),
    "mixed":            ("#071629", "#7A8FA0", "MIXED"),
}

# Section header → anchor slug mapping
_SECTION_ANCHORS: dict[str, str] = {
    "market setup": "market",
    "macro": "macro",
    "portfolio": "portfolio",
    "global news": "news",
    "top themes": "themes",
    "earnings": "earnings",
    "watchlist": "watchlist",
    "sector scan": "sectors",
}

# Jump-link nav labels (displayed at top of email)
_NAV_ITEMS = [
    ("market", "Market"),
    ("portfolio", "Portfolio"),
    ("news", "News"),
    ("themes", "Themes"),
    ("earnings", "Earnings"),
]


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
            plain_text=self._email_plain_text(briefing, full_html),
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
        desk_read = self._top_desk_read(briefing)

        # Regime banner: pick first matching tag for styling
        primary_tag = next((t for t in regime_tags if t in _REGIME_STYLES), "mixed")
        regime_bg, regime_accent, regime_label = _REGIME_STYLES.get(primary_tag, _REGIME_STYLES["mixed"])
        all_tags_text = " · ".join(str(t).replace("_", " ").upper() for t in regime_tags) or "MIXED"

        parts = [
            f"<html><body style=\"margin:0;padding:0;background:#071629;font-family:{_EMAIL_FONT_STACK};color:#E8ECEF;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#071629;\">",
            "<tr><td align=\"center\" style=\"padding:8px 4px;\">",
            "<table role=\"presentation\" width=\"680\" cellspacing=\"0\" cellpadding=\"0\" style=\"width:680px;max-width:680px;background:#071629;border:1px solid #1F3447;\">",
            # Regime colour bar (3px top accent)
            f"<tr><td style=\"height:3px;line-height:3px;font-size:0;background:{regime_accent};\">&nbsp;</td></tr>",
            # Header row
            "<tr><td style=\"padding:13px 16px 11px 16px;border-bottom:1px solid #1F3447;background:#071629;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            "<td valign=\"bottom\" style=\"width:62%;\">",
            f"<div style=\"font-size:20px;line-height:1.08;font-weight:800;color:#E8ECEF;letter-spacing:-0.01em;\">{html.escape(title)}</div>",
            f"<div style=\"margin-top:5px;font-size:12px;line-height:1.35;color:#7A8FA0;\">{html.escape(lead)}</div>",
            "</td>",
            "<td valign=\"bottom\" align=\"right\" style=\"width:38%;\">",
            f"<div style=\"font-size:13px;line-height:1.2;font-weight:700;color:#E8ECEF;letter-spacing:-0.01em;\">{html.escape(date_label)}</div>",
            "</td></tr></table>",
            "</td></tr>",
            # Full-width regime banner row
            f"<tr><td style=\"padding:6px 16px;border-bottom:1px solid #1F3447;background:{regime_bg};\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:11px;line-height:1.3;font-weight:800;color:{regime_accent};letter-spacing:0.06em;\">",
            f"&#9646; REGIME: {html.escape(regime_label)}",
            "</td>",
            f"<td align=\"right\" style=\"font-size:9.5px;color:#7A8FA0;\">{html.escape(all_tags_text)}</td>",
            "</tr></table>",
            "</td></tr>",
            # Meta row
            "<tr><td style=\"padding:7px 16px;border-bottom:1px solid #1F3447;background:#071629;\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">GENERATED</strong> {html.escape(generated_local)}</td>",
            f"<td align=\"center\" style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">PROFILE</strong> {html.escape(profile_name)}</td>",
            f"<td align=\"right\" style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">MODE</strong> {html.escape(delivery_mode)} · <strong style=\"color:#E8ECEF;\">CONF</strong> {html.escape(confidence)}</td>",
            "</tr></table>",
            "</td></tr>",
            # Source freshness row
            "<tr><td style=\"padding:7px 16px;border-bottom:1px solid #1F3447;background:#071629;\">",
            f"<div style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">SOURCE</strong> {html.escape(freshness)}</div>",
            "</td></tr>",
            # Jump-link nav row
            "<tr><td style=\"padding:6px 16px 6px 16px;border-bottom:1px solid #1F3447;background:#071629;\">",
            self._nav_row(),
            "</td></tr>",
        ]

        if desk_read:
            parts.append(
                "<tr><td style=\"padding:9px 16px;border-bottom:1px solid #1F3447;background:#071629;"
                "font-size:12.5px;line-height:1.38;color:#E8ECEF;\">"
                f"{desk_read}</td></tr>"
            )

        if briefing.chart_assets:
            parts.append("<tr><td style=\"padding:10px 16px 0 16px;background:#071629;\">")
            parts.append(self._chart_modules(briefing))
            parts.append("</td></tr>")

        parts.append("<tr><td style=\"padding:0 16px 16px 16px;background:#071629;\">")
        parts.append(self._brief_modules(full_html))
        parts.append("</td></tr>")

        parts.extend(["</table>", "</td></tr></table>", "</body></html>"])
        return "".join(parts)

    @staticmethod
    def _nav_row() -> str:
        links = " &nbsp;|&nbsp; ".join(
            f"<a href=\"#{slug}\" style=\"color:#7A8FA0;font-size:10px;font-weight:700;text-decoration:none;"
            f"letter-spacing:0.04em;\">{html.escape(label).upper()}</a>"
            for slug, label in _NAV_ITEMS
        )
        return f"<div style=\"line-height:1.5;\">{links}</div>"

    def _chart_modules(self, briefing: MorningBriefing) -> str:
        roles = {row.get("chart_key"): row.get("role") for row in (briefing.morning_chart_selection or [])}
        modules = [
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">"
        ]
        for idx, asset in enumerate(briefing.chart_assets):
            role = str(roles.get(asset.key) or ("hero" if idx == 0 else "support"))
            is_hero = role == "hero" or idx == 0
            is_micro = role.startswith("micro")
            title_size = "15px" if is_hero else ("13px" if is_micro else "14px")
            pad_top = "0" if idx == 0 else ("8px" if is_micro else "11px")
            pad_bottom = "13px" if is_hero else ("9px" if is_micro else "11px")
            read_line = self._chart_read_line(asset.caption)
            takeaway_line = self._chart_takeaway_line(asset.caption)
            explain_line = self._chart_explainer_paragraph(asset.key, asset.caption)
            modules.append(
                f"<tr><td style=\"padding:{pad_top} 0 {pad_bottom} 0;border-bottom:1px solid #1F3447;\">"
            )
            modules.append(
                f"<div style=\"font-size:{title_size};line-height:1.15;font-weight:800;color:#E8ECEF;letter-spacing:-0.01em;padding:0 0 4px 0;\">{html.escape(asset.title)}</div>"
            )
            modules.append(
                f"<div style=\"font-size:12px;line-height:1.35;color:#7A8FA0;padding:0 0 11px 0;\"><span style=\"color:#FF6B00;font-size:10px;letter-spacing:0.05em;font-weight:800;\">READ</span> {html.escape(read_line)}</div>"
            )
            modules.append(
                f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                "width=\"640\" style=\"display:block;width:100%;max-width:640px;height:auto;margin-top:0;border:0;\">"
            )
            modules.append(
                f"<div style=\"font-size:11px;line-height:1.35;color:#9BA3AB;padding:7px 0 0 0;\">{html.escape(takeaway_line)}</div>"
            )
            modules.append(
                f"<div style=\"font-size:12px;line-height:1.45;color:#B6C4D3;padding:6px 0 0 0;\">{html.escape(explain_line)}</div>"
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
        anchor_id = ""
        if header_match:
            header_text = header_match.group(1)
            slug = next(
                (v for k, v in _SECTION_ANCHORS.items() if k in header_text.lower()),
                None,
            )
            if slug:
                anchor_id = f" id=\"{slug}\""
            if len(lines) > 1:
                body = self._emphasize_market_labels("<br>".join(line for line in lines[1:] if line is not None))
                return (
                    f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
                    f"<tr><td{anchor_id} style=\"padding:11px 0 9px 0;font-size:13px;color:#E8ECEF;line-height:1.38;border-top:1px solid #1F3447;\">"
                    f"<div style=\"font-size:15px;line-height:1.18;font-weight:800;letter-spacing:-0.01em;color:#E8ECEF;margin:0 0 6px 0;\">{html.escape(header_text)}</div>"
                    f"{body}"
                    "</td></tr></table>"
                )
        body = self._emphasize_market_labels("<br>".join(lines))
        return (
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"margin-top:10px;\">"
            f"<tr><td{anchor_id} style=\"padding:11px 0 9px 0;font-size:13px;color:#E8ECEF;line-height:1.38;border-top:1px solid #1F3447;\">"
            f"{body}"
            "</td></tr></table>"
        )

    @staticmethod
    def _chart_read_line(caption: str | None) -> str:
        text = (caption or "Deterministic market read from current briefing inputs.").strip()
        words = text.split()
        if len(words) > 20:
            text = " ".join(words[:20]).rstrip(".,;:") + "."
        return text

    @staticmethod
    def _chart_takeaway_line(caption: str | None) -> str:
        text = (caption or "Interpretation uses deterministic chart values and fixed market rules.").strip()
        words = text.split()
        if len(words) > 30:
            text = " ".join(words[:30]).rstrip(".,;:") + "."
        return f"Takeaway: {text}"

    @staticmethod
    def _chart_explainer_paragraph(chart_key: str | None, caption: str | None) -> str:
        key = str(chart_key or "").strip().lower()
        base = (caption or "").strip()
        if len(base.split()) > 34:
            base = " ".join(base.split()[:34]).rstrip(".,;:") + "."
        if key == "cross_asset_impulse_strip":
            return (
                "This strip ranks cross-asset shocks around a neutral zero line: right-side values signal positive impulse, "
                "left-side values signal drag. Use it to identify whether rates, commodities, or risk gauges are driving the tape."
            )
        if key == "breadth_leadership_panel":
            return (
                "Breadth and leadership factors are shown on a bullish/bearish scale, so bar direction and magnitude both matter. "
                "A positive cluster confirms participation, while mixed signs usually indicate fragile trend quality."
            )
        if key == "pnl_attribution_waterfall":
            return (
                "Each bar is a weighted contribution to daily portfolio return, not just raw move, so larger positions carry more influence. "
                "Positive bars add to P&L and negative bars subtract, with the TOTAL line summarizing net impact."
            )
        if key == "event_linked_annotated_trend":
            return (
                "The dashed marker shows where the current catalyst window starts, and the shaded region tracks price response since that trigger. "
                "This helps separate pre-event drift from event-driven follow-through."
            )
        if base:
            return f"Interpretation: {base}"
        return "Interpretation: Deterministic chart values are summarized here to explain direction, magnitude, and the current risk signal."

    def _top_desk_read(self, briefing: MorningBriefing) -> str:
        lines = [html.escape(line) for line in self._top_desk_read_lines(briefing)]
        return self._emphasize_market_labels("<br>".join(lines))

    def _email_plain_text(self, briefing: MorningBriefing, full_html: str) -> str:
        plain = _HTML_TAG_RE.sub("", full_html)
        desk_lines = self._top_desk_read_lines(briefing)
        if not desk_lines:
            return plain
        return "\n".join(desk_lines) + "\n\n" + plain

    @staticmethod
    def _top_desk_read_lines(briefing: MorningBriefing) -> list[str]:
        driver = (
            briefing.dominant_tape_driver.strip()
            if briefing.dominant_tape_driver
            else "No single dominant driver; monitor setup read for mixed market impulses."
        )
        lines = [f"Dominant driver: {driver}"]
        if briefing.market_setup_analysis:
            lines.append(f"Setup read: {briefing.market_setup_analysis}")
        if briefing.geo_risk_summary:
            lines.append(f"Geo risk meter: {briefing.geo_risk_summary}")
        if briefing.regime_shift:
            shift_text = ", ".join(
                f"{k.replace('_', ' ')} {v}" for k, v in sorted((briefing.regime_shift or {}).items())
            )
            if shift_text:
                lines.append(f"Regime shift: {shift_text}")
        return lines

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
            return f'<strong style="color:#FF6B00;font-weight:800;">{label}:</strong>'

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
