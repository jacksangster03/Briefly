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
import matplotlib.colors as mcolors
from matplotlib.ticker import MaxNLocator

logger = get_logger("chart_renderer")

POSITIVE = "#00C2A8"
NEGATIVE = "#FF5C64"
NEUTRAL = "#8FA4BA"
ACCENT = "#FF7A00"
WARNING = "#D18C00"
BG = "#03101D"
PANEL = "#081A2B"
GRID = "#2A3441"
AXIS = "#3D4451"
MUTED = "#7F93A8"
TEXT = "#EAF2FF"
SUBTLE = "#14263A"

REGION_COLORS = {
    "us": ACCENT,
    "europe": "#6FA8E8",
    "asia": POSITIVE,
    "other": NEUTRAL,
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
        title: str = "",
        xlabel: str | None = None,
        ylabel: str | None = None,
        grid_axis: str = "y",
        lock_x_ticks: bool = False,
        lock_y_ticks: bool = False,
    ) -> None:
        if title:
            ax.set_title(title, loc="left", fontsize=14.5, weight="bold", color=TEXT, pad=10)
        if xlabel:
            ax.set_xlabel(xlabel, color=MUTED, fontsize=9.0)
        if ylabel:
            ax.set_ylabel(ylabel, color=MUTED, fontsize=9.0)
        ax.tick_params(axis="x", colors=MUTED, labelsize=8.7, pad=5)
        ax.tick_params(axis="y", colors=MUTED, labelsize=8.7, pad=5)
        if not lock_x_ticks:
            ax.xaxis.set_major_locator(MaxNLocator(nbins=8))
        if not lock_y_ticks:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.5, alpha=0.58)
        for axis_name in ("top", "right"):
            ax.spines[axis_name].set_visible(False)
        for axis_name in ("left", "bottom"):
            ax.spines[axis_name].set_color(AXIS)
            ax.spines[axis_name].set_linewidth(0.8)

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
            bbox={"facecolor": BG, "edgecolor": "none", "alpha": 0.86, "pad": 1.2},
            zorder=6,
        )

    @staticmethod
    def _series_color(row: dict, idx: int) -> str:
        family = str(row.get("family") or "").lower()
        if idx == 0 and float(row.get("change_pct") or 0.0) >= 0:
            return ACCENT
        if float(row.get("change_pct") or 0.0) < 0:
            return NEGATIVE
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
    def _label_positions(values: list[float], *, min_gap: float = 0.34, low: float | None = None, high: float | None = None) -> list[float]:
        indexed = sorted(enumerate(values), key=lambda item: item[1])
        adjusted = [float(value) for value in values]
        previous: float | None = None
        for original_idx, value in indexed:
            next_value = float(value) if previous is None else max(float(value), previous + min_gap)
            if high is not None:
                next_value = min(next_value, high)
            if low is not None:
                next_value = max(next_value, low)
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
        if key == "yield_curve_shape":
            return self.render_yield_curve_from_spec(spec)
        if key == "vix_term_structure":
            return self.render_vix_term_structure_from_spec(spec)
        if key == "pnl_attribution_waterfall":
            return self.render_pnl_waterfall_from_spec(spec)
        if key == "rsi_momentum_heatmap":
            return self.render_rsi_heatmap_from_spec(spec)
        if key == "implied_move_strip":
            return self.render_implied_move_from_spec(spec)
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
        series = self._line_series_for_email(list(spec.get("series") or []), limit=5)
        if not series:
            return None
        fig, ax = self._figure(8.8, 5.0)
        y_latest: list[float] = []
        label_rows: list[tuple[int, str, float, float, str]] = []
        for idx, row in enumerate(series):
            x_raw = row.get("x_5d") or []
            y = row.get("y_5d") or []
            if not x_raw or not y:
                continue
            n = len(x_raw)
            # Replace integer indices with meaningful relative labels: T-4 … T
            x_numeric = list(range(n))
            name = str(row.get("name") or row.get("symbol") or f"Series {idx + 1}")
            color = self._series_color(row, idx)
            alpha = 0.98 if idx in (0, len(series) - 1) else 0.70
            linewidth = 3.0 if idx == 0 else (2.2 if idx == len(series) - 1 else 1.6)
            ax.plot(x_numeric, y, color=color, linewidth=linewidth, alpha=alpha)
            y_latest.append(float(y[-1]))
            label = name.replace(" Composite", "").replace("EURO STOXX 50", "STOXX50")
            label_rows.append((idx, label, float(x_numeric[-1]), float(y[-1]), color))

        ax.axhline(100, color=GRID, linewidth=1.0, alpha=0.78, linestyle="--")

        # Set x-axis ticks to relative session labels
        if label_rows:
            n_pts = max(len(line.get_xdata()) for line in ax.lines) if ax.lines else 5
            tick_positions = list(range(n_pts))
            tick_labels = [f"T-{n_pts - 1 - i}" if i < n_pts - 1 else "Today" for i in range(n_pts)]
            ax.set_xticks(tick_positions)
            ax.set_xticklabels(tick_labels, fontsize=9, color=MUTED)

        if y_latest:
            spread = max(y_latest) - min(y_latest)
            ax.text(
                0.01,
                0.03,
                f"5D rebased to 100 · spread {spread:.2f} pts · leader vs laggard",
                transform=ax.transAxes,
                color=MUTED,
                fontsize=8.4,
                weight="bold",
            )
            y_low = min(min(line.get_ydata()) for line in ax.lines) if ax.lines else None
            y_high = max(max(line.get_ydata()) for line in ax.lines) if ax.lines else None
            adjusted = self._label_positions(
                [row[3] for row in label_rows],
                low=(y_low + 0.12 if y_low is not None else None),
                high=(y_high - 0.12 if y_high is not None else None),
            )
            for row, y_pos in zip(label_rows, adjusted):
                idx, label, x_val, y_val, color = row
                excess = y_val - 100.0
                self._value_box(
                    ax,
                    x_val + 0.12,
                    y_pos,
                    f"{label} {excess:+.1f}%",
                    color=color,
                    fontsize=8.4 if idx in (0, len(series) - 1) else 7.8,
                )
        self._style_axes(
            ax,
            title="",
            xlabel="Trading session",
            ylabel="Rebased to 100",
            grid_axis="y",
            lock_x_ticks=True,
        )
        ax.margins(x=0.2, y=0.18)
        fig.subplots_adjust(left=0.10, right=0.82, top=0.94, bottom=0.19)
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
        labels = [
            self._truncate_label(
                self._compact_impulse_label(str(row.get("name") or row.get("symbol") or "")),
                max_len=32,
            )
            for row in points
        ]
        tick_labels = [self._truncate_label(label, max_len=28) for label in labels]
        values = [float(row.get("impulse") or 0.0) for row in points]
        units = [str(row.get("unit") or "pct") for row in points]
        normalized_values: list[float] = []
        for value, unit in zip(values, units):
            if unit == "bps":
                normalized_values.append(max(-2.5, min(2.5, value / 6.0)))
            else:
                normalized_values.append(max(-2.5, min(2.5, value / 1.2)))
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in values]

        # Cap the visual range if one outlier dominates (>3x the median abs value)
        abs_vals = sorted([abs(v) for v in normalized_values if v != 0.0])
        median_abs = abs_vals[len(abs_vals) // 2] if abs_vals else 1.0
        cap = max(abs_vals) if not abs_vals or max(abs_vals) <= median_abs * 3.5 else median_abs * 3.5
        display_values = [max(-cap, min(cap, v)) for v in normalized_values]
        capped_any = any(abs(v) > cap for v in normalized_values)

        fig, ax = self._figure(8.8, 4.9)
        y_pos = list(range(len(labels)))
        ax.axvline(0, color=AXIS, linewidth=1.55, alpha=0.9)
        ax.hlines(y=y_pos, xmin=[0 for _ in display_values], xmax=display_values, color=colors, linewidth=2.8, alpha=0.88)
        ax.scatter(display_values, y_pos, color=colors, s=88, edgecolor=BG, linewidth=1.1, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(tick_labels, color=TEXT, fontsize=9.0, fontweight="bold")
        ax.invert_yaxis()

        x_pad = max(1.0, cap * 0.22)
        ax.set_xlim(-cap - x_pad, cap + x_pad)

        # Group dividers: draw a subtle line between rates and commodities buckets
        buckets = [self._impulse_sort_key(row)[0] for row in points]
        for i in range(1, len(buckets)):
            if buckets[i] != buckets[i - 1]:
                ax.axhline(i - 0.5, color=GRID, linewidth=0.8, alpha=0.6, linestyle="--")

        for idx, row in enumerate(points):
            unit = str(row.get("unit") or "pct")
            value = float(row.get("impulse") or 0.0)
            suffix = "bp" if unit == "bps" else "%"
            dv = display_values[idx]
            # Keep value boxes inside the plot area to avoid overlapping y-axis labels.
            x_text = dv + (x_pad * 0.28 if dv >= 0 else -x_pad * 0.20)
            label_text = f"{value:+.2f}{suffix}" + (" ▶" if abs(value) > cap else "")
            self._value_box(
                ax,
                x_text,
                idx,
                label_text,
                color=colors[idx],
                ha="left" if dv >= 0 else "right",
                fontsize=8.5,
            )
        subtitle = "Normalized impulse score · rates (bps) and commodities/risk (%)"
        if capped_any:
            subtitle += "  (scale capped)"
        self._style_axes(
            ax,
            title="",
            xlabel=subtitle,
            grid_axis="x",
            lock_y_ticks=True,
        )
        ax.grid(False)
        fig.subplots_adjust(
            left=self._left_margin_for_labels(labels, min_margin=0.30, max_margin=0.42),
            right=0.94,
            top=0.86,
            bottom=0.18,
        )
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
        if "10y us treasury" in lower:
            return "US 10Y Yield"
        if "2y us treasury" in lower:
            return "US 2Y Yield"
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

    @staticmethod
    def _truncate_label(label: str, max_len: int = 24) -> str:
        text = (label or "").strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    @staticmethod
    def _left_margin_for_labels(labels: list[str], *, min_margin: float = 0.22, max_margin: float = 0.36) -> float:
        """Estimate a safe left subplot margin for long y-axis labels."""
        if not labels:
            return min_margin
        longest = max(len(label) for label in labels)
        margin = min_margin + max(0.0, float(longest - 18)) * 0.0052
        return max(min_margin, min(max_margin, margin))

    def render_holdings_excess_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        rows = sorted(rows, key=lambda row: float(row.get("excess_pct") or 0.0), reverse=True)[:8]
        labels = [self._truncate_label(str(row.get("symbol") or row.get("name") or ""), max_len=22) for row in rows]
        excess = [float(row.get("excess_pct") or 0.0) for row in rows]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in excess]

        fig, ax = self._figure(8.8, 5.0)
        y_pos = list(range(len(labels)))
        # Zero line labelled as "Benchmark"
        ax.axvline(0, color=AXIS, linewidth=1.25, alpha=0.9)
        ax.text(0, len(labels) - 0.3, "Benchmark", color=MUTED, fontsize=7.8, ha="center", va="top")
        ax.hlines(y=y_pos, xmin=[0 for _ in excess], xmax=excess, color=colors, linewidth=2.8, alpha=0.88)
        ax.scatter(excess, y_pos, color=colors, s=88, edgecolor=BG, linewidth=1.0, zorder=5)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT, fontsize=9.3, fontweight="bold")
        ax.invert_yaxis()

        x_range = max(abs(v) for v in excess) if excess else 1.0
        x_pad = max(0.15, x_range * 0.22)
        for idx, value in enumerate(excess):
            x_text = value + (x_pad * 0.5 if value >= 0 else -x_pad * 0.5)
            self._value_box(
                ax,
                x_text,
                idx,
                f"{value:+.2f}%",
                ha="left" if value >= 0 else "right",
                fontsize=8.8,
                color=colors[idx],
            )
        self._style_axes(
            ax,
            title="",
            xlabel="Excess return vs benchmark (%)",
            grid_axis="x",
            lock_y_ticks=True,
        )
        fig.subplots_adjust(
            left=self._left_margin_for_labels(labels, min_margin=0.18, max_margin=0.30),
            right=0.89,
            top=0.86,
            bottom=0.18,
        )
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
            title="",
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
        ax.plot(x, y, color=line_color, linewidth=2.7, zorder=3)
        ax.fill_between(x, y, min(y), color=line_color, alpha=0.10, zorder=2)
        y_min = float(min(y))
        y_max = float(max(y))
        y_span = max(y_max - y_min, 1e-9)

        event_label = ""
        for ann in (spec.get("annotations") or []):
            if ann.get("label") == "event_window_start":
                x_mark = int(ann.get("x") or 0)
                event_label = str(ann.get("event_name") or "event")
                # Shaded vertical band from event start to chart end
                ax.axvspan(x_mark, max(x), color=ACCENT, alpha=0.10, zorder=1)
                ax.axvline(x_mark, color=ACCENT, linewidth=1.4, linestyle="--", alpha=0.75, zorder=4)
                # Event band label inside the shaded region (kept away from endpoint callout).
                y_label_pos = y_max - (y_span * 0.06)
                ax.text(
                    x_mark + (max(x) - x_mark) * 0.04,
                    y_label_pos,
                    event_label,
                    color=ACCENT,
                    fontsize=8.2,
                    weight="bold",
                    ha="left",
                    va="top",
                    zorder=5,
                )
                window_pct = ((float(y[-1]) / float(y[x_mark])) - 1.0) * 100.0 if float(y[x_mark]) else 0.0
                ax.text(
                    x_mark + (max(x) - x_mark) * 0.04,
                    y_label_pos - (y_span * 0.08),
                    f"+{window_pct:.2f}% window" if window_pct >= 0 else f"{window_pct:.2f}% window",
                    color=MUTED,
                    fontsize=7.8,
                    ha="left",
                    va="top",
                    zorder=5,
                )
                break

        change_pct = ((float(y[-1]) / float(y[0])) - 1.0) * 100.0 if y[0] else 0.0
        x_left = float(min(x))
        x_right = float(max(x))
        x_span = max(x_right - x_left, 1.0)
        label_x = x_right - (x_span * 0.03)
        label_y = min(y_max - (y_span * 0.02), float(y[-1]) + (y_span * 0.05))
        self._value_box(
            ax,
            label_x,
            label_y,
            f"{label} {change_pct:+.2f}%",
            color=line_color,
            ha="right",
            va="bottom",
            fontsize=8.5,
        )
        self._style_axes(
            ax,
            title="",
            xlabel="30D session",
            ylabel="Price",
            grid_axis="y",
            lock_x_ticks=True,
        )
        fig.subplots_adjust(left=0.10, right=0.95, top=0.94, bottom=0.18)
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
        breadth_row = next((row for row in rows if "breadth" in str(row.get("name") or "").lower()), None)
        factor_rows = [row for row in rows if row is not breadth_row]
        if not factor_rows:
            return None

        labels = [self._truncate_label(str(row.get("name") or ""), max_len=32) for row in factor_rows]
        values = [float(row.get("value") or 0.0) for row in factor_rows]
        colors = [POSITIVE if value >= 0 else NEGATIVE for value in values]

        fig = plt.figure(figsize=(8.8, max(4.8, 0.5 * len(labels) + 2.4)), dpi=200)
        fig.patch.set_facecolor(BG)
        gs = fig.add_gridspec(2, 1, height_ratios=[1.1, 4.3], hspace=0.28)
        ax_top = fig.add_subplot(gs[0])
        ax = fig.add_subplot(gs[1])
        ax_top.set_facecolor(BG)
        ax.set_facecolor(BG)

        breadth_val = float((breadth_row or {}).get("value") or 0.0)
        ax_top.barh([0], [breadth_val], color=POSITIVE if breadth_val >= 50 else NEGATIVE, alpha=0.85, height=0.54)
        ax_top.set_xlim(0, 100)
        ax_top.set_yticks([0])
        ax_top.set_yticklabels(["Breadth % Up"], fontsize=8.8, color=TEXT, fontweight="bold")
        ax_top.set_xticks([0, 25, 50, 75, 100])
        ax_top.set_xticklabels(["0", "25", "50", "75", "100"], fontsize=8, color=MUTED)
        ax_top.grid(axis="x", color=GRID, linewidth=0.5, alpha=0.58)
        for spine in ax_top.spines.values():
            spine.set_visible(False)
        self._value_box(ax_top, min(98.0, breadth_val + 2.2), 0, f"{breadth_val:.1f}%", color=TEXT, ha="left", fontsize=8.5)

        y_pos = list(range(len(labels)))
        ax.barh(y_pos, values, color=colors, alpha=0.82, height=0.58)
        ax.axvline(0, color=ACCENT, linewidth=1.5, alpha=0.90, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8.5, color=TEXT, fontweight="bold")
        ax.invert_yaxis()

        x_range = max((abs(v) for v in values), default=1.0)
        pad = max(0.2, x_range * 0.35)
        ax.set_xlim(-x_range - pad, x_range + pad)

        for idx, value in enumerate(values):
            offset = max(0.04, x_range * 0.08)
            self._value_box(
                ax,
                value + (offset if value >= 0 else -offset),
                idx,
                f"{value:+.2f}",
                ha="left" if value >= 0 else "right",
                fontsize=8.8,
                color=colors[idx],
            )
        self._style_axes(
            ax,
            title="",
            xlabel="Leadership factor signal (negative = defensive, positive = pro-cyclical)",
            grid_axis="x",
            lock_y_ticks=True,
            lock_x_ticks=True,
        )
        fig.subplots_adjust(
            left=self._left_margin_for_labels(labels, min_margin=0.30, max_margin=0.43),
            right=0.93,
            top=0.95,
            bottom=0.14,
        )
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
        # Title intentionally omitted inside image; email section header provides title.
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
            "elevated": WARNING,
            "stress": NEGATIVE,
        }.get(regime, NEUTRAL)
        fig, ax = self._figure(8.0, 2.9)
        ax.axis("off")
        # Title intentionally omitted inside image; email section header provides title.
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
        state_color = {"balanced": POSITIVE, "moderate": ACCENT, "concentrated": WARNING}.get(state, NEUTRAL)

        # Thresholds for visual reference lines
        top5_threshold = 75.0
        largest_threshold = 15.0

        fig, ax = self._figure(8.4, 3.4)
        ax.set_facecolor(BG)
        ax.set_xlim(0, 100)
        ax.set_ylim(-0.5, 2.5)

        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        # Title intentionally omitted inside image; email section header provides title.

        bar_height = 0.38

        # Top 5 gauge
        top5_color = NEGATIVE if top5 >= top5_threshold else (ACCENT if top5 >= top5_threshold * 0.85 else POSITIVE)
        ax.barh(1.8, top5, height=bar_height, color=top5_color, alpha=0.82, left=0, zorder=3)
        ax.barh(1.8, 100, height=bar_height, color=SUBTLE, alpha=0.9, left=0, zorder=2)
        ax.axvline(top5_threshold, color=MUTED, linewidth=1.0, linestyle="--", alpha=0.6, ymin=0.62, ymax=0.82)
        ax.text(-1.5, 1.8, "Top 5", color=MUTED, fontsize=8.5, ha="right", va="center", weight="bold")
        ax.text(top5 + 1.5, 1.8, f"{top5:.1f}%", color=top5_color, fontsize=9.0, ha="left", va="center", weight="bold")
        ax.text(top5_threshold, 2.12, f"≥{top5_threshold:.0f}%", color=MUTED, fontsize=7.2, ha="center", va="bottom")

        # Largest position gauge
        lrg_color = NEGATIVE if largest >= largest_threshold else (ACCENT if largest >= largest_threshold * 0.8 else POSITIVE)
        ax.barh(1.0, largest, height=bar_height, color=lrg_color, alpha=0.82, left=0, zorder=3)
        ax.barh(1.0, 100, height=bar_height, color=SUBTLE, alpha=0.9, left=0, zorder=2)
        ax.axvline(largest_threshold, color=MUTED, linewidth=1.0, linestyle="--", alpha=0.6, ymin=0.28, ymax=0.48)
        ax.text(-1.5, 1.0, "Largest", color=MUTED, fontsize=8.5, ha="right", va="center", weight="bold")
        ax.text(largest + 1.5, 1.0, f"{largest:.1f}%", color=lrg_color, fontsize=9.0, ha="left", va="center", weight="bold")
        ax.text(largest_threshold, 1.32, f"≥{largest_threshold:.0f}%", color=MUTED, fontsize=7.2, ha="center", va="bottom")

        # Holdings count, VaR, and state label
        var_pct = float(values.get("1D 95% VaR") or 0.0) if values.get("1D 95% VaR") is not None else None
        holdings_text = f"{holdings} holdings"
        if var_pct is not None:
            holdings_text += f"  ·  1D 95% VaR {var_pct:.2f}%"
        ax.text(0, 0.18, holdings_text, color=TEXT, fontsize=10, ha="left", va="center", weight="bold")
        ax.text(
            60, 0.18,
            state.upper(),
            color=state_color,
            fontsize=10,
            ha="left",
            va="center",
            weight="bold",
            bbox={"facecolor": SUBTLE, "edgecolor": GRID, "linewidth": 0.5, "pad": 2.4},
        )

        fig.subplots_adjust(left=0.12, right=0.96, top=0.84, bottom=0.08)
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

        # Skip chart entirely when all buckets are empty; caller shows text fallback
        if not any(v > 0 for v in values):
            return None

        # Lollipop: filter to non-zero buckets only, sort by value
        non_zero = [(l, v) for l, v in zip(labels, values) if v > 0]
        non_zero.sort(key=lambda item: item[1], reverse=True)
        labels_nz = [item[0] for item in non_zero]
        values_nz = [item[1] for item in non_zero]
        colors_nz = [ACCENT if "portfolio" in l.lower() or "watchlist" in l.lower() else NEUTRAL for l in labels_nz]

        fig, ax = self._figure(8.0, max(2.9, 0.6 * len(labels_nz) + 1.4))
        y_pos = list(range(len(labels_nz)))
        ax.axvline(0, color=GRID, linewidth=0.8, alpha=0.6)
        ax.hlines(y=y_pos, xmin=0, xmax=values_nz, color=colors_nz, linewidth=2.5, alpha=0.88)
        ax.scatter(values_nz, y_pos, color=colors_nz, s=80, edgecolor=BG, linewidth=1.0, zorder=3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels_nz, color=TEXT, fontsize=9.0, fontweight="bold")
        ax.invert_yaxis()
        x_pad = max(0.5, max(values_nz) * 0.15)
        ax.set_xlim(0, max(values_nz) + x_pad)
        for idx, value in enumerate(values_nz):
            ax.text(
                value + x_pad * 0.3,
                idx,
                f"{value:.0f}",
                ha="left",
                va="center",
                fontsize=9.0,
                color=colors_nz[idx],
                weight="bold",
            )
        self._style_axes(
            ax,
            title="",
            xlabel="Count",
            grid_axis="x",
        )
        ax.grid(False)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        fig.subplots_adjust(left=0.28, right=0.94, top=0.84, bottom=0.18)
        return self._to_asset(
            fig,
            key="earnings_relevance_strip",
            title=str(spec.get("title") or "Earnings Relevance"),
            caption=str(spec.get("caption") or ""),
            filename="earnings-relevance-strip.png",
        )

    def render_yield_curve_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        tenors = [int(row["tenor"]) for row in rows]
        today_vals = [float(row["today"]) for row in rows]
        week_ago_vals = [float(row["week_ago"]) if row.get("week_ago") is not None else None for row in rows]

        fig, ax = self._figure(8.8, 4.8)
        ax.plot(tenors, today_vals, color=ACCENT, linewidth=2.8, marker="o", markersize=6, zorder=4, label="Today")
        if any(v is not None for v in week_ago_vals):
            wa = [v if v is not None else today_vals[i] for i, v in enumerate(week_ago_vals)]
            ax.plot(tenors, wa, color=MUTED, linewidth=1.8, linestyle="--", marker="o", markersize=4, zorder=3, label="1W ago")
            ax.fill_between(tenors, today_vals, wa, alpha=0.12, color=ACCENT, zorder=2)

        ax.axhline(0, color=GRID, linewidth=0.8, alpha=0.6)
        ax.set_xticks(tenors)
        ax.set_xticklabels(["2Y", "5Y", "10Y", "30Y"], color=MUTED, fontsize=9)
        for i, (tenor, val) in enumerate(zip(tenors, today_vals)):
            change = rows[i].get("change_bps")
            label = f"{val:.2f}%"
            if change is not None:
                sign = "+" if change >= 0 else ""
                label += f"\n{sign}{change:.0f}bp"
            ax.text(tenor, val + (max(today_vals) - min(today_vals)) * 0.06,
                    label, ha="center", va="bottom", fontsize=8.2, color=ACCENT, weight="bold", zorder=5)

        shape = str((spec.get("meta") or {}).get("shape") or "")
        ax.legend(frameon=False, labelcolor=MUTED, fontsize=8.5)
        self._style_axes(ax, title="",
                         xlabel="Tenor", ylabel="Yield (%)", grid_axis="y")
        ax.text(0.01, 0.04, f"Shape: {shape.upper()}" if shape else "",
                transform=ax.transAxes, color=MUTED, fontsize=8.2, weight="bold")
        fig.subplots_adjust(left=0.10, right=0.96, top=0.86, bottom=0.18)
        return self._to_asset(fig, key="yield_curve_shape",
                              title=str(spec.get("title") or "Yield Curve Shape"),
                              caption=str(spec.get("caption") or ""), filename="yield-curve.png")

    def render_vix_term_structure_from_spec(self, spec: dict) -> ChartAsset | None:
        series = list(spec.get("series") or [])
        if not series:
            return None
        meta = dict(spec.get("meta") or {})
        structure = str(meta.get("structure") or "unavailable")
        structure_color = {
            "backwardation": NEGATIVE,
            "contango": POSITIVE,
            "flat": MUTED,
        }.get(structure, MUTED)

        fig, ax = self._figure(8.8, 4.6)
        colors_map = [ACCENT, NEUTRAL]
        for idx, row in enumerate(series[:2]):
            x = row.get("x") or []
            y = row.get("y") or []
            if not x or not y:
                continue
            col = colors_map[idx]
            ax.plot(x, y, color=col, linewidth=2.4 if idx == 0 else 1.8, alpha=0.92)
            ax.fill_between(x, y, min(y), color=col, alpha=0.07)
            level = row.get("level")
            if level is not None:
                self._value_box(ax, x[-1] + 0.3, float(y[-1]),
                                f"{row['name']} {float(level):.1f}",
                                color=col, fontsize=8.2)

        ax.text(0.5, 0.91, structure.upper(), transform=ax.transAxes,
                color=structure_color, fontsize=11, weight="bold",
                ha="center", va="top",
                bbox={"facecolor": SUBTLE, "edgecolor": GRID, "linewidth": 0.5, "pad": 3})
        self._style_axes(ax, title="",
                         xlabel="Session", ylabel="VIX Level", grid_axis="y")
        ax.margins(x=0.18, y=0.18)
        fig.subplots_adjust(left=0.10, right=0.86, top=0.86, bottom=0.18)
        return self._to_asset(fig, key="vix_term_structure",
                              title=str(spec.get("title") or "VIX Term Structure"),
                              caption=str(spec.get("caption") or ""), filename="vix-term-structure.png")

    def render_pnl_waterfall_from_spec(self, spec: dict) -> ChartAsset | None:
        bars = [
            row for row in list(spec.get("series") or [])
            if str(row.get("symbol") or "").strip() not in {"", "-", "N/A"}
        ]
        if not bars:
            return None
        total = float((spec.get("annotations") or [{}])[0].get("value") or 0.0)

        # Sort: positives (descending) then negatives (ascending by abs), TOTAL always last
        pos_bars = sorted([r for r in bars if float(r.get("contribution") or 0.0) >= 0],
                          key=lambda r: float(r.get("contribution") or 0.0), reverse=True)
        neg_bars = sorted([r for r in bars if float(r.get("contribution") or 0.0) < 0],
                          key=lambda r: float(r.get("contribution") or 0.0))
        sorted_bars = pos_bars + neg_bars

        labels = [row["symbol"] for row in sorted_bars]
        contribs = [float(row["contribution"]) for row in sorted_bars]
        colors = [POSITIVE if c >= 0 else NEGATIVE for c in contribs]

        # Append TOTAL bar (visually separated by inserting a gap row)
        labels.append("TOTAL")
        contribs.append(total)
        colors.append(ACCENT)

        fig, ax = self._figure(8.8, max(4.0, 0.52 * len(labels) + 1.5))
        y_pos = list(range(len(labels)))

        # Simple diverging bars from zero — no waterfall offset
        ax.barh(y_pos, contribs, color=colors, alpha=0.84, height=0.55,
                edgecolor=BG, linewidth=0.6)

        # Prominent zero reference line
        ax.axvline(0, color=TEXT, linewidth=1.5, alpha=0.85, zorder=3)

        # Symmetric x-axis
        x_range = max((abs(c) for c in contribs if c != 0.0), default=0.5)
        pad = x_range * 0.35
        ax.set_xlim(-x_range - pad, x_range + pad)

        offset = x_range * 0.06
        for i, contrib in enumerate(contribs):
            series_label = labels[i] or "TOTAL"
            self._value_box(ax, contrib + (offset if contrib >= 0 else -offset), i,
                            f"{series_label} {contrib:+.2f}%",
                            ha="left" if contrib >= 0 else "right",
                            color=colors[i], fontsize=8.2)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT, fontsize=9, fontweight="bold")
        ax.invert_yaxis()
        self._style_axes(ax, title="",
                         xlabel="Weighted daily contribution (%)", grid_axis="x", lock_y_ticks=True)
        fig.subplots_adjust(left=0.16, right=0.92, top=0.86, bottom=0.14)
        return self._to_asset(fig, key="pnl_attribution_waterfall",
                              title=str(spec.get("title") or "P&L Attribution"),
                              caption=str(spec.get("caption") or ""), filename="pnl-attribution.png")

    def render_rsi_heatmap_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None
        annotations = list(spec.get("annotations") or [])
        overbought = next((float(a["value"]) for a in annotations if a.get("label") == "overbought"), 65.0)
        oversold = next((float(a["value"]) for a in annotations if a.get("label") == "oversold"), 35.0)

        symbols = [row["symbol"] for row in rows]
        windows = ["RSI(5)", "RSI(21)", "RSI(63)"]
        keys = ["rsi_5", "rsi_21", "rsi_63"]

        n_rows = len(symbols)
        n_cols = len(windows)
        matrix = []
        for row in rows:
            matrix.append([row.get(k) for k in keys])

        fig, ax = self._figure(8.0, max(2.8, 0.52 * n_rows + 1.4))
        ax.set_facecolor(BG)
        ax.axis("off")
        # Title intentionally omitted inside image; email section header provides title.

        cell_w = 0.20
        cell_h = 0.72 / max(n_rows, 1)
        x0 = 0.30
        y0 = 0.82

        # Column headers
        for j, win in enumerate(windows):
            ax.text(x0 + j * cell_w + cell_w / 2, y0 + 0.05, win,
                    transform=ax.transAxes, ha="center", va="bottom",
                    fontsize=8.5, color=MUTED, weight="bold")

        for i, (sym, vals) in enumerate(zip(symbols, matrix)):
            y = y0 - i * cell_h
            ax.text(x0 - 0.02, y - cell_h / 2, sym,
                    transform=ax.transAxes, ha="right", va="center",
                    fontsize=8.8, color=TEXT, weight="bold")
            for j, val in enumerate(vals):
                x = x0 + j * cell_w
                if val is None:
                    cell_color = SUBTLE
                    label = "n/a"
                    text_color = MUTED
                elif val >= overbought:
                    t = min(1.0, (val - overbought) / 20.0)
                    cell_color = mcolors.to_hex(mcolors.to_rgba(NEGATIVE, 0.25 + t * 0.55))
                    label = f"{val:.0f}"
                    text_color = NEGATIVE
                elif val <= oversold:
                    t = min(1.0, (oversold - val) / 20.0)
                    cell_color = mcolors.to_hex(mcolors.to_rgba(POSITIVE, 0.25 + t * 0.55))
                    label = f"{val:.0f}"
                    text_color = POSITIVE
                else:
                    cell_color = SUBTLE
                    label = f"{val:.0f}"
                    text_color = MUTED
                rect = plt.Rectangle((x, y - cell_h), cell_w - 0.012, cell_h - 0.015,
                                     transform=ax.transAxes,
                                     facecolor=cell_color, edgecolor=GRID, linewidth=0.5)
                ax.add_patch(rect)
                ax.text(x + cell_w / 2, y - cell_h / 2, label,
                        transform=ax.transAxes, ha="center", va="center",
                        fontsize=8.5, color=text_color, weight="bold")

        ax.text(0.01, 0.04, f"Red >={overbought:.0f} overbought  ·  Green <={oversold:.0f} oversold",
                transform=ax.transAxes, color=MUTED, fontsize=7.8)
        fig.subplots_adjust(left=0.04, right=0.98, top=0.88, bottom=0.06)
        return self._to_asset(fig, key="rsi_momentum_heatmap",
                              title=str(spec.get("title") or "Momentum / RSI"),
                              caption=str(spec.get("caption") or ""), filename="rsi-heatmap.png")

    def render_implied_move_from_spec(self, spec: dict) -> ChartAsset | None:
        rows = list(spec.get("series") or [])
        if not rows:
            return None

        labels = [row["symbol"] for row in rows]
        moves = [float(row["implied_move_pct"]) for row in rows]
        tags = [str(row.get("relevance_tag") or "") for row in rows]
        dates = [str(row.get("report_date") or "") for row in rows]
        colors = [ACCENT if t == "portfolio" else (NEUTRAL if t == "watchlist" else MUTED) for t in tags]

        fig, ax = self._figure(8.8, max(3.0, 0.55 * len(rows) + 1.4))
        y_pos = list(range(len(rows)))

        # Symmetric ± bars
        ax.axvline(0, color=AXIS, linewidth=1.2, alpha=0.9)
        ax.barh(y_pos, moves, color=colors, alpha=0.76, height=0.50, left=0)
        ax.barh(y_pos, [-m for m in moves], color=colors, alpha=0.76, height=0.50, left=0)

        x_max = max(moves) if moves else 5.0
        x_pad = x_max * 0.25
        for i, (move, date, col) in enumerate(zip(moves, dates, colors)):
            self._value_box(ax, move + x_pad * 0.3, i,
                            f"±{move:.1f}%  {date}", color=col,
                            ha="left", fontsize=8.2)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, color=TEXT, fontsize=9.3, fontweight="bold")
        ax.invert_yaxis()
        ax.set_xlim(-x_max - x_pad, x_max + x_pad * 2.5)
        self._style_axes(ax, title="",
                         xlabel="Options-implied ±move (%)", grid_axis="x", lock_y_ticks=True)
        ax.grid(False)
        ax.text(0.01, 0.04, "Orange = portfolio  ·  Teal = watchlist  ·  ATM straddle / spot",
                transform=ax.transAxes, color=MUTED, fontsize=7.8)
        fig.subplots_adjust(left=0.14, right=0.88, top=0.86, bottom=0.16)
        return self._to_asset(fig, key="implied_move_strip",
                              title=str(spec.get("title") or "Implied Earnings Moves"),
                              caption=str(spec.get("caption") or ""), filename="implied-move.png")

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
            fig.savefig(
                buffer,
                format="png",
                facecolor=fig.get_facecolor(),
                bbox_inches=None,
                pad_inches=0.06,
            )
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
