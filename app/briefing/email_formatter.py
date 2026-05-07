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
from app.briefing.move_colors import move_color_hex
from app.briefing.trust_contract import chart_copy_is_distinct, contract_warning_summary, distinct_lines
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
_OUTER_BG = "#030A12"
_CANVAS_BG = "#06111F"
_SECTION_BG = "#06111F"
_CHART_PANEL_BG = "#03101D"
_DIVIDER = "#153047"
_TEXT_PRIMARY = "#EAF2FF"
_TEXT_SECONDARY = "#A9B8C8"
_TEXT_MUTED = "#7F93A8"
_FOCUS_ORANGE = "#FF7A00"
_POSITIVE = "#00C2A8"
_NEGATIVE = "#FF5C64"
_NEUTRAL_MOVE = "#8FA4BA"
_GOOD_SHADE_SCALE = ["#7BE0CF", _POSITIVE, "#0FA98F", "#0B7A66"]
_BAD_SHADE_SCALE = ["#FFB3B3", _NEGATIVE, "#E14A4A", "#A3373F"]
_SESSION_BUCKETS = [
    (-1.0, -0.6, "SEVERE_STRESS", "#5C1111", "SEVERE STRESS"),
    (-0.6, -0.25, "CAUTIOUS", "#9B2C2C", "CAUTIOUS"),
    (-0.25, 0.25, "MIXED", "#2F3744", "MIXED"),
    (0.25, 0.6, "CONSTRUCTIVE", "#0F7A4A", "CONSTRUCTIVE"),
    (0.6, 1.01, "STRONG_RISK_ON", "#16A34A", "STRONG RISK ON"),
]

# Regime → (background tint hex, accent hex, display label)
_REGIME_STYLES: dict[str, tuple[str, str, str]] = {
    "risk_on":          (_SECTION_BG, _POSITIVE, "RISK ON"),
    "defensive":        (_SECTION_BG, _NEGATIVE, "DEFENSIVE"),
    "oil_shock":        (_SECTION_BG, _FOCUS_ORANGE, "OIL SHOCK"),
    "rates_led":        (_SECTION_BG, "#6FA8E8", "RATES LED"),
    "regional_split":   (_SECTION_BG, "#92A5BD", "REGIONAL SPLIT"),
    "breadth_divergence": (_SECTION_BG, "#6FA8E8", "BREADTH DIVERGENCE"),
    "mixed":            (_SECTION_BG, _TEXT_MUTED, "MIXED"),
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


def _avg_region_move(briefing: MorningBriefing, tokens: tuple[str, ...]) -> float:
    moves = [
        float(q.change_percent or 0.0)
        for q in briefing.market_setup.index_quotes
        if any(tok in (q.display_name or q.symbol or "").upper() for tok in tokens)
    ]
    if not moves:
        return 0.0
    return sum(moves) / len(moves)


class EmailFormatter:
    """Render Outlook-safe HTML email while preserving Telegram narrative."""

    def __init__(self, timezone_name: str = "UTC", content_width: int = 680):
        self.telegram_formatter = TelegramFormatter(timezone_name)
        self.timezone_name = timezone_name
        self.content_width = max(560, min(760, int(content_width)))
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
            session_label = "Weekend Briefing"
        elif briefing.session_title and briefing.session_key not in {"", "morning"}:
            session_label = briefing.session_title
        else:
            session_label = "Morning Briefing"
        return f"Briefly | {session_label} | {date_str}"

    def _html_body(self, briefing: MorningBriefing, full_html: str, subject: str) -> str:
        self._move_shading_baselines = self._build_move_shading_baselines(briefing)
        regime_tags = list((briefing.morning_chart_bundle or {}).get("regime_tags") or [])
        bundle_meta = dict((briefing.morning_chart_bundle or {}).get("meta") or {})
        delivery_mode = str(bundle_meta.get("delivery_mode") or "deterministic")
        llm_shadow = bool(bundle_meta.get("llm_shadow_mode", True))
        profile_name = str(bundle_meta.get("profile_name") or "default_user")
        confidence = self._confidence_summary(briefing, bundle_meta)
        density_mode = str(bundle_meta.get("email_density_mode") or "desk")
        chart_count = len(briefing.chart_assets or [])
        lead = "Deterministic market stack, portfolio lens, and high-signal narrative."
        generated_local = self._format_local(briefing.generated_at)
        section_conf_lines = self._section_confidence_lines(briefing)
        freshness_lines = self._freshness_breakdown_lines(briefing, generated_local)
        title, date_label = self._split_subject(subject)
        desk_read = self._top_desk_read(briefing)
        trigger_lines = self._trigger_lines(briefing)
        change_lines = self._what_changed_lines(briefing)
        market_clock_lines = self._market_clock_lines(briefing)

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
            f"<html><body bgcolor=\"{_OUTER_BG}\" style=\"margin:0;padding:0;background:{_OUTER_BG};background-color:{_OUTER_BG};font-family:{_EMAIL_FONT_STACK};color:{_TEXT_PRIMARY};\">",
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_OUTER_BG}\" style=\"background:{_OUTER_BG};background-color:{_OUTER_BG};\">",
            f"<tr><td align=\"center\" bgcolor=\"{_OUTER_BG}\" style=\"padding:8px 4px;background:{_OUTER_BG};background-color:{_OUTER_BG};\">",
            f"<table role=\"presentation\" width=\"{self.content_width}\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CANVAS_BG}\" style=\"width:{self.content_width}px;max-width:{self.content_width}px;background:{_CANVAS_BG};background-color:{_CANVAS_BG};border:1px solid {_DIVIDER};\">",
            # Regime colour bar (3px top accent)
            f"<tr><td style=\"height:3px;line-height:3px;font-size:0;background:{regime_accent};background-color:{regime_accent};\">&nbsp;</td></tr>",
            # Header row
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:13px 16px 11px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            "<td valign=\"bottom\" style=\"width:62%;\">",
            f"<div style=\"font-size:20px;line-height:1.08;font-weight:800;color:{_TEXT_PRIMARY};letter-spacing:-0.01em;\">{html.escape(title)}</div>",
            f"<div style=\"margin-top:5px;font-size:12px;line-height:1.35;color:{_TEXT_SECONDARY};\">{html.escape(lead)}</div>",
            "</td>",
            "<td valign=\"bottom\" align=\"right\" style=\"width:38%;\">",
            f"<div style=\"font-size:13px;line-height:1.2;font-weight:700;color:{_TEXT_PRIMARY};letter-spacing:-0.01em;\">{html.escape(date_label)}</div>",
            "</td></tr></table>",
            "</td></tr>",
            # Full-width regime banner row
            f"<tr><td style=\"padding:6px 16px;border-bottom:1px solid {_DIVIDER};background:{regime_bg};\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:11px;line-height:1.3;font-weight:800;color:{regime_accent};letter-spacing:0.06em;\">",
            f"&#9646; REGIME: {html.escape(regime_label)}",
            "</td>",
            f"<td align=\"right\" style=\"font-size:9.5px;color:{_TEXT_MUTED};\">{html.escape(all_tags_text)}</td>",
            "</tr></table>",
            "</td></tr>",
            # Meta row
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:7px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">",
            "<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\"><tr>",
            f"<td style=\"font-size:10.5px;line-height:1.35;color:{_TEXT_SECONDARY};\"><strong style=\"color:{_TEXT_PRIMARY};\">GENERATED</strong> {html.escape(generated_local)}</td>",
            f"<td align=\"center\" style=\"font-size:10.5px;line-height:1.35;color:{_TEXT_SECONDARY};\"><strong style=\"color:{_TEXT_PRIMARY};\">PROFILE</strong> {html.escape(profile_name)}</td>",
            f"<td align=\"right\" style=\"font-size:10.5px;line-height:1.35;color:{_TEXT_SECONDARY};\"><strong style=\"color:{_TEXT_PRIMARY};\">MODE</strong> {html.escape(delivery_mode)} · <strong style=\"color:{_TEXT_PRIMARY};\">CONF</strong> {html.escape(confidence)}<br><strong style=\"color:{_TEXT_PRIMARY};\">VISUAL</strong> {html.escape(density_mode)} · {chart_count} charts</td>",
            "</tr></table>",
            "</td></tr>",
            # Section confidence row
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:7px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">",
            f"<div style=\"font-size:10.5px;line-height:1.35;color:{_TEXT_SECONDARY};\">"
            f"<strong style=\"color:{_TEXT_PRIMARY};\">SECTION CONFIDENCE</strong><br>"
            + "<br>".join(html.escape(line) for line in section_conf_lines)
            + "</div>",
            "</td></tr>",
            # Source freshness row
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:7px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">",
            f"<div style=\"font-size:10.5px;line-height:1.35;color:{_TEXT_SECONDARY};\">"
            f"<strong style=\"color:{_TEXT_PRIMARY};\">DATA FRESHNESS · SOURCE</strong><br>"
            + "<br>".join(html.escape(line) for line in freshness_lines)
            + "</div>",
            "</td></tr>",
            # Jump-link nav row
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:6px 16px 6px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">",
            self._nav_row(),
            "</td></tr>",
        ]

        if desk_read:
            parts.append(
                f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:9px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};"
                f"font-size:12.5px;line-height:1.38;color:{_TEXT_PRIMARY};\">"
                f"{desk_read}</td></tr>"
            )
        if trigger_lines:
            parts.append(
                f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:8px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
                f"<div style=\"font-size:10px;line-height:1.4;color:{_TEXT_MUTED};letter-spacing:0.05em;font-weight:800;\">TODAY'S TRIGGERS</div>"
                f"<div style=\"margin-top:4px;font-size:11.5px;line-height:1.4;color:{_TEXT_SECONDARY};\">"
                + "<br>".join(html.escape(line) for line in trigger_lines)
                + "</div></td></tr>"
            )
        if market_clock_lines:
            parts.append(
                f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:8px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
                f"<div style=\"font-size:10px;line-height:1.4;color:{_TEXT_MUTED};letter-spacing:0.05em;font-weight:800;\">MARKET CLOCK</div>"
                f"<div style=\"margin-top:4px;font-size:11.5px;line-height:1.4;color:{_TEXT_SECONDARY};\">"
                + "<br>".join(html.escape(line) for line in market_clock_lines)
                + "</div></td></tr>"
            )
        if change_lines:
            parts.append(
                f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:8px 16px;border-bottom:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
                f"<div style=\"font-size:10px;line-height:1.4;color:{_TEXT_MUTED};letter-spacing:0.05em;font-weight:800;\">WHAT CHANGED</div>"
                f"<div style=\"margin-top:4px;font-size:11.5px;line-height:1.4;color:{_TEXT_SECONDARY};\">"
                + "<br>".join(html.escape(line) for line in change_lines)
                + "</div></td></tr>"
            )

        if briefing.chart_assets:
            parts.append(f"<tr><td bgcolor=\"{_CHART_PANEL_BG}\" style=\"padding:10px 16px 0 16px;background:{_CHART_PANEL_BG};background-color:{_CHART_PANEL_BG};\">")
            parts.append(self._chart_modules(briefing))
            parts.append("</td></tr>")

        commodity_grid = self._commodity_grid_html(briefing)
        if commodity_grid:
            parts.append(commodity_grid)

        breadth_row = self._breadth_row_html(briefing)
        if breadth_row:
            parts.append(breadth_row)

        parts.append(f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:0 16px 16px 16px;background:{_SECTION_BG};background-color:{_SECTION_BG};\">")
        if (briefing.session_key or "morning").lower() != "morning":
            base = self._web_public_base_url()
            profile = str(((briefing.morning_chart_bundle or {}).get("meta") or {}).get("profile_name") or "default_user")
            parts.append(
                f"<div style=\"font-size:11px;line-height:1.4;color:{_TEXT_SECONDARY};padding:0 0 10px 0;\">"
                f"<a href=\"{html.escape(base + '/ui/briefing/charts/watchlist?profile=' + profile)}\" "
                f"style=\"color:{_TEXT_SECONDARY};text-decoration:none;border-bottom:1px dotted {_TEXT_MUTED};\">Open Watchlist Explorer</a>"
                "</div>"
            )
        parts.append(self._brief_modules(full_html))
        parts.append("</td></tr>")

        parts.extend(["</table>", "</td></tr></table>", "</body></html>"])
        self._move_shading_baselines = {}
        return "".join(parts)

    @staticmethod
    def _web_public_base_url() -> str:
        base = str(os.getenv("WEB_PUBLIC_BASE_URL", "")).strip().rstrip("/")
        if base:
            return base
        return "http://127.0.0.1:8080"

    @staticmethod
    def _market_clock_lines(briefing: MorningBriefing) -> list[str]:
        ctx = briefing.market_clock_context or {}
        lines: list[str] = []
        open_now = ctx.get("open_now") or []
        recently_closed = ctx.get("recently_closed") or []
        opening_next = ctx.get("opening_next") or []
        open_src = str(ctx.get("open_now_source") or "computed")
        closed_src = str(ctx.get("recently_closed_source") or "computed")
        next_src = str(ctx.get("opening_next_source") or "computed")
        focus = (ctx.get("focus") or "").strip()
        if open_now:
            label = "Open now" if open_src == "computed" else "Open now context"
            lines.append(f"{label}: {', '.join(str(x) for x in open_now[:4])}")
        if recently_closed:
            label = "Recently closed" if closed_src == "computed" else "Recently closed context"
            lines.append(f"{label}: {', '.join(str(x) for x in recently_closed[:4])}")
        if opening_next:
            label = "Opening next" if next_src == "computed" else "Opening next context"
            lines.append(f"{label}: {', '.join(str(x) for x in opening_next[:4])}")
        if focus:
            lines.append(f"Session focus: {focus}")
        return lines

    @staticmethod
    def _nav_row() -> str:
        links = " &nbsp;|&nbsp; ".join(
            f"<a href=\"#{slug}\" style=\"color:{_TEXT_MUTED};font-size:10px;font-weight:700;text-decoration:none;"
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
                color = _POSITIVE if chg_pct >= 0 else _NEGATIVE
                sign = "+" if chg_pct >= 0 else ""
                chg_html = f"<span style=\"color:{color};\">{sign}{chg_pct:.2f}%</span>"
            else:
                chg_html = ""
            name = (m.name or m.series_id).replace(" (USD/bbl)", "").replace(" (USD/troy oz)", "").replace(" (USD/MMBtu)", "")
            val = f"{m.value:,.2f}"
            cells.append(
                f"<td style=\"padding:3px 10px 3px 0;font-size:11px;color:{_TEXT_PRIMARY};white-space:nowrap;\">"
                f"<span style=\"color:{_TEXT_MUTED};\">{html.escape(name)}</span>&nbsp;"
                f"<strong>{html.escape(val)}</strong>&nbsp;{chg_html}</td>"
            )
        # Two columns
        rows_html = ""
        for i in range(0, len(cells), 2):
            pair = cells[i:i+2]
            rows_html += f"<tr>{''.join(pair)}</tr>"
        return (
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:8px 16px;border-bottom:1px solid {_DIVIDER};"
            f"background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
            f"<div style=\"font-size:10px;color:{_TEXT_MUTED};letter-spacing:0.05em;font-weight:700;"
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
            color = _POSITIVE if chg >= 0 else _NEGATIVE
            sign = "+" if chg >= 0 else ""
            cells.append(
                f"<td style=\"padding:2px 8px 2px 0;font-size:10px;white-space:nowrap;\">"
                    f"<span style=\"color:{_TEXT_MUTED};\">{html.escape(b.display_name or b.symbol)}</span>&nbsp;"
                    f"<span style=\"color:{color};\">{sign}{chg:.1f}%</span></td>"
            )
        rows_html = ""
        for i in range(0, len(cells), 4):
            rows_html += f"<tr>{''.join(cells[i:i+4])}</tr>"
        summary_color = _POSITIVE if up >= dn else _NEGATIVE
        return (
            f"<tr><td bgcolor=\"{_SECTION_BG}\" style=\"padding:8px 16px;border-bottom:1px solid {_DIVIDER};"
            f"background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
            f"<div style=\"font-size:10px;color:{_TEXT_MUTED};letter-spacing:0.05em;font-weight:700;"
            f"margin-bottom:5px;\">SECTOR BREADTH &nbsp;"
            f"<span style=\"color:{summary_color};\">{up}↑ {dn}↓</span></div>"
            f"<table role=\"presentation\" cellspacing=\"0\" cellpadding=\"0\">{rows_html}</table>"
            f"</td></tr>"
        )

    def _chart_modules(self, briefing: MorningBriefing) -> str:
        roles = {row.get("chart_key"): row.get("role") for row in (briefing.morning_chart_selection or [])}
        modules = [
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_CHART_PANEL_BG}\" style=\"border-collapse:collapse;background:{_CHART_PANEL_BG};background-color:{_CHART_PANEL_BG};\">"
        ]
        for idx, asset in enumerate(briefing.chart_assets):
            role = str(roles.get(asset.key) or ("hero" if idx == 0 else "support"))
            is_hero = role == "hero" or idx == 0
            is_micro = role.startswith("micro")
            title_size = "15px" if is_hero else ("13px" if is_micro else "14px")
            pad_top = "0" if idx == 0 else ("8px" if is_micro else "11px")
            pad_bottom = "13px" if is_hero else ("9px" if is_micro else "11px")
            read_line, why_line, lens_line = self._chart_copy_triplet(asset.key, asset.caption)
            modules.append(
                f"<tr><td bgcolor=\"{_CHART_PANEL_BG}\" style=\"padding:{pad_top} 0 {pad_bottom} 0;border-bottom:1px solid {_DIVIDER};background:{_CHART_PANEL_BG};background-color:{_CHART_PANEL_BG};\">"
            )
            modules.append(
                f"<div style=\"font-size:{title_size};line-height:1.15;font-weight:800;color:{_TEXT_PRIMARY};letter-spacing:-0.01em;padding:0 0 4px 0;\">{html.escape(asset.title)}</div>"
            )
            modules.append(
                f"<div style=\"font-size:12px;line-height:1.35;color:{_TEXT_SECONDARY};padding:0 0 11px 0;\"><span style=\"color:{_FOCUS_ORANGE};font-size:10px;letter-spacing:0.05em;font-weight:800;\">READ</span> {html.escape(read_line)}</div>"
            )
            modules.append(
                f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                "width=\"640\" style=\"display:block;width:100%;max-width:640px;height:auto;margin-top:0;border:0;\">"
            )
            modules.append(
                f"<div style=\"font-size:11px;line-height:1.35;color:{_TEXT_SECONDARY};padding:7px 0 0 0;\"><span style=\"color:{_TEXT_MUTED};font-size:10px;letter-spacing:0.04em;font-weight:800;\">WHY IT MATTERS</span> {html.escape(why_line)}</div>"
            )
            modules.append(
                f"<div style=\"font-size:12px;line-height:1.45;color:{_TEXT_PRIMARY};padding:6px 0 0 0;\"><span style=\"color:{_TEXT_MUTED};font-size:10px;letter-spacing:0.04em;font-weight:800;\">PORTFOLIO LENS</span> {html.escape(lens_line)}</div>"
            )
            if str(asset.key or "").strip().lower() == "watchlist_performance_snapshot":
                base = self._web_public_base_url()
                profile = str(((briefing.morning_chart_bundle or {}).get("meta") or {}).get("profile_name") or "default_user")
                links = [
                    ("Open 1D", f"{base}/ui/briefing/charts/watchlist?profile={profile}&period=1D&mode=rebased"),
                    ("Open 1M", f"{base}/ui/briefing/charts/watchlist?profile={profile}&period=1M&mode=rebased"),
                    ("Open 1Y", f"{base}/ui/briefing/charts/watchlist?profile={profile}&period=1Y&mode=rebased"),
                    ("Open 5Y", f"{base}/ui/briefing/charts/watchlist?profile={profile}&period=5Y&mode=rebased"),
                    ("Full explorer", f"{base}/ui/briefing/charts/watchlist?profile={profile}"),
                ]
                modules.append(
                    f"<div style=\"font-size:11px;line-height:1.35;color:{_TEXT_SECONDARY};padding:6px 0 0 0;\">"
                    "Open interactive explorer for 1D, 1M, 1Y, 5Y, zoom and hover."
                    "</div>"
                )
                modules.append(
                    f"<div style=\"font-size:11px;line-height:1.35;color:{_TEXT_SECONDARY};padding:4px 0 0 0;\">"
                    + " · ".join(
                        f"<a href=\"{html.escape(url)}\" style=\"color:{_TEXT_SECONDARY};text-decoration:none;border-bottom:1px dotted {_TEXT_MUTED};\">{html.escape(label)}</a>"
                        for label, url in links
                    )
                    + "</div>"
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
                    f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_SECTION_BG}\" style=\"margin-top:10px;background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
                    f"<tr><td{anchor_id} bgcolor=\"{_SECTION_BG}\" style=\"padding:11px 0 9px 0;font-size:13px;color:{_TEXT_PRIMARY};line-height:1.38;border-top:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
                    f"<div style=\"font-size:15px;line-height:1.18;font-weight:800;letter-spacing:-0.01em;color:{_TEXT_PRIMARY};margin:0 0 6px 0;\">{html.escape(header_text)}</div>"
                    f"{body}"
                    "</td></tr></table>"
                )
        body = self._format_section_body(lines, section_title)
        return (
            f"<table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" bgcolor=\"{_SECTION_BG}\" style=\"margin-top:10px;background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
            f"<tr><td{anchor_id} bgcolor=\"{_SECTION_BG}\" style=\"padding:11px 0 9px 0;font-size:13px;color:{_TEXT_PRIMARY};line-height:1.38;border-top:1px solid {_DIVIDER};background:{_SECTION_BG};background-color:{_SECTION_BG};\">"
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
        if len(words) > 28:
            text = " ".join(words[:28]).rstrip(".,;:") + "."
        return text

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
                "Cross-asset pressure shows where macro stress is entering first: rates, commodities, or risk gauges."
            )
        if key == "geo_confirmation_ladder":
            return "Geo risk is more credible when oil, volatility, and equities confirm the same stress direction."
        if key == "oil_transmission_card":
            return "Oil shocks can reprice inflation expectations, margins, transport costs, and equity risk premia."
        if key == "regional_divergence_score":
            return "Regional divergence shows whether risk appetite is global or concentrated in one session."
        if key == "watchlist_movers_card":
            return "Watchlist dispersion shows whether portfolio-relevant risk is broad or concentrated in one or two names."
        if key == "setup_confirmation_card":
            return "This checks whether the session is confirming or fading the original setup across regions, rates, volatility, and breadth."
        if key == "yield_curve_shape":
            lower = base.lower()
            if "10y move lower" in lower or re.search(r"10y move .*?\(-\d+(?:\.\d+)?\s*bp\)", lower):
                return (
                    "Lower long-end yields can ease discount-rate pressure for growth and duration assets, "
                    "but declines can also reflect weaker growth expectations or a defensive shift."
                )
            return "Higher long-end yields usually increase discount-rate pressure for growth equities and duration-sensitive assets."
        if key == "watchlist_performance_snapshot":
            return "Static email snapshot for quick context; use the web explorer for interactive period and benchmark comparisons."
        if key == "volatility_regime_card":
            return "A rising VIX below 20 signals caution and fragility, but not full panic by itself."
        if key == "breadth_leadership_panel":
            return (
                "Breadth is a confirmation test, not a direction forecast: clustered positives support trend durability, while split signals "
                "usually imply rotation-heavy tape and lower conviction on outright index follow-through."
            )
        if key == "pnl_attribution_waterfall":
            return (
                "Attribution is contribution-weighted, so concentration can dominate the day even when headline breadth looks balanced. "
                "The key question is whether the total is broad-based or driven by one sleeve."
            )
        if key == "event_linked_annotated_trend":
            return (
                "The event marker separates pre-catalyst drift from post-catalyst repricing; persistence after the marker matters more than the "
                "initial spike. Sustained slope suggests a regime handoff rather than a one-session reaction."
            )
        if key in {"portfolio_concentration_risk", "portfolio_concentration_risk_card"}:
            return (
                "High top-weight concentration means daily P&L can be dominated by a small number of sleeves rather than broad market direction."
            )
        if key == "global_relative_performance":
            return (
                "Rebased leadership isolates relative momentum across regions; widening endpoints indicate persistent regional divergence, while "
                "converging endpoints typically signal a risk-beta catch-up phase."
            )
        if base:
            short = " ".join(base.split()[:26]).rstrip(".,;:")
            return f"Signal context is {bias}: {short}."
        return "Use this panel as context alongside breadth and cross-asset confirmation."

    def _chart_copy_triplet(self, chart_key: str | None, caption: str | None) -> tuple[str, str, str]:
        read_line = self._chart_read_line(caption)
        why_line = self._chart_explainer_paragraph(chart_key, caption)
        lens_line = self._chart_portfolio_lens(chart_key, caption)
        read_line, why_line, lens_line = distinct_lines(read_line, why_line, lens_line)
        if not chart_copy_is_distinct(read_line, why_line, lens_line):
            why_line = "Why it matters: confirm the move with breadth and cross-asset follow-through before upgrading conviction."
            lens_line = "Portfolio lens: prioritize concentration, directional beta, and macro-sensitivity in position sizing."
        return read_line, why_line, lens_line

    @staticmethod
    def _chart_portfolio_lens(chart_key: str | None, caption: str | None) -> str:
        key = str(chart_key or "").strip().lower()
        base = (caption or "").strip()
        if key == "cross_asset_impulse_strip":
            return "Rates pressure maps first to BND/IEF/LQD and then to long-duration growth exposure."
        if key == "geo_confirmation_ladder":
            return "Use this as macro risk context for ACWI/SPY/QQQ and energy-sensitive exposures, not as a standalone trade signal."
        if key == "oil_transmission_card":
            return "Watch whether energy strength worsens equity weakness or remains isolated to commodity-sensitive sleeves."
        if key == "regional_divergence_score":
            return "Europe/global equity sleeves are most exposed when Europe leads the downside."
        if key == "watchlist_movers_card":
            return "Separate index-level pressure from single-name idiosyncratic moves before changing posture."
        if key == "setup_confirmation_card":
            return "If setup confirmation weakens, treat intraday strength as lower quality until breadth and rates stabilise."
        if key == "yield_curve_shape":
            lower = base.lower()
            if "10y move lower" in lower or re.search(r"10y move .*?\(-\d+(?:\.\d+)?\s*bp\)", lower):
                return "Watch BND/IEF/LQD for duration sensitivity and QQQ/growth sleeves for discount-rate effects."
            return "Watch BND/IEF/LQD for duration drag and QQQ/growth sleeves if yields keep rising."
        if key == "watchlist_performance_snapshot":
            return "Use the interactive explorer to check whether watchlist strength is broad or concentrated."
        if key == "volatility_regime_card":
            return "Watch whether volatility confirms the regional/rates pressure before reducing risk."
        if key == "breadth_leadership_panel":
            return "If breadth remains split, avoid treating headline index strength as broad risk confirmation."
        if key == "pnl_attribution_waterfall":
            lower_base = (base or "").lower()
            if "no displayed sleeve offset the decline" in lower_base:
                return "Losses are broad across displayed sleeves; treat the day as drag-led until positive contribution breadth improves."
            symbols = re.findall(r"([A-Z]{2,6})\s+[+\-]\d+(?:\.\d+)?%", base or "")
            if len(symbols) >= 2:
                return f"{symbols[0]} is the main directional driver today; validate whether offsets from {symbols[1]} and other sleeves are broad enough."
            return "Use top contributor vs top drag to decide whether the day is concentrated or broad."
        if key == "event_linked_annotated_trend":
            return "Persistence after the catalyst window matters more than the first-day reaction when sizing follow-through risk."
        if key in {"portfolio_concentration_risk", "portfolio_concentration_risk_card"}:
            return "High top-weight concentration means daily P&L can be top-sleeve driven until contribution breadth broadens."
        if key == "global_relative_performance":
            return "Regional leaders should align with portfolio geography; widening spreads can lift tracking-error risk."
        if base:
            return "Link this signal to current portfolio beta, duration, and concentration before changing posture."
        return "Use this panel with the setup read and dominant driver before changing posture."

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
        driver = briefing.dominant_tape_driver.strip() if briefing.dominant_tape_driver else "No single equity catalyst dominates."
        posture = briefing.session_quality_label or briefing.regime_context or "mixed"
        regional = briefing.regional_skew_summary or "regional read unavailable"
        lines = [f"Desk read: {driver}"]
        if briefing.market_setup_analysis:
            lines.append(f"Setup read: {briefing.market_setup_analysis}")
        us = _avg_region_move(briefing, ("S&P", "NASDAQ", "DOW", "RUSSELL"))
        eu = _avg_region_move(briefing, ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
        asia = _avg_region_move(briefing, ("NIKKEI", "HANG SENG", "HSI"))
        lines.append(
            f"Risk posture: {posture.lower()}; US {us:+.2f}%, Europe {eu:+.2f}%, Asia {asia:+.2f} ({regional})."
        )
        if briefing.session_key in {"us_pre_open", "us_intraday_risk", "into_close"}:
            charts = {str(row.get("chart_key")): row for row in (briefing.morning_chart_bundle or {}).get("charts", [])}
            setup_card = charts.get("setup_confirmation_card") or {}
            setup_line = str(setup_card.get("caption") or "").strip()
            if setup_line:
                lines.append(f"Setup confirmation: {setup_line}")
        if briefing.geo_risk_summary:
            lines.append(f"Geo lens: {briefing.geo_risk_summary}")
        if briefing.portfolio_action_posture:
            lines.append(f"Portfolio implication: {briefing.portfolio_action_posture}.")
        return lines[:4]

    def _trigger_lines(self, briefing: MorningBriefing) -> list[str]:
        triggers: list[str] = []
        vix = next(
            (
                float(q.current_price)
                for q in (briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes)
                if "VIX" in (q.display_name or q.symbol or "").upper() and q.current_price is not None
            ),
            None,
        )
        ten_y = next(
            (
                float(q.current_price)
                for q in briefing.market_setup.macro_quotes
                if "10Y" in (q.display_name or q.symbol or "").upper() and q.current_price is not None
            ),
            None,
        )
        oil = next(
            (
                float(q.current_price)
                for q in briefing.market_setup.macro_quotes
                if any(tok in (q.display_name or q.symbol or "").upper() for tok in ("WTI", "CRUDE")) and q.current_price is not None
            ),
            None,
        )
        europe_moves = [
            float(q.change_percent or 0.0)
            for q in briefing.market_setup.index_quotes
            if any(tok in (q.display_name or q.symbol or "").upper() for tok in ("STOXX", "DAX", "CAC", "FTSE", "IBEX"))
        ]
        europe_avg = (sum(europe_moves) / len(europe_moves)) if europe_moves else None
        if vix is not None:
            triggers.append(f"VIX > 20 confirms broader risk-off pressure (now {vix:.2f}).")
        if ten_y is not None:
            triggers.append(f"US 10Y > 4.45% would reinforce rates-repricing pressure (now {ten_y:.2f}%).")
        if oil is not None:
            triggers.append(f"WTI > $107 would signal escalating energy shock (now ${oil:.2f}).")
        if europe_avg is not None:
            triggers.append(f"Europe average move below -1.5% would confirm deeper regional weakness (now {europe_avg:+.2f}%).")
        triggers.append("Nasdaq turning negative would indicate the growth cushion is failing.")
        return triggers[:5]

    @staticmethod
    def _what_changed_lines(briefing: MorningBriefing) -> list[str]:
        if briefing.what_changed_lines:
            return list(briefing.what_changed_lines)
        if not briefing.regime_shift:
            return ["No prior comparable snapshot available."]
        lines: list[str] = []
        for key, value in sorted(briefing.regime_shift.items()):
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {value}")
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
            return f'<strong style="color:{_FOCUS_ORANGE};font-weight:800;">{label}:</strong>'

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
        section = (section_title or "").strip().lower()
        if section in {"watchlist", "portfolio impact today", "portfolio check", "portfolio attribution"}:
            context = "watchlist"
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
        in_target_section = section in {
            "market setup",
            "market snapshot",
            "macro context",
            "watchlist",
            "sector scan",
            "portfolio impact today",
            "portfolio check",
            "portfolio attribution",
        }
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
        if re.search(r"\b[A-Z0-9\.\-]{2,8}\s+[+\-−]\d", plain):
            return "watchlist"
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
            return _TEXT_PRIMARY
        if context in {"watchlist", "portfolio_move"}:
            return move_color_hex(value)
        if abs(value) < 1e-12:
            return _NEUTRAL_MOVE
        positive_is_good = context not in {"stress", "rates"}
        good_move = (value > 0 and positive_is_good) or (value < 0 and not positive_is_good)
        if not self.enable_move_intensity_shading:
            return _POSITIVE if good_move else _NEGATIVE
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
        if briefing.data_freshness:
            lines = [
                f"Market Prices: {briefing.data_freshness.get('Market Prices', 'unavailable')}",
                f"News: {briefing.data_freshness.get('News', f'live, generated {generated_local}')}",
                f"Macro/FRED: {briefing.data_freshness.get('Macro/FRED', 'latest available release')}",
                f"Portfolio P&L: {briefing.data_freshness.get('Portfolio P&L', 'based on prior-close prices')}",
            ]
            summary = contract_warning_summary(briefing.contract_warnings or [])
            lines.append(f"Contract checks: {summary}")
            return lines

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

    def _confidence_summary(self, briefing: MorningBriefing, bundle_meta: dict) -> str:
        if briefing.section_confidence:
            levels = [str(v).upper() for v in briefing.section_confidence.values()]
            if "LOW" in levels:
                if levels.count("LOW") >= 2:
                    return "MEDIUM"
                return "MED-HIGH"
            if all(level == "HIGH" for level in levels):
                return "HIGH"
            return "MED-HIGH"
        return self._confidence_label_legacy(briefing, bundle_meta)

    def _section_confidence_lines(self, briefing: MorningBriefing) -> list[str]:
        if briefing.section_confidence:
            return [f"{name}: {value}" for name, value in briefing.section_confidence.items()]
        return [
            "Regime: MEDIUM",
            "Market prices: MEDIUM",
            "News: MEDIUM",
            "Macro/rates: MEDIUM",
            "Portfolio: MEDIUM",
            "Geo risk: MEDIUM",
        ]

    def _confidence_label_legacy(self, briefing: MorningBriefing, bundle_meta: dict) -> str:
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
