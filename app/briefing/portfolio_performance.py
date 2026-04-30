"""Portfolio Performance Snapshot for morning briefings (Phase 5.9).

Pure formatter that takes a `risk_analytics` block (as produced by
`app.risk.service.compute_risk_analytics`) and renders a compact
Telegram/email-safe HTML section summarising the 22-metric workbench.

This is intentionally side-effect-free so it can be unit-tested and
slotted into `MorningFormatter._compose_morning` behind a feature flag,
or invoked manually from the CLI.
"""

from __future__ import annotations

from typing import Any


def _safe(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def format_portfolio_performance_snapshot(risk_analytics: dict[str, Any]) -> str:
    """Return an HTML snippet summarising portfolio performance.

    The snippet uses the same `<b>` / `<i>` / line-break conventions as
    other briefing sections so it round-trips cleanly through Telegram and
    the HTML email renderer.

    If the risk_analytics block is unavailable, returns an empty string so
    the caller can simply skip this section.
    """
    if not risk_analytics or not risk_analytics.get("available"):
        return ""

    advanced = risk_analytics.get("advanced_metrics") or {}
    if not advanced.get("available"):
        return ""

    bench_name = _safe(risk_analytics.get("benchmark_name"), "Benchmark")
    lookback = _safe(risk_analytics.get("lookback_label"), "1 year")

    total = _safe(risk_analytics.get("total_return_display"))
    bench_total = _safe(risk_analytics.get("benchmark_return_display"))
    active = _safe(risk_analytics.get("active_return_display"))

    sharpe = _safe(risk_analytics.get("sharpe_display"))
    sortino = _safe(risk_analytics.get("sortino_display"))
    info_ratio = _safe(risk_analytics.get("information_ratio_display"))
    vol = _safe(risk_analytics.get("volatility_display"))
    max_dd = _safe(risk_analytics.get("max_drawdown_display"))

    beta = _safe(advanced.get("beta_display"))
    beta_label = _safe(advanced.get("beta_label"))
    r2 = _safe(advanced.get("r_squared_display"))
    alpha = _safe(advanced.get("jensens_alpha_display"))
    alpha_label = _safe(advanced.get("alpha_label"))
    treynor = _safe(advanced.get("treynor_display"))

    prob_loss = _safe(advanced.get("probability_of_loss_display"))
    avg_loss = _safe(advanced.get("average_loss_display"))
    downside = _safe(advanced.get("downside_risk_display"))

    prob_under = _safe(advanced.get("probability_of_underperformance_display"))
    prob_over = _safe(advanced.get("probability_of_outperformance_display"))
    avg_under = _safe(advanced.get("average_underperformance_display"))
    avg_over = _safe(advanced.get("average_outperformance_display"))

    bull_active = _safe(advanced.get("average_bull_active_display"))
    bear_active = _safe(advanced.get("average_bear_active_display"))
    bull_n = advanced.get("bull_months", 0)
    bear_n = advanced.get("bear_months", 0)

    lines: list[str] = []
    lines.append(f"<b>📊 Portfolio Performance ({lookback})</b>")
    lines.append(
        f"Return {total} vs {bench_name} {bench_total} → active {active}."
    )
    lines.append(
        f"Sharpe {sharpe}, Sortino {sortino}, Info Ratio {info_ratio}; "
        f"vol {vol}, max drawdown {max_dd}."
    )
    lines.append(
        f"Beta {beta} ({beta_label}), R² {r2}, alpha {alpha} ({alpha_label}), "
        f"Treynor {treynor}."
    )
    lines.append(
        f"Loss profile: {prob_loss} of months negative, average {avg_loss}; "
        f"downside risk {downside}."
    )
    lines.append(
        f"vs benchmark: outperformed {prob_over} of months "
        f"(avg {avg_over}), underperformed {prob_under} (avg {avg_under})."
    )
    lines.append(
        f"Capture: bull months ({bull_n}) active {bull_active}, "
        f"bear months ({bear_n}) active {bear_active}."
    )
    return "\n".join(lines)


def format_portfolio_performance_plain(risk_analytics: dict[str, Any]) -> str:
    """Plain-text variant for CLI preview and logs."""
    html = format_portfolio_performance_snapshot(risk_analytics)
    if not html:
        return ""
    return (
        html.replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
    )
