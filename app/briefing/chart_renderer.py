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

_mpl_config = Path(tempfile.gettempdir()) / "mbb-mpl"
_mpl_config.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = get_logger("chart_renderer")

POSITIVE = "#0F9D58"
NEGATIVE = "#D93025"
NEUTRAL = "#335C81"
ACCENT = "#1B4965"
BG = "#F7F9FC"
GRID = "#D8E2ED"
TEXT = "#102A43"


class ChartRenderer:
    """Render simple static PNG chart cards."""

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
