"""Email rendering for institutional-style morning briefs."""

from __future__ import annotations

import html
import os
import re
import statistics
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
_MOVE_TOKEN_RE = re.compile(r"([+\-−]\d[\d,]*(?:\.\d+)?%?)")
_PAREN_MOVE_RE = re.compile(r"(\([+\-−]?\d[\d,]*(?:\.\d+)?%?\))")
_PAIR_MOVE_RE = re.compile(r"([+\-−]\d[\d,]*(?:\.\d+)?\s*\([+\-−]?\d[\d,]*(?:\.\d+)?%\))")
_EMAIL_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"
_CANVAS_BG = "#071629"
_GOOD_SHADE_SCALE = ["#6EE7C8", "#00D4AA", "#00B894", "#0F7A4A"]
_BAD_SHADE_SCALE = ["#FFB4B4", "#FF6B6B", "#E14A4A", "#9B2C2C"]
_SESSION_BUCKETS = [
    (-1.0, -0.6, "SEVERE_STRESS", "#5C1111", "SEVERE STRESS"),
    (-0.6, -0.25, "CAUTIOUS", "#9B2C2C", "CAUTIOUS"),
    (-0.25, 0.25, "MIXED", "#2F3744", "MIXED"),
    (0.25, 0.6, "CONSTRUCTIVE", "#0F7A4A", "CONSTRUCTIVE"),
    (0.6, 1.01, "STRONG_RISK_ON", "#16A34A", "STRONG RISK ON"),
]

# Regime → (background tint hex, accent hex, display label)
_REGIME_STYLES: dict[str, tuple[str, str, str]] = {
    "risk_on":          (_CANVAS_BG, "#00D4AA", "RISK ON"),
    "defensive":        (_CANVAS_BG, "#FF6B6B", "DEFENSIVE"),
    "oil_shock":        (_CANVAS_BG, "#FF6B00", "OIL SHOCK"),
    "rates_led":        (_CANVAS_BG, "#4A90E2", "RATES LED"),
    "regional_split":   (_CANVAS_BG, "#B08EFF", "REGIONAL SPLIT"),
    "breadth_divergence": (_CANVAS_BG, "#4A90E2", "BREADTH DIVERGENCE"),
    "mixed":            (_CANVAS_BG, "#7A8FA0", "MIXED"),
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
        self.enable_move_intensity_shading = os.getenv("EMAIL_ENABLE_MOVE_INTENSITY_SHADING", "true").strip().lower() not in {"0", "false", "off", "no"}
        self.enable_session_quality_accents = os.getenv("EMAIL_ENABLE_SESSION_QUALITY_ACCENTS", "true").strip().lower() not in {"0", "false", "off", "no"}
        self._move_shading_baselines: dict[str, float] = {}

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
        self._move_shading_baselines = self._build_move_shading_baselines(briefing)
        regime_tags = list((briefing.morning_chart_bundle or {}).get("regime_tags") or [])
        bundle_meta = dict((briefing.morning_chart_bundle or {}).get("meta") or {})
        delivery_mode = str(bundle_meta.get("delivery_mode") or "deterministic")
        llm_shadow = bool(bundle_meta.get("llm_shadow_mode", True))
        profile_name = str(bundle_meta.get("profile_name") or "default_user")
        confidence = self._confidence_label(briefing, bundle_meta)
        lead = "Deterministic market stack, portfolio lens, and high-signal narrative."
        generated_local = self._format_local(briefing.generated_at)
        freshness_lines = self._freshness_breakdown_lines(briefing, generated_local)
        title, date_label = self._split_subject(subject)
        desk_read = self._top_desk_read(briefing)

        # Regime banner: deterministic session quality accent takes precedence.
        primary_tag = next((t for t in regime_tags if t in _REGIME_STYLES), "mixed")
        regime_bg, regime_accent, regime_label = _REGIME_STYLES.get(primary_tag, _REGIME_STYLES["mixed"])
        if self.enable_session_quality_accents:
            session = self._compute_session_quality(briefing)
            regime_accent = str(session["color_hex"])
            regime_label = str(session["label"])
            bundle_meta["session_quality_score"] = round(float(session["score"]), 3)
            bundle_meta["session_quality_bucket"] = str(session["bucket"])
            bundle_meta["session_quality_color_hex"] = regime_accent
            bundle_meta["session_quality_label"] = regime_label
        all_tags_text = " · ".join(str(t).replace("_", " ").upper() for t in regime_tags) or "MIXED"

        parts = [
            f"<html><body bgcolor=\"{_CANVAS_BG}\" style=\"margin:0;padding:0;background:{_CANVAS_BG};background-color:{_CANVAS_BG};font-family:{_EMAIL_FONT_STACK};color:#E8ECEF;\">",
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
            f"<tr><td align=\"center\" bgcolor=\"{_CANVAS_BG}\" style=\"padding:8px 4px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
            f"<table role=\"presentation\" width=\"680\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"width:680px;max-width:680px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};border:1px solid #1F3447;\">",
            # Regime colour bar (3px top accent)
            f"<tr><td style=\"height:3px;line-height:3px;font-size:0;background:{regime_accent};background-color:{regime_accent};\">&nbsp;</td></tr>",
            # Header row
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:13px 16px 11px 16px;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
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
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:7px 16px;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">GENERATED</strong> {html.escape(generated_local)}</td>",
            f"<td align=\"center\" style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">PROFILE</strong> {html.escape(profile_name)}</td>",
            f"<td align=\"right\" style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\"><strong style=\"color:#E8ECEF;\">MODE</strong> {html.escape(delivery_mode)} · <strong style=\"color:#E8ECEF;\">CONF</strong> {html.escape(confidence)}</td>",
            "</tr></table>",
            "</td></tr>",
            # Source freshness row
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:7px 16px;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
            "<div style=\"font-size:10.5px;line-height:1.35;color:#7A8FA0;\">"
            "<strong style=\"color:#E8ECEF;\">DATA FRESHNESS · SOURCE</strong><br>"
            + "<br>".join(html.escape(line) for line in freshness_lines)
            + "</div>",
            "</td></tr>",
            # Jump-link nav row
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:6px 16px 6px 16px;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">",
            self._nav_row(),
            "</td></tr>",
        ]

        if desk_read:
            parts.append(
                f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:9px 16px;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};"
                "font-size:12.5px;line-height:1.38;color:#E8ECEF;\">"
                f"{desk_read}</td></tr>"
            )

        if briefing.chart_assets:
            parts.append(f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:10px 16px 0 16px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">")
            parts.append(self._chart_modules(briefing))
            parts.append("</td></tr>")

        commodity_grid = self._commodity_grid_html(briefing)
        if commodity_grid:
            parts.append(commodity_grid)

        breadth_row = self._breadth_row_html(briefing)
        if breadth_row:
            parts.append(breadth_row)

        parts.append(f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:0 16px 16px 16px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">")
        parts.append(self._brief_modules(full_html))
        parts.append("</td></tr>")

        parts.extend(["</table>", "</td></tr></table>", "</body></html>"])
        self._move_shading_baselines = {}
        return "".join(parts)

    @staticmethod
    def _nav_row() -> str:
        links = " &nbsp;|&nbsp; ".join(
            f"<a href=\"#{slug}\" style=\"color:#7A8FA0;font-size:10px;font-weight:700;text-decoration:none;"
            f"letter-spacing:0.04em;\">{html.escape(label).upper()}</a>"
            for slug, label in _NAV_ITEMS
        )
        return f"<div style=\"line-height:1.5;\">{links}</div>"

    def _commodity_grid_html(self, briefing: MorningBriefing) -> str:
        strip = briefing.commodity_strip
        if not strip:
            return ""
        cells = []
        for m in strip:
            chg_pct = m.change_percent
            if chg_pct is not None:
                color = "#00D4AA" if chg_pct >= 0 else "#FF6B6B"
                sign = "+" if chg_pct >= 0 else ""
                chg_html = f"<span style=\"color:{color};\">{sign}{chg_pct:.2f}%</span>"
            else:
                chg_html = ""
            name = (m.name or m.series_id).replace(" (USD/bbl)", "").replace(" (USD/troy oz)", "").replace(" (USD/MMBtu)", "")
            val = f"{m.value:,.2f}"
            cells.append(
                f"<td style=\"padding:3px 10px 3px 0;font-size:11px;color:#E8ECEF;white-space:nowrap;\">"
                f"<span style=\"color:#9BA3AB;\">{html.escape(name)}</span>&nbsp;"
                f"<strong>{html.escape(val)}</strong>&nbsp;{chg_html}</td>"
            )
        # Two columns
        rows_html = ""
        for i in range(0, len(cells), 2):
            pair = cells[i:i+2]
            rows_html += f"<tr>{''.join(pair)}</tr>"
        return (
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:8px 16px;border-bottom:1px solid #1F3447;"
            f"background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
            f"<div style=\"font-size:10px;color:#9BA3AB;letter-spacing:0.05em;font-weight:700;"
            f"margin-bottom:5px;\">COMMODITIES</div>"
            f"<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\">{rows_html}</table>"
            f"</td></tr>"
        )

    def _breadth_row_html(self, briefing: MorningBriefing) -> str:
        rows = briefing.market_setup.market_breadth
        if not rows:
            return ""
        up = sum(1 for b in rows if float(b.change_percent or 0) > 0)
        dn = len(rows) - up
        cells = []
        for b in rows:
            chg = float(b.change_percent or 0)
            color = "#00D4AA" if chg >= 0 else "#FF6B6B"
            sign = "+" if chg >= 0 else ""
            cells.append(
                f"<td style=\"padding:2px 8px 2px 0;font-size:10px;white-space:nowrap;\">"
                f"<span style=\"color:#9BA3AB;\">{html.escape(b.display_name or b.symbol)}</span>&nbsp;"
                f"<span style=\"color:{color};\">{sign}{chg:.1f}%</span></td>"
            )
        rows_html = ""
        for i in range(0, len(cells), 4):
            rows_html += f"<tr>{''.join(cells[i:i+4])}</tr>"
        summary_color = "#00D4AA" if up >= dn else "#FF6B6B"
        return (
            f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:8px 16px;border-bottom:1px solid #1F3447;"
            f"background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
            f"<div style=\"font-size:10px;color:#9BA3AB;letter-spacing:0.05em;font-weight:700;"
            f"margin-bottom:5px;\">SECTOR BREADTH &nbsp;"
            f"<span style=\"color:{summary_color};\">{up}↑ {dn}↓</span></div>"
            f"<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\">{rows_html}</table>"
            f"</td></tr>"
        )

    def _chart_modules(self, briefing: MorningBriefing) -> str:
        roles = {row.get("chart_key"): row.get("role") for row in (briefing.morning_chart_selection or [])}
        modules = [
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"border-collapse:collapse;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
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
                f"<tr><td bgcolor=\"{_CANVAS_BG}\" style=\"padding:{pad_top} 0 {pad_bottom} 0;border-bottom:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
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
        section_title: str | None = None
        if header_match:
            header_text = header_match.group(1)
            section_title = header_text
            slug = next(
                (v for k, v in _SECTION_ANCHORS.items() if k in header_text.lower()),
                None,
            )
            if slug:
                anchor_id = f" id=\"{slug}\""
            if len(lines) > 1:
                body = self._format_section_body(lines[1:], section_title)
                return (
                    f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"margin-top:10px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
                    f"<tr><td{anchor_id} bgcolor=\"{_CANVAS_BG}\" style=\"padding:11px 0 9px 0;font-size:13px;color:#E8ECEF;line-height:1.38;border-top:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
                    f"<div style=\"font-size:15px;line-height:1.18;font-weight:800;letter-spacing:-0.01em;color:#E8ECEF;margin:0 0 6px 0;\">{html.escape(header_text)}</div>"
                    f"{body}"
                    "</td></tr></table>"
                )
        body = self._format_section_body(lines, section_title)
        return (
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"margin-top:10px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
            f"<tr><td{anchor_id} bgcolor=\"{_CANVAS_BG}\" style=\"padding:11px 0 9px 0;font-size:13px;color:#E8ECEF;line-height:1.38;border-top:1px solid #1F3447;background:{_CANVAS_BG};background-color:{_CANVAS_BG};\">"
            f"{body}"
            "</td></tr></table>"
        )

    def _format_section_body(self, lines: list[str], section_title: str | None) -> str:
        colored_lines = [self._colorize_structured_line(line, section_title) for line in lines if line is not None]
        return self._emphasize_market_labels("<br>".join(colored_lines))

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
        numbers = re.findall(r"[+\-−]?\d+(?:\.\d+)?%?", base.replace("−", "-"))
        first_signal = numbers[0] if numbers else None
        bias = "mixed"
        if first_signal:
            try:
                bias_value = float(first_signal.replace("%", ""))
                if bias_value > 0:
                    bias = "constructive"
                elif bias_value < 0:
                    bias = "defensive"
            except ValueError:
                bias = "mixed"
        if key == "cross_asset_impulse_strip":
            return (
                "Cross-asset impulse is centered on zero, so leadership is defined by which sleeve shows the largest absolute displacement. "
                "Today’s read is best treated as a transmission map for where macro pressure is entering the tape first."
            )
        if key == "breadth_leadership_panel":
            return (
                "Breadth is a confirmation test, not a direction forecast: clustered positives support trend durability, while split signals "
                "usually imply rotation-heavy tape and lower conviction on outright index follow-through."
            )
        if key == "pnl_attribution_waterfall":
            return (
                "Attribution reflects weighted contribution rather than simple return, so concentration can dominate the day even when headline "
                "breadth looks benign. Focus first on whether gains are broad-based or reliant on one sleeve."
            )
        if key == "event_linked_annotated_trend":
            return (
                "The event marker separates pre-catalyst drift from post-catalyst repricing; persistence after the marker matters more than the "
                "initial spike. Sustained slope suggests a regime handoff rather than a one-session reaction."
            )
        if key == "portfolio_concentration_risk":
            return (
                "Concentration should be read as a fragility gauge: when top-weight exposure is elevated, idiosyncratic headline risk can override "
                "otherwise constructive macro tape and amplify both upside and drawdown paths."
            )
        if key == "global_relative_performance":
            return (
                "Rebased leadership isolates relative momentum across regions; widening endpoints indicate persistent regional divergence, while "
                "converging endpoints typically signal a risk-beta catch-up phase."
            )
        if base:
            short = " ".join(base.split()[:26]).rstrip(".,;:")
            return f"Signal context is {bias}: {short}."
        return "Signal context is mixed: treat this panel as a directional cue only when confirmed by breadth and cross-asset alignment."

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

    def _colorize_structured_line(self, line: str, section_title: str | None) -> str:
        raw = line or ""
        if "<span style=\"color:#" in raw:
            return raw
        plain = _HTML_TAG_RE.sub("", raw).strip()
        if not plain:
            return raw
        if not self._is_structured_move_line(plain, section_title):
            return raw

        context = self._line_context(plain)
        if " | " in plain and ":" not in plain:
            return self._colorize_watchlist_strip(raw, context)

        # Only colorize data after the first colon to avoid touching instrument labels (e.g., "10Y-2Y").
        head, sep, tail = raw.partition(":")
        if not sep:
            return raw
        if "<span style=\"color:#" in tail:
            return raw

        # Colorize paired move segments: +1.23 (+0.45%) and stop to avoid nested span wrapping.
        if _PAIR_MOVE_RE.search(tail):
            colored_tail = _PAIR_MOVE_RE.sub(
                lambda m: self._wrap_color(m.group(1), self._color_for_change(m.group(1), context)),
                tail,
            )
            return f"{head}{sep}{colored_tail}"

        # Then colorize remaining parenthetical changes: (-0.0200)
        out = _PAREN_MOVE_RE.sub(lambda m: self._wrap_color(m.group(1), self._color_for_change(m.group(1), context)), tail)
        # Finally colorize standalone % tokens in compact strips.
        out = _MOVE_TOKEN_RE.sub(lambda m: self._wrap_color(m.group(1), self._color_for_change(m.group(1), context)), out)
        return f"{head}{sep}{out}"

    @staticmethod
    def _wrap_color(token: str, color: str) -> str:
        return f"<span style=\"color:{color};\">{token}</span>"

    def _colorize_watchlist_strip(self, line: str, context: str) -> str:
        return re.sub(
            r"([A-Z0-9\.\-]+)\s+([+\-−]\d[\d,]*(?:\.\d+)?%)",
            lambda m: f"{m.group(1)} {self._wrap_color(m.group(2), self._color_for_change(m.group(2), context))}",
            line,
        )

    @staticmethod
    def _is_structured_move_line(plain: str, section_title: str | None) -> bool:
        lower = plain.lower()
        if lower.startswith(
            (
                "dominant driver:",
                "setup read:",
                "regional skew:",
                "action posture:",
                "net takeaway:",
                "why market-relevant:",
                "quotes as of",
                "watchlist is ",
            )
        ):
            return False

        section = (section_title or "").strip().lower()
        in_target_section = section in {"market setup", "macro context", "watchlist", "sector scan", "portfolio impact today"}
        has_move = bool(_PAIR_MOVE_RE.search(plain) or _PAREN_MOVE_RE.search(plain) or re.search(r"[+\-−]\d[\d,]*(?:\.\d+)?%", plain))
        if not has_move:
            return False
        if " | " in plain:
            return True
        if ":" in plain and in_target_section:
            return True
        # compact mover summaries in other sections
        if ":" in plain and re.search(r"\([A-Z]{1,5}\s+[+\-−]\d", plain):
            return True
        return False

    @staticmethod
    def _line_context(plain: str) -> str:
        lower = plain.lower()
        name = lower.split(":", 1)[0] if ":" in lower else lower
        if any(tok in name for tok in ("wti", "crude", "brent", "copper", "gold", "commodity")):
            return "commodity"
        if any(tok in name for tok in ("vix", "move index", "volatility", "fear", "stress")):
            return "stress"
        if any(tok in name for tok in ("yield", "treasury", "curve", "10y", "2y", "30y")):
            return "rates"
        return "risk"

    def _color_for_change(self, token: str, context: str) -> str:
        value = EmailFormatter._extract_signed_value(token)
        if value is None:
            return "#E8ECEF"
        if abs(value) < 1e-12:
            return "#9BA3AB"
        positive_is_good = context not in {"stress", "rates"}
        good_move = (value > 0 and positive_is_good) or (value < 0 and not positive_is_good)
        if not self.enable_move_intensity_shading:
            return "#00D4AA" if good_move else "#FF6B6B"
        return self._shade_for_intensity(
            abs(value),
            context=context,
            good_move=good_move,
        )

    @staticmethod
    def _extract_signed_value(token: str) -> float | None:
        text = (token or "").replace(",", "").replace("−", "-")
        percent_match = re.search(r"([+\-]?\d+(?:\.\d+)?)\s*%", text)
        if percent_match:
            return float(percent_match.group(1))
        match = re.search(r"[+\-]?\d+(?:\.\d+)?", text)
        if not match:
            return None
        return float(match.group(0))

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _build_move_shading_baselines(self, briefing: MorningBriefing) -> dict[str, float]:
        risk_moves = [
            abs(float(q.change_percent))
            for q in (briefing.market_setup.index_quotes or [])
            if self._line_context(q.display_name or q.symbol) == "risk"
        ]
        stress_moves = [
            abs(float(q.change_percent))
            for q in (briefing.market_setup.index_quotes or [])
            if self._line_context(q.display_name or q.symbol) == "stress"
        ]
        commodity_moves = [
            abs(float(q.change_percent))
            for q in (briefing.market_setup.index_quotes or [])
            if any(k in (q.display_name or "").lower() for k in ("wti", "crude", "gold", "copper", "brent"))
        ]
        rates_moves = [
            abs(float(point.change or 0.0))
            for point in (briefing.macro_context or [])
            if any(k in (point.name or "").lower() for k in ("yield", "treasury", "10y-2y", "curve"))
        ]
        baselines = {
            "risk": self._median_or_default(risk_moves, 0.55),
            "stress": self._median_or_default(stress_moves, 1.8),
            "commodity": self._median_or_default(commodity_moves, 1.0),
            "rates": self._median_or_default(rates_moves, 0.02),
        }
        baselines["default"] = baselines["risk"]
        return baselines

    @staticmethod
    def _median_or_default(values: list[float], default: float) -> float:
        usable = [v for v in values if v > 0]
        if not usable:
            return default
        return max(default * 0.4, float(statistics.median(usable)))

    @staticmethod
    def _intensity_index(ratio: float) -> int:
        if ratio <= 0.75:
            return 0
        if ratio <= 1.25:
            return 1
        if ratio <= 2.0:
            return 2
        return 3

    def _shade_for_intensity(self, magnitude: float, *, context: str, good_move: bool) -> str:
        baseline = float(self._move_shading_baselines.get(context, self._move_shading_baselines.get("default", 0.55)))
        ratio = magnitude / baseline if baseline > 0 else 1.0
        idx = self._intensity_index(ratio)
        return _GOOD_SHADE_SCALE[idx] if good_move else _BAD_SHADE_SCALE[idx]

    def _compute_session_quality(self, briefing: MorningBriefing) -> dict[str, float | str]:
        from app.briefing.session_quality import compute_session_quality as _sq_compute

        # Use pre-computed fields if morning_generator already ran the score.
        if briefing.session_quality_bucket:
            return {
                "score":      briefing.session_quality_score,
                "bucket":     briefing.session_quality_bucket,
                "color_hex":  briefing.session_quality_color_hex,
                "label":      briefing.session_quality_label,
            }

        # Fallback: compute inline (e.g. in tests or when generator skipped the step).
        sq = _sq_compute(
            market_breadth=briefing.market_setup.market_breadth,
            index_quotes=briefing.market_setup.index_quotes,
            macro_context=briefing.macro_context,
            commodity_strip=briefing.commodity_strip,
            geo_risk_level=briefing.geo_risk_level,
        )
        return {"score": sq.score, "bucket": sq.bucket, "color_hex": sq.color_hex, "label": sq.label}

    @staticmethod
    def _extract_regional_moves(text: str | None) -> dict[str, float]:
        if not text:
            return {}
        lower = text.lower().replace("−", "-")
        out: dict[str, float] = {}
        for region in ("us", "europe", "asia"):
            match = re.search(rf"{region}\s*([+\-]\d+(?:\.\d+)?)%", lower)
            if match:
                out[region] = float(match.group(1))
        return out

    def _freshness_summary(self, briefing: MorningBriefing) -> str:
        quotes = self._freshness_quotes(briefing)
        if not quotes:
            return "Quotes as of unavailable; sources: none"
        latest_ts = max((quote.timestamp for quote in quotes if quote.timestamp), default=None)
        timestamp = self._format_local(latest_ts) if latest_ts else "unavailable"
        counter = Counter((quote.source or "unknown").strip().lower() or "unknown" for quote in quotes)
        src = ", ".join(f"{name}({count})" for name, count in sorted(counter.items()))
        return f"Quotes as of {timestamp} · sources: {src}"

    def _freshness_breakdown_lines(self, briefing: MorningBriefing, generated_local: str) -> list[str]:
        quotes = self._freshness_quotes(briefing)
        latest_ts = max((quote.timestamp for quote in quotes if quote.timestamp), default=None)
        if latest_ts is None:
            market_line = "Market prices: unavailable"
            src_line = "Sources: none"
        else:
            ts_text = self._format_local(latest_ts)
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=ZoneInfo(self.timezone_name))
            age_hours = max(
                0.0,
                (briefing.generated_at.astimezone(ZoneInfo(self.timezone_name)) - latest_ts.astimezone(ZoneInfo(self.timezone_name))).total_seconds() / 3600.0,
            )
            freshness_tag = "prior close" if age_hours >= 8.0 else "near-real-time"
            market_line = f"Market prices: {freshness_tag}, {ts_text}"
            counter = Counter((quote.source or "unknown").strip().lower() or "unknown" for quote in quotes)
            src_line = "Sources: " + ", ".join(f"{name}({count})" for name, count in sorted(counter.items()))

        return [
            market_line,
            f"News: live, generated {generated_local}",
            "Macro/FRED: latest available official release",
            "Portfolio P&L: based on prior-close prices",
            src_line,
        ]

    def _confidence_label(self, briefing: MorningBriefing, bundle_meta: dict) -> str:
        score = 0.0
        base_conf = str(bundle_meta.get("data_confidence") or briefing.market_setup_analysis_confidence or "medium").strip().lower()
        if base_conf in {"high", "med-high", "medium-high"}:
            score += 1.0
        elif base_conf in {"medium", "med"}:
            score += 0.7
        else:
            score += 0.4

        if briefing.events_fetched >= 300:
            score += 1.0
        elif briefing.events_fetched >= 120:
            score += 0.8
        elif briefing.events_fetched > 0:
            score += 0.5

        quotes = self._freshness_quotes(briefing)
        latest_ts = max((quote.timestamp for quote in quotes if quote.timestamp), default=None)
        if latest_ts is not None:
            if latest_ts.tzinfo is None:
                latest_ts = latest_ts.replace(tzinfo=ZoneInfo(self.timezone_name))
            age_hours = max(
                0.0,
                (briefing.generated_at.astimezone(ZoneInfo(self.timezone_name)) - latest_ts.astimezone(ZoneInfo(self.timezone_name))).total_seconds() / 3600.0,
            )
            if age_hours <= 12:
                score += 1.0
            elif age_hours <= 36:
                score += 0.7
            else:
                score += 0.4

        if briefing.geo_risk_level:
            score += 0.5
        if briefing.session_quality_bucket:
            score += 0.5

        if score >= 3.6:
            return "HIGH"
        if score >= 2.8:
            return "MED-HIGH"
        if score >= 1.9:
            return "MEDIUM"
        return "LOW"

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
