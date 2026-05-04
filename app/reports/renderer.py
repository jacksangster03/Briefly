"""Phase 7B: PDF renderer using fpdf2."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

from app.logger import get_logger

logger = get_logger("reports.renderer")

# Colour palette matching Briefly's email theme
_NAVY = (7, 22, 41)        # #071629
_ORANGE = (255, 102, 0)    # #FF6600
_WHITE = (255, 255, 255)
_LIGHT_GREY = (245, 246, 248)
_MID_GREY = (180, 185, 195)
_DARK_GREY = (60, 70, 85)
_GREEN = (34, 139, 34)
_RED = (200, 50, 50)


def _fmt_pct(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "-"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def _fmt_val(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _safe(text: str | None) -> str:
    """Replace characters outside Latin-1 range for fpdf2 core fonts."""
    if text is None:
        return "-"
    result = []
    for ch in str(text):
        try:
            ch.encode("latin-1")
            result.append(ch)
        except UnicodeEncodeError:
            result.append("-")
    return "".join(result)


class _BrieflyPDF:
    """Thin wrapper around FPDF2 for Briefly-styled reports."""

    def __init__(self, title: str):
        from fpdf import FPDF
        self.pdf = FPDF(orientation="P", unit="mm", format="A4")
        self.pdf.set_auto_page_break(auto=True, margin=20)
        self.title = title
        self.page_width = 210
        self.margin = 16
        self.content_width = self.page_width - 2 * self.margin

    def _set_font(self, style: str = "", size: int = 10):
        self.pdf.set_font("Helvetica", style=style, size=size)

    def _set_text_color(self, rgb: tuple):
        self.pdf.set_text_color(*rgb)

    def _set_fill_color(self, rgb: tuple):
        self.pdf.set_fill_color(*rgb)

    def _set_draw_color(self, rgb: tuple):
        self.pdf.set_draw_color(*rgb)

    def add_page(self):
        self.pdf.add_page()
        self._draw_header_bar()

    def _draw_header_bar(self):
        """Draw the orange top rule and navy branding strip."""
        # Orange top rule
        self.pdf.set_fill_color(*_ORANGE)
        self.pdf.rect(0, 0, self.page_width, 3, style="F")
        # Navy bar
        self.pdf.set_fill_color(*_NAVY)
        self.pdf.rect(0, 3, self.page_width, 12, style="F")
        # Wordmark
        self.pdf.set_xy(self.margin, 4)
        self.pdf.set_font("Helvetica", style="B", size=9)
        self.pdf.set_text_color(*_ORANGE)
        self.pdf.cell(60, 8, "BRIEFLY", align="L")
        # Page number
        self.pdf.set_xy(self.page_width - self.margin - 30, 4)
        self.pdf.set_font("Helvetica", size=7)
        self.pdf.set_text_color(*_MID_GREY)
        self.pdf.cell(30, 8, _safe(f"p. {self.pdf.page_no()}"), align="R")
        # Reset position
        self.pdf.set_xy(self.margin, 20)
        self.pdf.set_text_color(*_DARK_GREY)

    def section_heading(self, text: str):
        """Orange-ruled section heading."""
        y = self.pdf.get_y()
        if y > 260:
            self.add_page()
        self.pdf.set_xy(self.margin, self.pdf.get_y() + 4)
        self.pdf.set_font("Helvetica", style="B", size=11)
        self.pdf.set_text_color(*_NAVY)
        self.pdf.cell(self.content_width, 7, _safe(text), align="L")
        self.pdf.ln(1)
        # Orange underline
        y_line = self.pdf.get_y()
        self.pdf.set_draw_color(*_ORANGE)
        self.pdf.set_line_width(0.4)
        self.pdf.line(self.margin, y_line, self.margin + self.content_width, y_line)
        self.pdf.ln(5)
        self.pdf.set_text_color(*_DARK_GREY)

    def body_text(self, text: str, size: int = 9):
        self.pdf.set_font("Helvetica", size=size)
        self.pdf.set_text_color(*_DARK_GREY)
        self.pdf.set_x(self.margin)
        self.pdf.multi_cell(self.content_width, 5, _safe(text))
        self.pdf.ln(2)

    def kpi_row(self, items: list[tuple[str, str]]):
        """Render a row of KPI boxes."""
        if not items:
            return
        n = len(items)
        box_w = self.content_width / n
        x_start = self.margin
        y = self.pdf.get_y()

        for i, (label, value) in enumerate(items):
            x = x_start + i * box_w
            # Background
            self.pdf.set_fill_color(*_LIGHT_GREY)
            self.pdf.rect(x, y, box_w - 1, 16, style="F")
            # Label
            self.pdf.set_xy(x + 2, y + 2)
            self.pdf.set_font("Helvetica", size=7)
            self.pdf.set_text_color(*_MID_GREY)
            self.pdf.cell(box_w - 4, 4, _safe(label), align="L")
            # Value
            self.pdf.set_xy(x + 2, y + 6)
            self.pdf.set_font("Helvetica", style="B", size=10)
            self.pdf.set_text_color(*_NAVY)
            self.pdf.cell(box_w - 4, 6, _safe(str(value)), align="L")

        self.pdf.set_xy(self.margin, y + 18)
        self.pdf.set_text_color(*_DARK_GREY)

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[float] | None = None):
        """Render a simple table."""
        if not rows:
            return
        n = len(headers)
        if col_widths is None:
            col_widths = [self.content_width / n] * n

        # Header row
        self.pdf.set_fill_color(*_NAVY)
        self.pdf.set_text_color(*_WHITE)
        self.pdf.set_font("Helvetica", style="B", size=8)
        self.pdf.set_x(self.margin)
        for header, w in zip(headers, col_widths):
            self.pdf.cell(w, 6, _safe(header), border=0, align="L", fill=True)
        self.pdf.ln()

        # Data rows
        self.pdf.set_font("Helvetica", size=8)
        for idx, row in enumerate(rows):
            if self.pdf.get_y() > 265:
                self.add_page()
                # Re-draw header
                self.pdf.set_fill_color(*_NAVY)
                self.pdf.set_text_color(*_WHITE)
                self.pdf.set_font("Helvetica", style="B", size=8)
                self.pdf.set_x(self.margin)
                for header, w in zip(headers, col_widths):
                    self.pdf.cell(w, 6, _safe(header), border=0, align="L", fill=True)
                self.pdf.ln()
                self.pdf.set_font("Helvetica", size=8)

            fill = idx % 2 == 0
            self.pdf.set_fill_color(*(_LIGHT_GREY if fill else _WHITE))
            self.pdf.set_text_color(*_DARK_GREY)
            self.pdf.set_x(self.margin)
            for cell, w in zip(row, col_widths):
                self.pdf.cell(w, 5, _safe(str(cell))[:40], border=0, align="L", fill=True)
            self.pdf.ln()

        self.pdf.set_text_color(*_DARK_GREY)
        self.pdf.ln(3)

    def output_bytes(self) -> bytes:
        return bytes(self.pdf.output())


# ── Section builders ──────────────────────────────────────────────────────────

def _build_cover(doc: _BrieflyPDF, profile_name: str, title: str, state: dict[str, Any]):
    from datetime import date
    policy = state.get("policy") or {}

    # Large title area
    doc.pdf.set_xy(doc.margin, 50)
    doc.pdf.set_font("Helvetica", style="B", size=22)
    doc.pdf.set_text_color(*_NAVY)
    doc.pdf.multi_cell(doc.content_width, 12, _safe(title), align="L")

    doc.pdf.ln(4)
    doc.pdf.set_font("Helvetica", size=11)
    doc.pdf.set_text_color(*_DARK_GREY)
    doc.pdf.set_x(doc.margin)
    doc.pdf.cell(doc.content_width, 7, _safe(f"Portfolio: {profile_name}"), align="L")
    doc.pdf.ln()

    doc.pdf.set_font("Helvetica", size=9)
    doc.pdf.set_text_color(*_MID_GREY)
    doc.pdf.set_x(doc.margin)
    doc.pdf.cell(doc.content_width, 6, _safe(f"Generated: {date.today().strftime('%d %B %Y')}"), align="L")
    doc.pdf.ln(2)

    if policy.get("base_currency") or policy.get("investor_type"):
        doc.pdf.set_x(doc.margin)
        meta_parts = []
        if policy.get("base_currency"):
            meta_parts.append(f"Currency: {policy['base_currency']}")
        if policy.get("investor_type"):
            meta_parts.append(f"Investor type: {policy['investor_type'].replace('_', ' ').title()}")
        doc.pdf.cell(doc.content_width, 6, _safe("  |  ".join(meta_parts)), align="L")
        doc.pdf.ln()

    # Orange divider
    doc.pdf.ln(8)
    doc.pdf.set_draw_color(*_ORANGE)
    doc.pdf.set_line_width(0.8)
    y = doc.pdf.get_y()
    doc.pdf.line(doc.margin, y, doc.margin + doc.content_width, y)
    doc.pdf.ln(10)

    # Summary chips
    analysis = state.get("analysis") or {}
    kpis = analysis.get("kpis") or {}
    holdings_totals = analysis.get("holdings_totals") or {}

    chips = []
    if holdings_totals.get("weighted_positions"):
        chips.append(("Holdings", str(holdings_totals["weighted_positions"])))
    if kpis.get("total_weight_display"):
        chips.append(("Total Weight", kpis.get("total_weight_display", "—")))

    benchmark = state.get("benchmark") or {}
    if benchmark.get("name"):
        chips.append(("Benchmark", str(benchmark["name"])[:25]))

    risk_analytics = analysis.get("risk_analytics") or {}
    if risk_analytics.get("sharpe_ratio") is not None:
        chips.append(("Sharpe", _fmt_val(risk_analytics.get("sharpe_ratio"))))

    if chips:
        doc.kpi_row(chips[:4])
        doc.pdf.ln(6)

    doc.pdf.set_text_color(*_MID_GREY)
    doc.pdf.set_font("Helvetica", size=7)
    doc.pdf.set_x(doc.margin)
    doc.pdf.cell(doc.content_width, 5, "This report is generated by Briefly for informational purposes only.", align="L")


def _build_holdings_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Portfolio Holdings")
    holdings = state.get("holdings") or []
    analysis = state.get("analysis") or {}
    holdings_totals = analysis.get("holdings_totals") or {}

    if holdings_totals:
        doc.kpi_row([
            ("Positions", str(holdings_totals.get("weighted_positions", "—"))),
            ("Total Weight", str(holdings_totals.get("holdings_weight_total_display", "—"))),
        ])
        doc.pdf.ln(2)

    if not holdings:
        doc.body_text("No holdings data available.")
        return

    rows = []
    for h in holdings[:40]:
        if isinstance(h, dict):
            sym = h.get("symbol", "")
            wt = h.get("weight_pct")
            bucket = h.get("bucket") or "—"
            sector = h.get("sector_override") or h.get("sector") or "—"
            rows.append([
                sym,
                f"{wt:.1f}%" if wt is not None else "—",
                bucket.replace("_", " ").title(),
                sector.replace("_", " ").title(),
            ])

    doc.table(
        headers=["Symbol", "Weight", "Bucket", "Sector"],
        rows=rows,
        col_widths=[30, 25, 50, 73],
    )

    if len(holdings) > 40:
        doc.body_text(f"... and {len(holdings) - 40} more positions. See Appendix for full list.", size=8)


def _build_risk_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Performance & Risk Analytics")
    analysis = state.get("analysis") or {}
    risk = analysis.get("risk_analytics") or {}

    if not risk.get("available"):
        doc.body_text(risk.get("error") or "Risk analytics not available. Configure a benchmark and ensure holdings have weights.")
        return

    doc.kpi_row([
        ("Portfolio Return", risk.get("total_return_display", "—")),
        ("Benchmark Return", risk.get("benchmark_return_display", "—")),
        ("Active Return", risk.get("active_return_display", "—")),
        ("Sharpe Ratio", risk.get("sharpe_display", "—")),
    ])
    doc.pdf.ln(4)

    doc.kpi_row([
        ("Volatility", risk.get("volatility_display", "—")),
        ("Max Drawdown", risk.get("max_drawdown_display", "—")),
        ("Tracking Error", risk.get("tracking_error_display", "—")),
        ("Info Ratio", risk.get("information_ratio_display", "—")),
    ])
    doc.pdf.ln(4)

    if risk.get("risk_summary"):
        doc.body_text(risk["risk_summary"])

    doc.body_text(f"Lookback period: {risk.get('lookback_label', '—')}  |  Risk-free rate: {_fmt_pct(risk.get('risk_free_rate_pct'))}  |  Benchmark: {risk.get('benchmark_name', '—')}", size=8)


def _build_attribution_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Performance Attribution (Brinson)")
    analysis = state.get("analysis") or {}
    attribution = analysis.get("attribution") or {}

    if not attribution.get("available"):
        doc.body_text(attribution.get("error") or "Attribution requires CMA assumptions and allocation targets.")
        return

    doc.kpi_row([
        ("Portfolio Return", attribution.get("portfolio_return_display", "—")),
        ("Benchmark Return", attribution.get("benchmark_return_display", "—")),
        ("Active Return", attribution.get("active_return_display", "—")),
        ("Allocation Effect", attribution.get("allocation_effect_display", "—")),
    ])
    doc.pdf.ln(4)

    if attribution.get("summary"):
        doc.body_text(attribution["summary"])

    rows_data = attribution.get("rows") or []
    if rows_data:
        table_rows = []
        for r in rows_data[:15]:
            table_rows.append([
                r.get("label", r.get("asset_class", ""))[:25],
                f"{r.get('w_portfolio_pct', 0):.1f}%",
                f"{r.get('w_benchmark_pct', 0):.1f}%",
                f"{r.get('allocation_effect_pct', 0):+.4f}%",
            ])
        doc.table(
            headers=["Asset Class", "Port. Wt.", "Bench. Wt.", "Alloc. Effect"],
            rows=table_rows,
            col_widths=[70, 30, 30, 48],
        )


def _build_bonds_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Fixed Income Analytics")
    analysis = state.get("analysis") or {}
    bonds = analysis.get("bonds_analytics") or {}

    if not bonds.get("available"):
        doc.body_text(bonds.get("error") or "No fixed income holdings found.")
        return

    doc.kpi_row([
        ("Duration", f"{bonds.get('portfolio_duration_display', '—')} yrs"),
        ("YTM", bonds.get("portfolio_ytm_display", "—")),
        ("Rate Sensitivity (+100bps)", bonds.get("rate_sensitivity_display", "—")),
        ("Bond Weight", f"{bonds.get('total_bond_weight_pct', 0):.1f}%"),
    ])
    doc.pdf.ln(4)

    if bonds.get("summary"):
        doc.body_text(bonds["summary"])

    if bonds.get("quality_distribution"):
        doc.body_text("Credit Quality Distribution:", size=8)
        quality_labels = {"govt": "Government", "ig": "Investment Grade", "hy": "High Yield", "em": "Emerging Markets"}
        quality_rows = [
            [quality_labels.get(k, k.title()), f"{v:.1f}%"]
            for k, v in bonds["quality_distribution"].items()
        ]
        doc.table(headers=["Quality", "Weight"], rows=quality_rows, col_widths=[80, 98])

    if bonds.get("holdings_detail"):
        detail_rows = [
            [
                h["symbol"],
                f"{h['weight_pct']:.1f}%",
                f"{h['duration_yrs']:.1f} yrs",
                f"{h['ytm_pct']:.2f}%",
                h["credit_quality_label"],
            ]
            for h in bonds["holdings_detail"]
        ]
        doc.table(
            headers=["Symbol", "Weight", "Duration", "YTM", "Quality"],
            rows=detail_rows,
            col_widths=[28, 24, 28, 24, 74],
        )


def _build_scenarios_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Stress Scenarios")
    analysis = state.get("analysis") or {}
    scenarios = analysis.get("scenario_stress") or {}
    scenarios_list = scenarios.get("scenarios") or []

    if not scenarios_list:
        doc.body_text("No scenario data available.")
        return

    rows = []
    for s in scenarios_list[:10]:
        impact = s.get("portfolio_impact_pct") or s.get("impact_pct")
        rows.append([
            s.get("label", s.get("name", ""))[:40],
            _fmt_pct(impact, signed=True) if impact is not None else "—",
        ])
    doc.table(headers=["Scenario", "Est. Portfolio Impact"], rows=rows, col_widths=[120, 58])


def _build_cma_section(doc: _BrieflyPDF, state: dict[str, Any]):
    doc.section_heading("Capital Market Assumptions")
    analysis = state.get("analysis") or {}
    cma = analysis.get("cma_analytics") or {}

    if not cma.get("available"):
        doc.body_text("CMA assumptions not configured.")
        return

    doc.kpi_row([
        ("Expected Return", _fmt_pct(cma.get("expected_portfolio_return_pct"))),
        ("Expected Volatility", _fmt_pct(cma.get("expected_portfolio_volatility_pct"))),
        ("Expected Sharpe", _fmt_val(cma.get("expected_portfolio_sharpe"))),
    ])
    doc.pdf.ln(4)

    entries = cma.get("entries") or []
    if entries:
        rows = [
            [
                e.get("asset_class", "").replace("_", " ").title()[:30],
                _fmt_pct(e.get("expected_return_pct")),
                _fmt_pct(e.get("expected_volatility_pct")),
            ]
            for e in entries
        ]
        doc.table(headers=["Asset Class", "Exp. Return", "Exp. Volatility"], rows=rows, col_widths=[98, 30, 50])


# ── Main render entry point ───────────────────────────────────────────────────

_SECTION_BUILDERS = {
    "cover": _build_cover,
    "holdings": _build_holdings_section,
    "risk": _build_risk_section,
    "attribution": _build_attribution_section,
    "bonds": _build_bonds_section,
    "scenarios": _build_scenarios_section,
    "cma": _build_cma_section,
}


def render_pdf(
    filepath: Path,
    profile_name: str,
    title: str,
    sections: list[str],
    state: dict[str, Any],
) -> None:
    """Render the PDF to filepath using fpdf2."""
    doc = _BrieflyPDF(title=title)

    for section in sections:
        builder = _SECTION_BUILDERS.get(section)
        if builder is None:
            continue
        doc.add_page()
        if section == "cover":
            builder(doc, profile_name, title, state)
        else:
            builder(doc, state)

    filepath.write_bytes(doc.output_bytes())
    logger.info("PDF written: %s (%d bytes)", filepath, filepath.stat().st_size)
