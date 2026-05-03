"""Static chart rendering for email/Telegram delivery."""

from __future__ import annotations

import os
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from app.logger import get_logger
from app.schemas.delivery import ChartAsset
from app.schemas.events import PricePoint, QuoteData

_mpl_config = Path(tempfile.gettempdir()) / "briefly-mpl"
_mpl_config.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

logger = get_logger("chart_renderer")

POSITIVE = "#00D4AA"
NEGATIVE = "#FF6B6B"
NEUTRAL = "#4A90E2"
ACCENT = "#FF6B00"
BG = "#071629"
PANEL = "#0B1D30"
GRID = "#2A3441"
AXIS = "#3D4451"
MUTED = "#7A8FA0"
TEXT = "#E8ECEF"
SUBTLE = "#14263A"

REGION_COLORS = {
    "us": ACCENT,
    "europe": "#4A90E2",
    "asia": "#FFD700",
    "other": "#9BA3AB",
}


class ChartRenderer:
    """Render deterministic static PNG chart cards."""

    @staticmethod
    def _figure(width: float = 8.8, height: float = 4.9):
        fig, ax = plt.subplots(figsize=(width, height), dpi=200)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        return fig, ax

    @staticmethod
    def _style_axes(
        ax,
        *,
        title: str,
        xlabel: str | None = None,
        ylabel: str | None = None,
        grid_axis: str = "y",
    ) -> None:
        ax.set_title(title, loc="left", fontsize=14.5, weight="bold", color=TEXT, pad=10)
        if xlabel:
            ax.set_xlabel(xlabel, color=MUTED, fontsize=9.0)
        if ylabel:
            ax.set_ylabel(ylabel, color=MUTED, fontsize=9.0)
        ax.tick_params(axis="x", colors=MUTED, labelsize=8.5)
        ax.tick_params(axis="y", colors=MUTED, labelsize=8.5)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=8))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.5, alpha=0.58)
        for spine in ax.spines.values():
            spine.set_visible(False)

    @staticmethod
    def _value_box(
        ax,
        x: float,
        y: float,
        label: str,
        *,
        color: str = TEXT,
        ha: str = "left",
        va: str = "center",
        fontsize: float = 8.6,
    ) -> None:
        ax.text(
            x,
            y,
            label,
            ha=ha,
            va=va,
            fontsize=fontsize,
            color=color,
            weight="bold",
            bbox={"facecolor": BG, "edgecolor": "none", "alpha": 0.82, "pad": 1.2},
            zorder=6,
        )

    @staticmethod
    def _series_color(row: dict, idx: int) -> str:
        family = str(row.get("family") or "").lower()
        if idx == 0:
            return ACCENT
        return REGION_COLORS.get(family, REGION_COLORS["other"])

    @staticmethod
    def _line_series_for_email(series: list[dict], limit: int = 7) -> list[dict]:
        ranked = sorted(
            series,
            key=lambda row: abs(float((row.get("y_5d") or [100.0])[-1]) - 100.0),
            reverse=True,
        )
        return ranked[:limit]

    @staticmethod
    def _label_positions(values: list[float], *, min_gap: float = 0.34) -> list[float]:
        indexed = sorted(enumerate(values), key=lambda item: item[1])
        adjusted = [float(value) for value in values]
        previous: float | None = None
        for original_idx, value in indexed:
            next_value = float(value) if previous is None else max(float(value), previous + min_gap)
            adjusted[original_idx] = next_value
            previous = next_value
        return adjusted

    @staticmethod
    def _impulse_sort_key(row: dict) -> tuple[int, float]:
        text = f"{row.get('name') or ''} {row.get('symbol') or ''}".lower()
        if "yield" in text or "curve" in text or "ust" in text:
            bucket = 0
        elif "wti" in text or "crude" in text or "gold" in text:
            bucket = 1
        else:
            bucket = 2
        return bucket, -abs(float(row.get("impulse") or 0.0))

    def render_from_spec(self, spec: dict) -> ChartAsset | None:
        key = str(spec.get("chart_key") or "").strip().lower()
        if not key or not bool(spec.get("available")):
            return None
        if key == "global_relative_performance":
            return self.render_global_relative_from_spec(spec)
        if key == "cross_asset_impulse_strip":
            return self.render_cross_asset_impulse_from_spec(spec)
        if key == "holdings_excess_performance":
            return self.render_holdings_excess_from_spec(spec)
        if key == "sector_exposure_quadrant":
            return self.render_sector_quadrant_from_spec(spec)
        if key == "event_linked_annotated_trend":
            return self.render_event_linked_from_spec(spec)
        if key == "breadth_leadership_panel":
            return self.render_breadth_leadership_from_spec(spec)
        if key == "rates_curve_micro_panel":
            return self.render_rates_curve_micro_from_spec(spec)
        if key == "volatility_regime_card":
            return self.render_volatility_regime_from_spec(spec)
        if key == "portfolio_concentration_risk_card":
            return self.render_concentration_risk_from_spec(spec)
        if key == "earnings_relevance_strip":
            return self.render_earnings_relevance_from_spec(spec)
        return None

    def render_market_snapshot(self, quotes: list[QuoteData]) -> ChartAsset | None:
        if not quotes:
            return None

        labels = [q.display_name or q.symbol for q in quotes[:4]]
        values = [q.change_percent for q in quotes[:4]]
        colors = [POSITIVE if v >= 0 else NEGATIVE for v in values]

        fig, ax = plt.subplots(figsize=(8.2, 4.6), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        bars = ax.bar(labels, values, color=colors, edgecolor="none")
        ax.axhline(0, color=GRID, linewidth=1.2)
        ax.set_title("Market Snapshot", loc="left", fontsize=15, weight="bold", color=TEXT)
        ax.set_ylabel("Change %", color=TEXT)
        ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
        ax.tick_params(axis="x", labelrotation=0, colors=TEXT)
        ax.tick_params(axis="y", colors=TEXT)
        for spine in ax.spines.values():
            spine.set_visible(False)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + (0.08 if value >= 0 else -0.18),
                f"{value:+.2f}%",
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=10,
                color=TEXT,
                weight="bold",
            )
        fig.tight_layout()
        return self._to_asset(
            fig,
            key="market_snapshot",
            title="Market Snapshot",
            caption="Major index moves at the latest captured quote.",
            filename="market-snapshot.png",
        )

    def render_performance_snapshot(
        self,
        quotes: list[QuoteData],
        *,
        title: str,
        key: str,
        caption: str,
    ) -> ChartAsset | None:
        if not quotes:
            return None

        ordered = sorted(quotes, key=lambda q: q.change_percent, reverse=True)[:6]
        labels = [q.display_name or q.symbol for q in ordered]
        values = [q.change_percent for q in ordered]
        colors = [POSITIVE if v >= 0 else NEGATIVE for v in values]

        fig, ax = plt.subplots(figsize=(8.2, 4.8), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        bars = ax.barh(labels, values, color=colors, edgecolor="none")
        ax.axvline(0, color=GRID, linewidth=1.2)
        ax.set_title(title, loc="left", fontsize=15, weight="bold", color=TEXT)
        ax.set_xlabel("Change %", color=TEXT)
        ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.8)
        ax.tick_params(axis="x", colors=TEXT)
        ax.tick_params(axis="y", colors=TEXT)
        ax.invert_yaxis()
        for spine in ax.spines.values():
            spine.set_visible(False)
        for bar, value in zip(bars, values):
            ax.text(
                value + (0.08 if value >= 0 else -0.08),
                bar.get_y() + bar.get_height() / 2,
                f"{value:+.2f}%",
                va="center",
                ha="left" if value >= 0 else "right",
                fontsize=10,
                color=TEXT,
                weight="bold",
            )
        fig.tight_layout()
        return self._to_asset(
            fig,
            key=key,
            title=title,
            caption=caption,
            filename=f"{key}.png",
        )

    def render_macro_risk_strip(self, quotes: list[QuoteData]) -> ChartAsset | None:
        """Render a compact macro risk strip from macro instrument moves."""
        if not quotes:
            return None

        selected = quotes[:5]
        labels = [q.display_name or q.symbol for q in selected]
        values = [q.change_percent for q in selected]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in values]

        fig, ax = plt.subplots(figsize=(8.2, 4.2), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        bars = ax.barh(labels, values, color=colors, edgecolor="none")
        ax.axvline(0, color=GRID, linewidth=1.2)
        ax.set_title("Macro Risk Strip", loc="left", fontsize=15, weight="bold", color=TEXT)
        ax.set_xlabel("Change %", color=TEXT)
        ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.8)
        ax.tick_params(axis="x", colors=TEXT)
        ax.tick_params(axis="y", colors=TEXT)
        ax.invert_yaxis()
        for spine in ax.spines.values():
            spine.set_visible(False)

        for bar, value in zip(bars, values):
            ax.text(
                value + (0.06 if value >= 0 else -0.06),
                bar.get_y() + bar.get_height() / 2,
                f"{value:+.2f}%",
                va="center",
                ha="left" if value >= 0 else "right",
                fontsize=9.5,
                color=TEXT,
                weight="bold",
            )

        fig.tight_layout()
        return self._to_asset(
            fig,
            key="macro_risk_strip",
            title="Macro Risk Strip",
            caption="Cross-asset risk gauges (rates, commodities, dollar, crypto) at the latest capture.",
            filename="macro-risk-strip.png",
        )

    def render_sector_exposure_performance(
        self,
        points: list[tuple[str, float, float]],
    ) -> ChartAsset | None:
        """Render portfolio sector exposure overlaid with ETF performance."""
        if not points:
            return None

        top_points = sorted(points, key=lambda item: item[1], reverse=True)[:6]
        labels = [label for label, _, _ in top_points]
        exposures = [weight for _, weight, _ in top_points]
        changes = [change for _, _, change in top_points]
        colors = [POSITIVE if change >= 0 else NEGATIVE for change in changes]

        fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        bars = ax.bar(labels, exposures, color=ACCENT, alpha=0.78, edgecolor="none")
        ax.set_ylim(0, max(exposures) * 1.35 if exposures else 1.0)
        ax.set_ylabel("Portfolio Weight", color=TEXT)
        ax.set_title("Sector Exposure vs Performance", loc="left", fontsize=15, weight="bold", color=TEXT)
        ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
        ax.tick_params(axis="x", labelrotation=18, colors=TEXT)
        ax.tick_params(axis="y", colors=TEXT)
        for spine in ax.spines.values():
            spine.set_visible(False)

        for bar, exposure in zip(bars, exposures):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                exposure + 0.01,
                f"{exposure:.0%}",
                ha="center",
                va="bottom",
                fontsize=9,
                color=TEXT,
            )

        ax2 = ax.twinx()
        ax2.axhline(0, color=GRID, linewidth=1.0)
        ax2.scatter(
            range(len(labels)),
            changes,
            color=colors,
            s=84,
            zorder=5,
        )
        ax2.set_ylabel("ETF Change %", color=TEXT)
        ax2.tick_params(axis="y", colors=TEXT)
        for spine in ax2.spines.values():
            spine.set_visible(False)
        for idx, change in enumerate(changes):
            ax2.text(
                idx,
                change + (0.08 if change >= 0 else -0.08),
                f"{change:+.2f}%",
                ha="center",
                va="bottom" if change >= 0 else "top",
                fontsize=9,
                color=TEXT,
                weight="bold",
            )

        fig.tight_layout()
        return self._to_asset(
            fig,
            key="sector_exposure_performance",
            title="Sector Exposure vs Performance",
            caption="Portfolio sector concentration with the latest corresponding sector ETF move.",
            filename="sector-exposure-performance.png",
        )

    def render_price_history(
        self,
        symbol_label: str,
        history: list[PricePoint],
    ) -> ChartAsset | None:
        if len(history) < 2:
            return None

        history = history[-30:]
        x = [point.timestamp for point in history]
        y = [point.close for point in history]
        start = y[0]
        end = y[-1]
        change_pct = ((end - start) / start * 100) if start else 0.0
        line_color = POSITIVE if change_pct >= 0 else NEGATIVE

        fig, ax = plt.subplots(figsize=(8.4, 4.8), dpi=150)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        ax.plot(x, y, color=line_color, linewidth=2.4)
        ax.fill_between(x, y, min(y), color=line_color, alpha=0.10)
        ax.set_title(f"{symbol_label} Trend", loc="left", fontsize=15, weight="bold", color=TEXT)
        ax.text(
            0.01,
            0.92,
            f"{start:,.2f} to {end:,.2f} ({change_pct:+.2f}%)",
            transform=ax.transAxes,
            fontsize=10.5,
            color=TEXT,
        )
        ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
        ax.tick_params(axis="x", colors=TEXT, labelrotation=0)
        ax.tick_params(axis="y", colors=TEXT)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.tight_layout()
        return self._to_asset(
            fig,
            key="focus_symbol",
            title=f"{symbol_label} Trend",
            caption="Recent price trend for the most relevant portfolio-linked symbol in this briefing.",
            filename="focus-symbol.png",
        )

    def render_global_relative_from_spec(self, spec: dict) -> ChartAsset | None:
        series = self._line_series_for_email(list(spec.get("series") or []), limit=7)
        if not series:
            return None
        fig, ax = self._figure(8.8, 5.0)
        y_latest: list[float] = []
        label_rows: list[tuple[int, str, float, float, str]] = []
        for idx, row in enumerate(series):
            x = row.get("x_5d") or []
            y = row.get("y_5d") or []
            if not x or not y:
                continue
            name = str(row.get("name") or row.get("symbol") or f"Series {idx + 1}")
            color = self._series_color(row, idx)
            alpha = 0.98 if idx <= 2 else 0.62
            linewidth = 2.9 if idx == 0 else (2.15 if idx <= 2 else 1.35)
            ax.plot(x, y, color=color, linewidth=linewidth, alpha=alpha)
            y_latest.append(float(y[-1]))
            label = name.replace(" Composite", "").replace("EURO STOXX 50", "STOXX50")
            label_rows.append((idx, label, float(x[-1]), float(y[-1]), color))

        ax.axhline(100, color=ACCENT, linewidth=1.0, alpha=0.62)
        if y_latest:
            spread = max(y_latest) - min(y_latest)
            ax.text(
                0.01,
                0.03,
                f"5D rebased · leadership spread {spread:.2f} pts",
                transform=ax.transAxes,
                color=MUTED,
                fontsize=8.4,
                weight="bold",
            )
            q1 = min(y_latest) + (max(y_latest) - min(y_latest)) * 0.25
            q3 = min(y_latest) + (max(y_latest) - min(y_latest)) * 0.75
            label_candidates = [
                row for row in label_rows if row[0] == 0 or row[3] <= q1 or row[3] >= q3
            ][:5]
            adjusted = self._label_positions([row[3] for row in label_candidates])
            for row, y_pos in zip(label_candidates, adjusted):
                idx, label, x_val, y_val, color = row
                self._value_box(
                    ax,
                    x_val + 0.12,
                    y_pos,
                    f"{label} {y_val:.1f}",
                    color=color,
                    fontsize=7.8 if idx > 2 else 8.2,
                )
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Global Equity Leadership"),
            xlabel="5D session path",
            ylabel="Rebased to 100",
            grid_axis="y",
        )
        ax.margins(x=0.2, y=0.18)
        fig.subplots_adjust(left=0.08, right=0.82, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="global_relative_performance",
            title=str(spec.get("title") or "Global Equity Leadership"),
            caption=str(spec.get("caption") or ""),
            filename="global-relative-performance.png",
        )

    def render_cross_asset_impulse_from_spec(self, spec: dict) -> ChartAsset | None:
        points = sorted(list(spec.get("series") or []), key=self._impulse_sort_key)
        if not points:
            return None
        labels = [self._compact_impulse_label(str(row.get("name") or row.get("symbol") or "")) for row in points]
        values = [float(row.get("impulse") or 0.0) for row in points]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in values]

        fig, ax = self._figure(8.8, 4.9)
        y_pos = list(range(len(labels)))
        ax.axvline(0, color=TEXT, linewidth=1.55, alpha=0.9)
        ax.hlines(y=y_pos, xmin=[0 for _ in values], xmax=values, color=colors, linewidth=2.8, alpha=0.88)
        ax.scatter(values, y_pos, color=colors, s=88, edgecolor=BG, linewidth=1.1, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT, fontsize=9.2, fontweight="bold")
        ax.invert_yaxis()

        min_value = min(values) if values else -1.0
        max_value = max(values) if values else 1.0
        x_pad = max(1.0, (max_value - min_value) * 0.16)
        ax.set_xlim(min(min_value - x_pad, -0.5), max(max_value + x_pad, 0.5))

        for idx, row in enumerate(points):
            unit = str(row.get("unit") or "pct")
            value = float(row.get("impulse") or 0.0)
            suffix = "bp" if unit == "bps" else "%"
            x_text = value + (0.20 if value >= 0 else -0.20)
            self._value_box(
                ax,
                x_text,
                idx,
                f"{value:+.2f}{suffix}",
                color=colors[idx],
                ha="left" if value >= 0 else "right",
                fontsize=8.5,
            )
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Cross-Asset Impulses"),
            xlabel="Centered daily impulse (bps / %)",
            grid_axis="x",
        )
        ax.grid(False)
        ax.text(
            0.01,
            0.02,
            "Rates first, commodities next; zero line marks neutral transmission",
            transform=ax.transAxes,
            color=MUTED,
            fontsize=8.2,
            weight="bold",
        )
        fig.subplots_adjust(left=0.25, right=0.94, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="cross_asset_impulse_strip",
            title=str(spec.get("title") or "Cross-Asset Impulses"),
            caption=str(spec.get("caption") or ""),
            filename="cross-asset-impulses.png",
        )

    @staticmethod
    def _compact_impulse_label(label: str) -> str:
        base = (label or "").strip()
        lower = base.lower()
        if "treasury yield" in lower and "10y" in lower:
            return "US 10Y Yield"
        if "wti crude" in lower:
            return "WTI Crude"
        if "gold" in lower:
            return "Gold"
        if "curve" in lower:
            return "10Y-2Y Curve"
        if "(" in base:
            base = base.split("(", 1)[0].strip()
        return base

    def render_holdings_excess_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        rows = sorted(rows, key=lambda row: abs(float(row.get("excess_pct") or 0.0)), reverse=True)[:8]
        labels = [str(row.get("symbol") or row.get("name") or "") for row in rows]
        absolute = [float(row.get("change_pct") or 0.0) for row in rows]
        excess = [float(row.get("excess_pct") or 0.0) for row in rows]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in excess]

        fig, ax = self._figure(8.8, 5.0)
        y_pos = list(range(len(labels)))
        ax.axvline(0, color=ACCENT, linewidth=1.25, alpha=0.8)
        for idx, (abs_move, excess_move, color) in enumerate(zip(absolute, excess, colors)):
            ax.hlines(y=idx, xmin=min(abs_move, excess_move), xmax=max(abs_move, excess_move), color=GRID, linewidth=2.2, alpha=0.9)
            ax.scatter(abs_move, idx, color=NEUTRAL, s=62, edgecolor=BG, linewidth=1.0, zorder=4)
            ax.scatter(excess_move, idx, color=color, s=88, edgecolor=BG, linewidth=1.0, zorder=5)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT, fontsize=9.3, fontweight="bold")
        ax.invert_yaxis()
        for idx, (abs_move, value) in enumerate(zip(absolute, excess)):
            self._value_box(
                ax,
                value + (0.12 if value >= 0 else -0.12),
                idx,
                f"excess {value:+.2f}% · abs {abs_move:+.2f}%",
                ha="left" if value >= 0 else "right",
                fontsize=8.5,
                color=colors[idx],
            )
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Portfolio Movers vs Benchmark"),
            xlabel="Daily move (%) · blue=absolute, green/red=excess",
            grid_axis="x",
        )
        fig.subplots_adjust(left=0.16, right=0.89, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="holdings_excess_performance",
            title=str(spec.get("title") or "Portfolio Movers vs Benchmark"),
            caption=str(spec.get("caption") or ""),
            filename="holdings-excess-performance.png",
        )

    def render_sector_quadrant_from_spec(self, spec: dict) -> ChartAsset | None:
        points = list(spec.get("series") or [])
        if not points:
            return None

        x_vals = [float(row.get("x_exposure") or 0.0) for row in points]
        y_vals = [float(row.get("y_change_pct") or 0.0) for row in points]
        labels = [str(row.get("name") or row.get("sector_key") or "") for row in points]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in y_vals]
        sizes = [max(70.0, min(420.0, float(row.get("bubble_size") or 10.0) * 10.0)) for row in points]

        fig, ax = self._figure(8.8, 5.0)
        exposure_mid = max(0.0, sum(x_vals) / len(x_vals)) if x_vals else 0.0
        ax.axhline(0, color=ACCENT, linewidth=1.15, alpha=0.78)
        ax.axvline(exposure_mid, color=GRID, linewidth=1.0, linestyle="--", alpha=0.82)
        ax.scatter(x_vals, y_vals, color=colors, s=sizes, alpha=0.84, edgecolor=BG, linewidth=1.1)
        outlier_order = sorted(range(len(points)), key=lambda idx: abs(y_vals[idx]) + x_vals[idx] * 0.08, reverse=True)
        for idx in outlier_order[:7]:
            x_val = x_vals[idx]
            y_val = y_vals[idx]
            label = labels[idx].replace("Communication Services", "Comm Services")
            ax.text(
                x_val + 0.35,
                y_val + (0.08 if y_val >= 0 else -0.08),
                label,
                fontsize=8.1,
                color=TEXT,
                weight="bold" if idx in outlier_order[:3] else "normal",
            )
        ax.text(0.02, 0.92, "Underweight winners", transform=ax.transAxes, color=MUTED, fontsize=8.0)
        ax.text(0.74, 0.92, "Overweight winners", transform=ax.transAxes, color=MUTED, fontsize=8.0)
        ax.text(0.02, 0.06, "Underweight losers", transform=ax.transAxes, color=MUTED, fontsize=8.0)
        ax.text(0.74, 0.06, "Overweight losers", transform=ax.transAxes, color=MUTED, fontsize=8.0)
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Sector Exposure vs Move"),
            xlabel="Portfolio exposure (%)",
            ylabel="Sector move (%)",
            grid_axis="both",
        )
        fig.subplots_adjust(left=0.1, right=0.96, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="sector_exposure_quadrant",
            title=str(spec.get("title") or "Sector Exposure vs Move"),
            caption=str(spec.get("caption") or ""),
            filename="sector-exposure-quadrant.png",
        )

    def render_event_linked_from_spec(self, spec: dict) -> ChartAsset | None:
        series = list(spec.get("series") or [])
        if not series:
            return None
        row = series[0]
        x = row.get("x") or []
        y = row.get("y") or []
        if not x or not y:
            return None
        label = str(row.get("name") or row.get("symbol") or "Focus Symbol")

        fig, ax = self._figure(8.8, 5.0)
        line_color = ACCENT
        ax.plot(x, y, color=line_color, linewidth=2.7)
        marker = None
        for ann in (spec.get("annotations") or []):
            if ann.get("label") == "event_window_start":
                x_mark = int(ann.get("x") or 0)
                marker = x_mark
                ax.axvspan(x_mark, max(x), color=ACCENT, alpha=0.08)
                ax.axvline(x_mark, color=ACCENT, linewidth=1.15, linestyle="--", alpha=0.92)
        change_pct = ((float(y[-1]) / float(y[0])) - 1.0) * 100.0 if y[0] else 0.0
        self._value_box(
            ax,
            float(x[-1]),
            float(y[-1]),
            f"{label} {change_pct:+.2f}%",
            color=line_color,
            ha="right",
            va="bottom",
            fontsize=8.5,
        )
        if marker is not None:
            ax.text(
                marker,
                max(y),
                "event window",
                color=ACCENT,
                fontsize=8.2,
                weight="bold",
                ha="left",
                va="top",
            )
        self._style_axes(
            ax,
            title=str(spec.get("title") or f"{label} Event-Linked Trend"),
            xlabel="30D session window",
            ylabel="Price",
            grid_axis="y",
        )
        fig.subplots_adjust(left=0.1, right=0.95, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="event_linked_annotated_trend",
            title=str(spec.get("title") or f"{label} Event-Linked Trend"),
            caption=str(spec.get("caption") or ""),
            filename="event-linked-annotated-trend.png",
        )

    def render_breadth_leadership_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        labels = [str(row.get("name") or "") for row in rows]
        values = [float(row.get("value") or 0.0) for row in rows]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in values]
        fig, ax = self._figure(8.8, 4.5)
        y_pos = list(range(len(labels)))
        ax.axvline(0, color=ACCENT, linewidth=1.15, alpha=0.78)
        ax.barh(y_pos, values, color=colors, alpha=0.82, height=0.58)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=9, color=TEXT, fontweight="bold")
        ax.invert_yaxis()
        for idx, value in enumerate(values):
            self._value_box(
                ax,
                value + (0.08 if value >= 0 else -0.08),
                idx,
                f"{value:+.2f}",
                ha="left" if value >= 0 else "right",
                fontsize=8.8,
                color=colors[idx],
            )
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Breadth & Leadership"),
            xlabel="Signal value",
            grid_axis="x",
        )
        fig.subplots_adjust(left=0.24, right=0.91, top=0.86, bottom=0.18)
        return self._to_asset(
            fig,
            key="breadth_leadership_panel",
            title=str(spec.get("title") or "Breadth & Leadership"),
            caption=str(spec.get("caption") or ""),
            filename="breadth-leadership.png",
        )

    def render_rates_curve_micro_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        labels = [str(row.get("name") or "") for row in rows]
        levels = [row.get("level") for row in rows]
        impulses = [row.get("impulse") for row in rows]
        colors = [POSITIVE if (float(value or 0.0) >= 0) else NEGATIVE for value in impulses]
        fig, ax = self._figure(8.0, 2.9)
        ax.axis("off")
        ax.set_title(str(spec.get("title") or "Rates & Curve"), loc="left", fontsize=15, weight="bold", color=TEXT, pad=9)
        x0 = 0.03
        for idx, label in enumerate(labels):
            xpos = x0 + idx * 0.31
            lvl = levels[idx]
            imp = impulses[idx]
            level_text = "n/a" if lvl is None else f"{float(lvl):.3f}%"
            impulse_text = "n/a" if imp is None else f"{float(imp):+.2f}bp"
            color = colors[idx]
            ax.plot([xpos, xpos + 0.23], [0.72, 0.72], transform=ax.transAxes, color=ACCENT, linewidth=1.8, alpha=0.8)
            ax.text(xpos, 0.58, label.upper(), transform=ax.transAxes, fontsize=8.8, color=MUTED, weight="bold")
            ax.text(xpos, 0.35, level_text, transform=ax.transAxes, fontsize=15, color=TEXT, weight="bold")
            ax.text(
                xpos,
                0.14,
                impulse_text,
                transform=ax.transAxes,
                fontsize=9.4,
                color=color,
                bbox={"facecolor": "#18243A", "edgecolor": "none", "pad": 1.2},
            )
        return self._to_asset(
            fig,
            key="rates_curve_micro_panel",
            title=str(spec.get("title") or "Rates & Curve"),
            caption=str(spec.get("caption") or ""),
            filename="rates-curve-micro.png",
        )

    def render_volatility_regime_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        meta = dict(spec.get("meta") or {})
        level = None
        delta = None
        for row in rows:
            if str(row.get("name") or "").lower().startswith("vix level"):
                level = row.get("value")
            if str(row.get("name") or "").lower().startswith("vix delta"):
                delta = row.get("value")
        regime = str(meta.get("regime") or "unavailable").lower()
        regime_color = {
            "calm": POSITIVE,
            "normal": ACCENT,
            "elevated": "#D18C00",
            "stress": NEGATIVE,
        }.get(regime, NEUTRAL)
        fig, ax = self._figure(8.0, 2.9)
        ax.axis("off")
        ax.set_title(str(spec.get("title") or "Volatility Regime"), loc="left", fontsize=15, weight="bold", color=TEXT, pad=9)
        level_text = "n/a" if level is None else f"{float(level):.2f}"
        delta_text = "n/a" if delta is None else f"{float(delta):+.2f}%"
        ax.plot([0.04, 0.42], [0.72, 0.72], transform=ax.transAxes, color=ACCENT, linewidth=1.8, alpha=0.8)
        ax.text(0.04, 0.50, f"VIX {level_text}", transform=ax.transAxes, fontsize=19, color=TEXT, weight="bold")
        ax.text(0.04, 0.25, f"daily delta {delta_text}", transform=ax.transAxes, fontsize=10, color=MUTED, weight="bold")
        ax.text(
            0.60,
            0.45,
            regime.upper(),
            transform=ax.transAxes,
            fontsize=12,
            color=regime_color,
            weight="bold",
            bbox={"facecolor": SUBTLE, "edgecolor": GRID, "linewidth": 0.5, "pad": 2.4},
        )
        return self._to_asset(
            fig,
            key="volatility_regime_card",
            title=str(spec.get("title") or "Volatility Regime"),
            caption=str(spec.get("caption") or ""),
            filename="volatility-regime.png",
        )

    def render_concentration_risk_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        meta = dict(spec.get("meta") or {})
        if not rows:
            return None
        values = {str(row.get("name") or ""): row.get("value") for row in rows}
        top5 = float(values.get("Top 5 Weight") or 0.0)
        largest = float(values.get("Largest Position") or 0.0)
        holdings = int(values.get("Active Holdings") or 0)
        state = str(meta.get("risk_state") or "moderate")
        state_color = {"balanced": POSITIVE, "moderate": ACCENT, "concentrated": NEGATIVE}.get(state, NEUTRAL)
        fig, ax = self._figure(8.0, 2.9)
        ax.axis("off")
        ax.set_title(str(spec.get("title") or "Portfolio Concentration"), loc="left", fontsize=15, weight="bold", color=TEXT, pad=9)
        ax.plot([0.04, 0.44], [0.72, 0.72], transform=ax.transAxes, color=ACCENT, linewidth=1.8, alpha=0.8)
        ax.text(0.04, 0.50, f"Top 5 {top5:.1f}%", transform=ax.transAxes, fontsize=17, color=TEXT, weight="bold")
        ax.text(0.04, 0.27, f"Largest {largest:.1f}% · Holdings {holdings}", transform=ax.transAxes, fontsize=10, color=MUTED, weight="bold")
        ax.text(
            0.64,
            0.42,
            state.upper(),
            transform=ax.transAxes,
            fontsize=12,
            color=state_color,
            weight="bold",
            bbox={"facecolor": SUBTLE, "edgecolor": GRID, "linewidth": 0.5, "pad": 2.4},
        )
        return self._to_asset(
            fig,
            key="portfolio_concentration_risk_card",
            title=str(spec.get("title") or "Portfolio Concentration"),
            caption=str(spec.get("caption") or ""),
            filename="portfolio-concentration.png",
        )

    def render_earnings_relevance_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        labels = [str(row.get("name") or "") for row in rows]
        values = [float(row.get("value") or 0.0) for row in rows]
        fig, ax = self._figure(8.0, 2.9)
        bars = ax.bar(labels, values, color=ACCENT, alpha=0.88, width=0.58)
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + max(0.08, max(values or [1.0]) * 0.04),
                f"{value:.0f}",
                ha="center",
                va="bottom",
                fontsize=9.2,
                color=TEXT,
                weight="bold",
            )
        self._style_axes(
            ax,
            title=str(spec.get("title") or "Earnings Relevance"),
            ylabel="Count",
            grid_axis="y",
        )
        ax.tick_params(axis="x", labelrotation=0, colors=TEXT, labelsize=8.4)
        ax.set_ylim(0, max(values) * 1.35 if values and max(values) else 1.0)
        fig.subplots_adjust(left=0.08, right=0.97, top=0.84, bottom=0.22)
        return self._to_asset(
            fig,
            key="earnings_relevance_strip",
            title=str(spec.get("title") or "Earnings Relevance"),
            caption=str(spec.get("caption") or ""),
            filename="earnings-relevance-strip.png",
        )

    @staticmethod
    def _to_asset(
        fig,
        *,
        key: str,
        title: str,
        caption: str,
        filename: str,
    ) -> ChartAsset:
        buffer = BytesIO()
        try:
            fig.savefig(buffer, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
        finally:
            plt.close(fig)
        content = buffer.getvalue()
        return ChartAsset(
            key=key,
            title=title,
            caption=caption,
            filename=filename,
            content_type="image/png",
            content_id=f"{key}-{uuid.uuid4().hex[:10]}",
            content=content,
        )
