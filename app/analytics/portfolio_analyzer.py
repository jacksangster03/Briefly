"""Deterministic portfolio analyzer payloads for the local control center."""

from __future__ import annotations

from collections import Counter
import re
from typing import Any

from app.personalization.user_profile import UserProfile
from app.settings import Settings
from app.universe.sector_universe import load_sector_universe

DISPLAY_ACRONYMS = {
    "ai": "AI",
    "api": "API",
    "cpi": "CPI",
    "ecb": "ECB",
    "etf": "ETF",
    "eu": "EU",
    "fed": "FED",
    "fomc": "FOMC",
    "fx": "FX",
    "gdp": "GDP",
    "ibex": "IBEX",
    "ipo": "IPO",
    "latam": "LATAM",
    "opec": "OPEC",
    "pce": "PCE",
    "ppi": "PPI",
    "sec": "SEC",
    "uk": "UK",
    "us": "US",
    "usa": "USA",
    "uae": "UAE",
}

STATUS_ORDER = {"needs_attention": 0, "mixed": 1, "strong": 2}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SCENARIO_DEFINITIONS = (
    {
        "key": "semis_down_10",
        "label": "Semis Down 10%",
        "description": "AI and semiconductor leadership unwind together.",
        "shocks": {
            "semiconductors": -10.0,
            "technology": -4.0,
            "software_internet": -3.0,
            "communication_services": -2.0,
        },
    },
    {
        "key": "rates_up_50bps",
        "label": "Rates +50 bps",
        "description": "A higher-rate shock hits duration-sensitive leadership.",
        "shocks": {
            "technology": -4.0,
            "software_internet": -4.5,
            "semiconductors": -3.0,
            "real_estate": -6.0,
            "utilities": -4.0,
            "financials": 1.5,
            "consumer_discretionary": -2.0,
        },
    },
    {
        "key": "oil_shock",
        "label": "Oil Shock",
        "description": "Energy spikes while transport and consumer margins compress.",
        "shocks": {
            "energy": 8.0,
            "industrials": -3.0,
            "consumer_discretionary": -4.0,
            "consumer_staples": -2.0,
            "materials": 2.0,
            "financials": -1.0,
        },
    },
    {
        "key": "dollar_spike",
        "label": "Dollar Spike",
        "description": "A stronger USD pressures exporters, cyclicals, and commodity sensitivity.",
        "shocks": {
            "semiconductors": -3.0,
            "technology": -2.0,
            "materials": -3.0,
            "energy": -2.0,
            "healthcare": -1.5,
            "consumer_staples": -1.5,
        },
    },
    {
        "key": "small_cap_risk_off",
        "label": "Small-Cap Risk-Off",
        "description": "Risk appetite fades and cyclicals/smaller-beta exposure lags.",
        "shocks": {
            "consumer_discretionary": -3.0,
            "industrials": -2.5,
            "financials": -2.0,
            "semiconductors": -2.5,
            "technology": -1.5,
        },
    },
)


def display_label(value: str) -> str:
    """Render human labels while preserving finance acronyms in uppercase."""
    raw = str(value or "").strip()
    if not raw:
        return ""

    text = raw.replace("_", " ").replace("-", " ")
    words: list[str] = []
    for token in text.split():
        if "/" in token:
            parts = [display_label(part) for part in token.split("/") if part]
            words.append("/".join(parts))
            continue
        lowered = token.lower()
        if lowered in DISPLAY_ACRONYMS:
            words.append(DISPLAY_ACRONYMS[lowered])
        else:
            words.append(token.capitalize())
    return " ".join(words)


def build_portfolio_analysis(
    *,
    profile: UserProfile,
    settings: Settings,
    metadata: dict[str, Any],
    validations: list[dict[str, str]],
    policy: dict[str, Any] | None = None,
    allocation_targets: list[dict[str, Any]] | None = None,
    actual_allocation: list[dict[str, Any]] | None = None,
    benchmark: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute presentation-ready analyzer metrics for the settings UI/API."""
    universe = load_sector_universe(settings)
    sector_label_by_key = {
        sector.key: (sector.display_name or display_label(sector.key))
        for sector in universe.sectors
    }
    ticker_to_sector: dict[str, str] = {}
    for sector in universe.sectors:
        for ticker in sector.key_names:
            ticker_to_sector[ticker.upper()] = sector.key

    aggregated_positions, duplicate_symbols = _build_symbol_positions(
        profile=profile,
        ticker_to_sector=ticker_to_sector,
        sector_label_by_key=sector_label_by_key,
    )
    weighted_positions = [row for row in aggregated_positions if row["weight_pct"] is not None]
    weighted_values = [float(row["weight_pct"]) for row in weighted_positions]
    total_weight = float(sum(weighted_values)) if weighted_values else None
    gap_to_100 = (100.0 - total_weight) if total_weight is not None else None
    largest_weight = weighted_values[0] if weighted_values else None

    top_positions: list[dict[str, Any]] = []
    for row in aggregated_positions[:8]:
        share_of_total_pct = None
        if row["weight_pct"] is not None and total_weight and total_weight > 0:
            share_of_total_pct = _rounded_metric(row["weight_pct"] / total_weight * 100.0)
        top_positions.append(
            {
                **row,
                "weight_display": _format_percent(row["weight_pct"], digits=2),
                "share_of_total_pct": share_of_total_pct,
                "share_of_total_display": _format_percent(share_of_total_pct),
                "bar_pct": _bar_pct(row["weight_pct"], largest_weight),
            }
        )

    largest_position = top_positions[0] if top_positions else None
    concentration = _build_concentration_rows(weighted_values, total_weight)
    sector_exposure = _build_sector_comparison_rows(profile, sector_label_by_key)
    region_emphasis = _build_region_rows(profile)
    bucket_allocation = _build_bucket_rows(profile)
    analyzer_warnings = _build_analyzer_warning_rows(validations)

    top_sector_row = next(
        (row for row in sector_exposure["rows"] if (row["portfolio_pct"] or 0.0) > 0),
        None,
    )
    top_region_row = next(
        (row for row in region_emphasis["rows"] if (row["normalized_pct"] or 0.0) > 0),
        None,
    )

    data_quality = _build_data_quality(
        profile=profile,
        aggregated_positions=aggregated_positions,
        duplicate_symbols=duplicate_symbols,
        total_weight=total_weight,
    )
    confidence = _build_analyzer_confidence(
        total_weight=total_weight,
        data_quality=data_quality,
    )
    alignment_findings = _build_alignment_findings(
        profile=profile,
        top_positions=top_positions,
        sector_exposure=sector_exposure["rows"],
        region_emphasis=region_emphasis["rows"],
    )
    health_checks = _build_health_checks(
        profile=profile,
        top_positions=top_positions,
        sector_exposure=sector_exposure["rows"],
        region_emphasis=region_emphasis["rows"],
    )
    briefing_influence = _build_briefing_influence(
        profile=profile,
        top_positions=top_positions,
        sector_exposure=sector_exposure["rows"],
        region_emphasis=region_emphasis["rows"],
        next_morning_send_local=metadata["next_morning_send_local"],
    )
    scenario_stress = _build_scenario_stress(
        aggregated_positions=aggregated_positions,
        sector_exposure=sector_exposure["rows"],
    )
    policy_fit = _build_policy_fit(
        top_positions=top_positions,
        policy=policy or {},
        allocation_targets=allocation_targets or [],
        actual_allocation=actual_allocation or [],
    )
    allocation_drift = _build_allocation_drift(
        allocation_targets=allocation_targets or [],
        actual_allocation=actual_allocation or [],
    )

    delivery_enabled = {
        "morning": bool(profile.channels_for("morning")),
        "intraday": bool(profile.channels_for("intraday")),
        "breaking": bool(profile.channels_for("breaking")),
    }
    watchlist_rows = [
        {"label": "Primary", "count": len(profile.watchlist_primary)},
        {"label": "Secondary", "count": len(profile.watchlist_secondary)},
        {"label": "Monitor", "count": len(profile.watchlist_monitor)},
    ]
    max_watch_count = max((row["count"] for row in watchlist_rows), default=0)
    top5_weight = concentration["top5_weight_pct"]
    top5_share = concentration["top5_share_of_total_pct"]

    return {
        "overview": {
            "profile": profile.name,
            "timezone": profile.timezone,
            "home_region_label": display_label(profile.home_region),
            "holdings_count": len(profile.portfolio_holdings),
            "watchlist_total": (
                len(profile.watchlist_primary)
                + len(profile.watchlist_secondary)
                + len(profile.watchlist_monitor)
            ),
            "primary_watchlist_count": len(profile.watchlist_primary),
            "active_override_count": len(profile.preference_overrides),
            "next_morning_send_local": metadata["next_morning_send_local"],
            "last_holdings_update_local": metadata["last_holdings_update_local"],
            "last_preferences_update_local": metadata["last_preferences_update_local"],
        },
        "executive_summary": _build_executive_summary(
            profile=profile,
            holdings_count=len(profile.portfolio_holdings),
            total_weight=total_weight,
            largest_position=largest_position,
            top_sector_label=top_sector_row["label"] if top_sector_row else "Not provided",
            top_region_label=top_region_row["label"] if top_region_row else "Not provided",
            next_morning_send_local=metadata["next_morning_send_local"],
        ),
        "warnings": analyzer_warnings,
        "holdings_totals": {
            "weighted_positions": len([position for position in profile.portfolio_holdings if position.weight_pct is not None]),
            "unweighted_positions": len([position for position in profile.portfolio_holdings if position.weight_pct is None]),
            "total_weight_pct": _rounded_metric(total_weight),
            "total_weight_display": _format_percent(total_weight),
            "gap_to_100_pct": _rounded_metric(gap_to_100),
            "gap_to_100_display": _format_gap(gap_to_100),
            "near_100": bool(total_weight is not None and 95.0 <= total_weight <= 105.0),
            "unallocated_pct": _rounded_metric(max(gap_to_100 or 0.0, 0.0)),
            "unallocated_display": _format_percent(max(gap_to_100 or 0.0, 0.0)),
        },
        "kpis": {
            "holdings_weight_total_pct": _rounded_metric(total_weight),
            "holdings_weight_total_display": _format_percent(total_weight),
            "holdings_weight_gap_pct": _rounded_metric(gap_to_100),
            "holdings_weight_gap_display": _format_gap(gap_to_100),
            "largest_position": largest_position,
            "top_sector": top_sector_row["key"] if top_sector_row else "",
            "top_sector_label": top_sector_row["label"] if top_sector_row else "Not provided",
            "top_region": top_region_row["key"] if top_region_row else "",
            "top_region_label": top_region_row["label"] if top_region_row else "Not provided",
            "top5_concentration_pct": top5_weight,
            "top5_concentration_display": _format_percent(top5_weight),
            "top5_share_of_holdings_pct": top5_share,
            "top5_share_of_holdings_display": _format_percent(top5_share),
            "watchlist_total": (
                len(profile.watchlist_primary)
                + len(profile.watchlist_secondary)
                + len(profile.watchlist_monitor)
            ),
            "delivery_enabled": delivery_enabled,
            "next_morning_send_local": metadata["next_morning_send_local"],
        },
        "top_positions": top_positions,
        "concentration": concentration,
        "sector_exposure": sector_exposure,
        "region_emphasis": region_emphasis,
        "bucket_allocation": bucket_allocation,
        "timing": {
            "next_morning_send_local": metadata["next_morning_send_local"],
            "last_holdings_update_local": metadata["last_holdings_update_local"],
            "last_preferences_update_local": metadata["last_preferences_update_local"],
        },
        "confidence": confidence,
        "alignment_findings": alignment_findings,
        "briefing_influence": briefing_influence,
        "scenario_stress": scenario_stress,
        "policy_fit": policy_fit,
        "allocation_drift": allocation_drift,
        "benchmark_summary": benchmark or {
            "type": "unconfigured",
            "name": "No benchmark configured",
            "description": "Choose a benchmark to anchor future relative analytics.",
        },
        "health_checks": health_checks,
        "data_quality": data_quality,
        "charts": {
            "sector_comparison": sector_exposure["rows"],
            "region_weights": region_emphasis["rows"],
            "watchlist_priority": watchlist_rows,
            "bucket_allocation": bucket_allocation["rows"],
        },
        "chart_max": {
            "sector": sector_exposure["max_scale_pct"],
            "region": region_emphasis["max_scale_pct"],
            "watchlist": float(max_watch_count),
            "bucket": bucket_allocation["max_scale_pct"],
            "positions": largest_weight or 0.0,
            "concentration": concentration["max_scale_pct"],
        },
        "briefing_impact_preview": _build_briefing_impact_preview(
            profile=profile,
            top_positions=top_positions,
            top_sector_label=top_sector_row["label"] if top_sector_row else "Not provided",
            top_region_label=top_region_row["label"] if top_region_row else "Not provided",
            next_morning_send_local=metadata["next_morning_send_local"],
        ),
    }


def _build_symbol_positions(
    *,
    profile: UserProfile,
    ticker_to_sector: dict[str, str],
    sector_label_by_key: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positions_by_symbol: dict[str, list[Any]] = {}
    for position in profile.portfolio_holdings:
        positions_by_symbol.setdefault(position.symbol, []).append(position)

    rows: list[dict[str, Any]] = []
    duplicate_symbols: list[dict[str, Any]] = []
    for symbol, positions in positions_by_symbol.items():
        if len(positions) > 1:
            duplicate_symbols.append({"symbol": symbol, "count": len(positions)})

        weighted_values = [float(position.weight_pct) for position in positions if position.weight_pct is not None]
        total_weight = sum(weighted_values) if weighted_values else None

        bucket_counter = Counter(
            position.bucket for position in positions if getattr(position, "bucket", None)
        )
        bucket = bucket_counter.most_common(1)[0][0] if bucket_counter else None

        sector_key = next(
            (
                position.sector_override
                for position in positions
                if getattr(position, "sector_override", None)
            ),
            None,
        ) or ticker_to_sector.get(symbol)
        sector_label = sector_label_by_key.get(sector_key, display_label(sector_key)) if sector_key else "Not provided"
        watchlist_role = _watchlist_role(symbol, profile)

        rows.append(
            {
                "symbol": symbol,
                "weight_pct": _rounded_metric(total_weight, digits=2) if total_weight is not None else None,
                "bucket": bucket,
                "bucket_label": _display_or_not_provided(display_label(bucket) if bucket else ""),
                "sector": sector_key,
                "sector_label": _display_or_not_provided(sector_label),
                "watchlist_role": watchlist_role,
                "holding_rows": len(positions),
                "unweighted_rows": len([position for position in positions if position.weight_pct is None]),
            }
        )

    rows.sort(
        key=lambda row: (
            row["weight_pct"] is None,
            -(row["weight_pct"] or 0.0),
            row["symbol"],
        )
    )
    duplicate_symbols.sort(key=lambda row: (-row["count"], row["symbol"]))
    return rows, duplicate_symbols


def _build_sector_comparison_rows(
    profile: UserProfile,
    sector_label_by_key: dict[str, str],
) -> dict[str, Any]:
    coverage_total = sum(float(weight) for weight in profile.sector_weights.values() if float(weight) > 0)
    keys = set(profile.portfolio_sector_weights.keys()) | set(profile.sector_weights.keys())
    rows: list[dict[str, Any]] = []
    for key in sorted(keys):
        portfolio_pct = float(profile.portfolio_sector_weights.get(key, 0.0) * 100.0)
        coverage_weight_raw = float(profile.sector_weights.get(key, 0.0))
        coverage_pct = (coverage_weight_raw / coverage_total * 100.0) if coverage_total > 0 else 0.0
        rows.append(
            {
                "key": key,
                "label": sector_label_by_key.get(key, display_label(key)),
                "portfolio_pct": _rounded_metric(portfolio_pct),
                "coverage_pct": _rounded_metric(coverage_pct),
                "coverage_weight": _rounded_metric(coverage_weight_raw, digits=2),
                "portfolio_display": _format_percent(portfolio_pct),
                "coverage_display": _format_percent(coverage_pct),
            }
        )
    rows.sort(key=lambda row: max(row["portfolio_pct"] or 0.0, row["coverage_pct"] or 0.0), reverse=True)
    trimmed = rows[:8]
    max_scale = max(
        (max(row["portfolio_pct"] or 0.0, row["coverage_pct"] or 0.0) for row in trimmed),
        default=0.0,
    )
    for row in trimmed:
        row["portfolio_bar_pct"] = _bar_pct(row["portfolio_pct"], max_scale)
        row["coverage_bar_pct"] = _bar_pct(row["coverage_pct"], max_scale)
        row["gap_pct"] = _rounded_metric((row["portfolio_pct"] or 0.0) - (row["coverage_pct"] or 0.0))
    return {"rows": trimmed, "max_scale_pct": _rounded_metric(max_scale) or 0.0}


def _build_region_rows(profile: UserProfile) -> dict[str, Any]:
    positive_total = sum(float(value) for value in profile.coverage_weights.values() if float(value) > 0)
    rows = [
        {
            "key": key,
            "label": display_label(key),
            "value": _rounded_metric(float(value), digits=2),
            "normalized_pct": _rounded_metric((float(value) / positive_total * 100.0) if positive_total > 0 else 0.0),
        }
        for key, value in profile.coverage_weights.items()
    ]
    rows.sort(key=lambda row: (row["value"] or 0.0, row["label"]), reverse=True)
    trimmed = rows[:8]
    max_scale = max((row["normalized_pct"] or 0.0 for row in trimmed), default=0.0)
    for row in trimmed:
        row["display"] = _format_percent(row["normalized_pct"])
        row["bar_pct"] = _bar_pct(row["normalized_pct"], max_scale)
    return {"rows": trimmed, "max_scale_pct": _rounded_metric(max_scale) or 0.0}


def _build_bucket_rows(profile: UserProfile) -> dict[str, Any]:
    bucket_totals: dict[str, float] = {}
    bucket_counts: dict[str, int] = {}
    unweighted_count = 0
    for position in profile.portfolio_holdings:
        bucket = (position.bucket or "unlabeled").strip().lower()
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if position.weight_pct is None:
            unweighted_count += 1
            continue
        bucket_totals[bucket] = bucket_totals.get(bucket, 0.0) + float(position.weight_pct)
    max_value = max(bucket_totals.values(), default=0.0)
    rows = [
        {
            "key": bucket,
            "label": display_label(bucket),
            "value": _rounded_metric(value),
            "display": _format_percent(value),
            "holdings_count": bucket_counts.get(bucket, 0),
            "bar_pct": _bar_pct(value, max_value),
        }
        for bucket, value in bucket_totals.items()
    ]
    rows.sort(key=lambda row: ((row["value"] or 0.0), row["holdings_count"]), reverse=True)
    return {
        "rows": rows[:6],
        "unweighted_positions": unweighted_count,
        "max_scale_pct": _rounded_metric(max_value) or 0.0,
    }


def _build_concentration_rows(weights_sorted: list[float], total_weight: float | None) -> dict[str, Any]:
    def _sum_slice(limit: int) -> float | None:
        if not weights_sorted:
            return None
        return float(sum(weights_sorted[:limit]))

    top1_weight = _sum_slice(1)
    top3_weight = _sum_slice(3)
    top5_weight = _sum_slice(5)
    tail_weight = None
    if total_weight is not None and top5_weight is not None:
        tail_weight = max(total_weight - top5_weight, 0.0)

    rows = [
        {"key": "top1", "label": "Top 1", "value_pct": _rounded_metric(top1_weight)},
        {"key": "top3", "label": "Top 3", "value_pct": _rounded_metric(top3_weight)},
        {"key": "top5", "label": "Top 5", "value_pct": _rounded_metric(top5_weight)},
        {"key": "tail", "label": "Residual Tail", "value_pct": _rounded_metric(tail_weight)},
    ]
    max_scale = max((row["value_pct"] or 0.0 for row in rows), default=0.0)
    for row in rows:
        share = None
        if row["value_pct"] is not None and total_weight and total_weight > 0:
            share = row["value_pct"] / total_weight * 100.0
        row["share_of_total_pct"] = _rounded_metric(share)
        row["display"] = _format_percent(row["value_pct"])
        row["bar_pct"] = _bar_pct(row["value_pct"], max_scale)

    return {
        "rows": rows,
        "top1_weight_pct": _rounded_metric(top1_weight),
        "top3_weight_pct": _rounded_metric(top3_weight),
        "top5_weight_pct": _rounded_metric(top5_weight),
        "tail_weight_pct": _rounded_metric(tail_weight),
        "top1_share_of_total_pct": rows[0]["share_of_total_pct"],
        "top3_share_of_total_pct": rows[1]["share_of_total_pct"],
        "top5_share_of_total_pct": rows[2]["share_of_total_pct"],
        "tail_share_of_total_pct": rows[3]["share_of_total_pct"],
        "max_scale_pct": _rounded_metric(max_scale) or 0.0,
    }


def _build_alignment_findings(
    *,
    profile: UserProfile,
    top_positions: list[dict[str, Any]],
    sector_exposure: list[dict[str, Any]],
    region_emphasis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for row in sector_exposure:
        portfolio_pct = row["portfolio_pct"] or 0.0
        coverage_pct = row["coverage_pct"] or 0.0
        gap_pct = portfolio_pct - coverage_pct
        if portfolio_pct >= 15.0 and gap_pct >= 10.0:
            severity = "high" if gap_pct >= 20.0 else "medium"
            findings.append(
                {
                    "code": "undercovered_sector",
                    "severity": severity,
                    "title": f"{row['label']} is under-covered",
                    "message": (
                        f"{row['label']} is {row['portfolio_display']} of weighted holdings but only "
                        f"{row['coverage_display']} of editorial coverage share."
                    ),
                    "sort_value": -gap_pct,
                }
            )
        elif coverage_pct >= 15.0 and gap_pct <= -10.0:
            findings.append(
                {
                    "code": "overcovered_sector",
                    "severity": "medium" if gap_pct <= -20.0 else "low",
                    "title": f"{row['label']} is over-covered",
                    "message": (
                        f"{row['label']} is only {row['portfolio_display']} of weighted holdings but "
                        f"receives {row['coverage_display']} of editorial coverage share."
                    ),
                    "sort_value": gap_pct,
                }
            )

    for row in top_positions[:3]:
        if row["weight_pct"] is None or row["weight_pct"] < 5.0:
            continue
        if row["watchlist_role"] in {"primary", "secondary"}:
            continue
        findings.append(
            {
                "code": "weak_watchlist_support",
                "severity": "high" if row["weight_pct"] >= 8.0 else "medium",
                "title": f"{row['symbol']} lacks watchlist support",
                "message": (
                    f"{row['symbol']} is {row['weight_display']} of weighted holdings but is not in the "
                    "primary or secondary watchlists."
                ),
                "sort_value": -(row["weight_pct"] or 0.0),
            }
        )

    home_region = profile.home_region
    home_weight = float(profile.coverage_weights.get(home_region, 0.0))
    top_region = region_emphasis[0] if region_emphasis else None
    max_region_value = top_region["value"] if top_region else 0.0
    if top_region and top_region["key"] != home_region and home_weight <= max_region_value * 0.35:
        findings.append(
            {
                "code": "home_region_underweighted",
                "severity": "medium",
                "title": "Home region is underweighted",
                "message": (
                    f"Home region is {display_label(home_region)}, but editorial emphasis currently leads with "
                    f"{top_region['label']}."
                ),
                "sort_value": -(max_region_value - home_weight),
            }
        )

    if top_positions and not profile.morning_section_enabled("portfolio_focus"):
        findings.append(
            {
                "code": "portfolio_focus_disabled",
                "severity": "medium",
                "title": "Portfolio Focus is disabled",
                "message": (
                    "Weighted holdings are configured, but the morning Portfolio Focus section is currently disabled."
                ),
                "sort_value": 0.0,
            }
        )

    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9), item.get("sort_value", 0.0)))
    trimmed = findings[:6]
    for item in trimmed:
        item.pop("sort_value", None)
    return trimmed


def _build_health_checks(
    *,
    profile: UserProfile,
    top_positions: list[dict[str, Any]],
    sector_exposure: list[dict[str, Any]],
    region_emphasis: list[dict[str, Any]],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    max_sector_gap = max((abs(row["gap_pct"] or 0.0) for row in sector_exposure), default=0.0)
    coverage_status = "strong"
    coverage_message = "Editorial sector weights broadly match portfolio exposure."
    if max_sector_gap > 20.0:
        coverage_status = "needs_attention"
        coverage_message = "At least one sector is materially misaligned versus portfolio exposure."
    elif max_sector_gap > 10.0:
        coverage_status = "mixed"
        coverage_message = "Some sectors are meaningfully misaligned versus portfolio exposure."
    checks.append(
        {
            "key": "coverage_fit",
            "label": "Portfolio Coverage Fit",
            "status": coverage_status,
            "message": coverage_message,
        }
    )

    covered = [row for row in top_positions[:3] if row["watchlist_role"] in {"primary", "secondary"}]
    top_count = len(top_positions[:3])
    if top_count == 0:
        watchlist_status = "mixed"
        watchlist_message = "No weighted holdings are available to test against the watchlists yet."
    elif len(covered) == top_count:
        watchlist_status = "strong"
        watchlist_message = "Top weighted holdings are represented in the primary/secondary watchlists."
    elif len(covered) >= 2:
        watchlist_status = "mixed"
        watchlist_message = "Most top weighted holdings are represented, but one is not strongly supported."
    else:
        watchlist_status = "needs_attention"
        watchlist_message = "Top weighted holdings are weakly represented in the primary/secondary watchlists."
    checks.append(
        {
            "key": "watchlist_support",
            "label": "Watchlist Support",
            "status": watchlist_status,
            "message": watchlist_message,
        }
    )

    positive_region_total = sum(float(value) for value in profile.coverage_weights.values() if float(value) > 0)
    home_weight = float(profile.coverage_weights.get(profile.home_region, 0.0))
    top_region = region_emphasis[0] if region_emphasis else None
    if positive_region_total <= 0:
        region_status = "needs_attention"
        region_message = "Region emphasis is effectively empty, so macro coverage may feel random."
    elif top_region and top_region["key"] == profile.home_region:
        region_status = "strong"
        region_message = f"Regional emphasis leads with {top_region['label']}, matching the home-region focus."
    elif home_weight > 0:
        region_status = "mixed"
        region_message = (
            f"Home region {display_label(profile.home_region)} is represented, but another region currently leads."
        )
    else:
        region_status = "needs_attention"
        region_message = f"Home region {display_label(profile.home_region)} has no active editorial emphasis."
    checks.append(
        {
            "key": "region_fit",
            "label": "Region Fit",
            "status": region_status,
            "message": region_message,
        }
    )

    morning_channels = profile.channels_for("morning")
    intraday_channels = profile.channels_for("intraday")
    breaking_channels = profile.channels_for("breaking")
    if morning_channels and intraday_channels and breaking_channels == ["telegram"]:
        delivery_status = "strong"
        delivery_message = "Morning, intraday, and breaking routing are configured in the recommended pattern."
    elif morning_channels and (intraday_channels or breaking_channels):
        delivery_status = "mixed"
        delivery_message = "Core delivery is configured, but routing is not yet in the recommended pattern."
    else:
        delivery_status = "needs_attention"
        delivery_message = "One or more briefing types are missing delivery channels."
    checks.append(
        {
            "key": "delivery_readiness",
            "label": "Delivery Readiness",
            "status": delivery_status,
            "message": delivery_message,
        }
    )

    checks.sort(key=lambda item: (STATUS_ORDER.get(item["status"], 9), item["label"]))
    return checks


def _build_policy_fit(
    *,
    top_positions: list[dict[str, Any]],
    policy: dict[str, Any],
    allocation_targets: list[dict[str, Any]],
    actual_allocation: list[dict[str, Any]],
) -> dict[str, Any]:
    breaches: list[dict[str, Any]] = []

    single_name_limit = _float_or_none(policy.get("single_name_limit_percent"))
    if single_name_limit is not None and top_positions:
        largest = top_positions[0]
        largest_weight = _float_or_none(largest.get("weight_pct"))
        if largest_weight is not None and largest_weight > single_name_limit:
            breaches.append(
                {
                    "code": "single_name_limit_breach",
                    "severity": "high",
                    "title": "Single-name limit exceeded",
                    "message": (
                        f"{largest['symbol']} is {_format_percent(largest_weight, digits=2)} versus a "
                        f"{_format_percent(single_name_limit, digits=1)} policy cap."
                    ),
                }
            )

    actual_by_class = {
        str(row.get("asset_class") or ""): _float_or_none(row.get("actual_pct")) or 0.0
        for row in actual_allocation
    }
    max_equity = _float_or_none(policy.get("max_equity_percent"))
    actual_equity = actual_by_class.get("equities", 0.0)
    if max_equity is not None and actual_equity > max_equity:
        breaches.append(
            {
                "code": "equity_max_breach",
                "severity": "high",
                "title": "Equity allocation above policy max",
                "message": (
                    f"Equities are {_format_percent(actual_equity, digits=1)} versus a "
                    f"{_format_percent(max_equity, digits=1)} maximum."
                ),
            }
        )

    min_liquid = _float_or_none(policy.get("min_liquid_assets_percent"))
    actual_liquid = actual_by_class.get("cash_liquidity", 0.0)
    if min_liquid is not None and actual_liquid < min_liquid:
        breaches.append(
            {
                "code": "liquidity_min_breach",
                "severity": "medium",
                "title": "Liquidity sleeve below minimum",
                "message": (
                    f"Cash / liquidity is {_format_percent(actual_liquid, digits=1)} versus a "
                    f"{_format_percent(min_liquid, digits=1)} minimum."
                ),
            }
        )

    for target in allocation_targets:
        asset_class = str(target.get("asset_class") or "")
        label = str(target.get("label") or display_label(asset_class))
        actual_pct = actual_by_class.get(asset_class, 0.0)
        min_pct = _float_or_none(target.get("min_weight_pct"))
        max_pct = _float_or_none(target.get("max_weight_pct"))
        if min_pct is not None and actual_pct < min_pct:
            breaches.append(
                {
                    "code": "allocation_band_breach",
                    "severity": "medium",
                    "title": f"{label} below policy band",
                    "message": (
                        f"{label} is {_format_percent(actual_pct, digits=1)} versus a "
                        f"{_format_percent(min_pct, digits=1)} minimum."
                    ),
                }
            )
        elif max_pct is not None and actual_pct > max_pct:
            breaches.append(
                {
                    "code": "allocation_band_breach",
                    "severity": "medium",
                    "title": f"{label} above policy band",
                    "message": (
                        f"{label} is {_format_percent(actual_pct, digits=1)} versus a "
                        f"{_format_percent(max_pct, digits=1)} maximum."
                    ),
                }
            )

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    breaches.sort(key=lambda item: (severity_rank.get(item["severity"], 9), item["title"]))
    trimmed = breaches[:6]
    if not trimmed:
        return {
            "status": "strong",
            "summary": "Current holdings sit inside the configured policy constraints.",
            "breaches": [],
        }
    status = "needs_attention" if any(item["severity"] == "high" for item in trimmed) else "mixed"
    return {
        "status": status,
        "summary": (
            f"{len(trimmed)} policy breach(es) need attention."
            if status == "needs_attention"
            else f"{len(trimmed)} policy drift item(s) are outside preferred ranges."
        ),
        "breaches": trimmed,
    }


def _build_allocation_drift(
    *,
    allocation_targets: list[dict[str, Any]],
    actual_allocation: list[dict[str, Any]],
) -> dict[str, Any]:
    actual_by_class = {
        str(row.get("asset_class") or ""): row for row in actual_allocation
    }
    rows: list[dict[str, Any]] = []
    breach_count = 0

    for target in allocation_targets:
        asset_class = str(target.get("asset_class") or "")
        actual_row = actual_by_class.get(asset_class, {})
        target_pct = _float_or_none(target.get("target_weight_pct"))
        actual_pct = _float_or_none(actual_row.get("actual_pct"))
        min_pct = _float_or_none(target.get("min_weight_pct"))
        max_pct = _float_or_none(target.get("max_weight_pct"))
        drift_pct = None
        if target_pct is not None and actual_pct is not None:
            drift_pct = round(actual_pct - target_pct, 2)

        status = "unconfigured"
        if actual_pct is not None:
            status = "within_band"
            if min_pct is not None and actual_pct < min_pct:
                status = "below_band"
            elif max_pct is not None and actual_pct > max_pct:
                status = "above_band"
        if status in {"below_band", "above_band"}:
            breach_count += 1

        if status == "below_band":
            rebalance_message = f"Below band by {_format_percent((min_pct or 0.0) - (actual_pct or 0.0), digits=1)}."
        elif status == "above_band":
            rebalance_message = f"Above band by {_format_percent((actual_pct or 0.0) - (max_pct or 0.0), digits=1)}."
        elif target_pct is not None and actual_pct is not None and abs(actual_pct - target_pct) >= 1.0:
            rebalance_message = f"Drift versus target is {_format_signed_percent(actual_pct - target_pct, digits=1)}."
        else:
            rebalance_message = "Within configured band."

        rows.append(
            {
                "asset_class": asset_class,
                "label": target.get("label") or display_label(asset_class),
                "role": target.get("role") or "Not provided",
                "target_pct": _rounded_metric(target_pct),
                "actual_pct": _rounded_metric(actual_pct),
                "min_pct": _rounded_metric(min_pct),
                "max_pct": _rounded_metric(max_pct),
                "drift_pct": _rounded_metric(drift_pct),
                "target_display": _format_percent(target_pct),
                "actual_display": _format_percent(actual_pct),
                "min_display": _format_percent(min_pct),
                "max_display": _format_percent(max_pct),
                "drift_display": _format_signed_percent(drift_pct, digits=1) if drift_pct is not None else "Not provided",
                "status": status,
                "rebalance_message": rebalance_message,
            }
        )

    rows.sort(
        key=lambda row: (
            0 if row["status"] in {"above_band", "below_band"} else 1,
            -(abs(row["drift_pct"] or 0.0)),
            row["label"],
        )
    )
    if not rows:
        return {
            "rows": [],
            "summary": "Allocation targets are not configured yet.",
        }
    if breach_count:
        summary = f"{breach_count} asset-class allocation band(s) currently sit outside policy ranges."
    else:
        summary = "Current allocation is within the configured strategic bands."
    return {"rows": rows, "summary": summary}


def _build_data_quality(
    *,
    profile: UserProfile,
    aggregated_positions: list[dict[str, Any]],
    duplicate_symbols: list[dict[str, Any]],
    total_weight: float | None,
) -> dict[str, Any]:
    weighted_count = len([position for position in profile.portfolio_holdings if position.weight_pct is not None])
    unweighted_count = len(profile.portfolio_holdings) - weighted_count
    unlabeled_buckets = len([position for position in profile.portfolio_holdings if not position.bucket])
    inferred_sector_gaps = sorted(
        row["symbol"] for row in aggregated_positions if row["sector_label"] == "Not provided"
    )

    issues: list[dict[str, str]] = []
    if unweighted_count:
        issues.append(
            {
                "code": "missing_weights",
                "title": "Missing weights",
                "message": f"{unweighted_count} holding row(s) are missing weights.",
            }
        )
    if total_weight is not None and not (95.0 <= total_weight <= 105.0):
        issues.append(
            {
                "code": "weight_sum_gap",
                "title": "Weights do not normalize to 100%",
                "message": f"Weighted holdings currently sum to {_format_percent(total_weight)}.",
            }
        )
    if duplicate_symbols:
        duplicate_text = ", ".join(f"{item['symbol']} x{item['count']}" for item in duplicate_symbols[:4])
        issues.append(
            {
                "code": "duplicate_symbols",
                "title": "Duplicate symbols detected",
                "message": f"Multiple rows exist for {duplicate_text}.",
            }
        )
    if unlabeled_buckets:
        issues.append(
            {
                "code": "unlabeled_buckets",
                "title": "Missing bucket labels",
                "message": f"{unlabeled_buckets} holding row(s) do not have a bucket label.",
            }
        )
    if inferred_sector_gaps:
        issues.append(
            {
                "code": "sector_inference_gaps",
                "title": "Sector inference gaps",
                "message": f"Sectors could not be inferred for {', '.join(inferred_sector_gaps[:4])}.",
            }
        )

    return {
        "issues": issues,
        "weighted_count": weighted_count,
        "unweighted_count": unweighted_count,
        "duplicate_symbols": duplicate_symbols,
        "unlabeled_buckets": unlabeled_buckets,
        "inferred_sector_gaps": inferred_sector_gaps,
        "total_weight_pct": _rounded_metric(total_weight),
    }


def _build_analyzer_confidence(
    *,
    total_weight: float | None,
    data_quality: dict[str, Any],
) -> dict[str, str]:
    issue_count = len(data_quality.get("issues", []))
    if total_weight is None or total_weight < 60.0:
        return {
            "status": "low",
            "label": "Low confidence",
            "message": "Weighted holdings are still too incomplete for full-confidence portfolio analysis.",
        }
    if issue_count >= 3 or not (95.0 <= total_weight <= 105.0):
        return {
            "status": "partial",
            "label": "Partial confidence",
            "message": "Core signals are usable, but data quality or normalization issues still affect precision.",
        }
    return {
        "status": "high",
        "label": "High confidence",
        "message": "Holdings are well-formed enough for the analyzer to give a strong deterministic read.",
    }


def _build_scenario_stress(
    *,
    aggregated_positions: list[dict[str, Any]],
    sector_exposure: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic scenario stress tests from current sector weights."""
    sector_label_by_key = {row["key"]: row["label"] for row in sector_exposure}
    sector_weight_by_key = {
        row["key"]: float(row["portfolio_pct"] or 0.0) for row in sector_exposure if row["key"]
    }
    scenarios: list[dict[str, Any]] = []

    for definition in SCENARIO_DEFINITIONS:
        normalized_shocks = {_normalized_key(key): float(value) for key, value in definition["shocks"].items()}
        holding_rows: list[dict[str, Any]] = []
        for row in aggregated_positions:
            if row["weight_pct"] is None or not row.get("sector"):
                continue
            shock_pct = normalized_shocks.get(_normalized_key(row["sector"]), 0.0)
            contribution_pct = float(row["weight_pct"]) * shock_pct / 100.0
            if abs(contribution_pct) < 0.01:
                continue
            holding_rows.append(
                {
                    "symbol": row["symbol"],
                    "sector_label": row["sector_label"],
                    "weight_display": row["weight_display"] if "weight_display" in row else _format_percent(row["weight_pct"], digits=2),
                    "contribution_pct": _rounded_metric(contribution_pct, digits=2) or 0.0,
                    "contribution_display": _format_signed_percent(contribution_pct, digits=2),
                }
            )

        sector_rows: list[dict[str, Any]] = []
        for sector_key, shock_pct in normalized_shocks.items():
            matched_key = next(
                (key for key in sector_weight_by_key if _normalized_key(key) == sector_key),
                None,
            )
            if not matched_key:
                continue
            portfolio_pct = sector_weight_by_key.get(matched_key, 0.0)
            contribution_pct = portfolio_pct * shock_pct / 100.0
            if abs(contribution_pct) < 0.01:
                continue
            sector_rows.append(
                {
                    "key": matched_key,
                    "label": sector_label_by_key.get(matched_key, display_label(matched_key)),
                    "portfolio_pct": _rounded_metric(portfolio_pct) or 0.0,
                    "portfolio_display": _format_percent(portfolio_pct),
                    "shock_pct": _rounded_metric(shock_pct, digits=1) or 0.0,
                    "shock_display": _format_signed_percent(shock_pct, digits=1),
                    "estimated_contribution_pct": _rounded_metric(contribution_pct, digits=2) or 0.0,
                    "estimated_contribution_display": _format_signed_percent(contribution_pct, digits=2),
                }
            )

        holding_rows.sort(key=lambda item: abs(item["contribution_pct"]), reverse=True)
        sector_rows.sort(key=lambda item: abs(item["estimated_contribution_pct"]), reverse=True)
        estimated_impact = float(sum(item["contribution_pct"] for item in holding_rows))

        top_holdings = holding_rows[:3]
        top_sectors = sector_rows[:3]
        if not top_holdings and not top_sectors:
            scenario_summary = "No mapped holdings currently participate in this scenario."
        elif estimated_impact <= -0.25:
            scenario_summary = (
                f"{definition['label']} would likely pressure "
                f"{', '.join(item['symbol'] for item in top_holdings[:2]) or 'the mapped book'} first."
            )
        elif estimated_impact >= 0.25:
            scenario_summary = (
                f"{definition['label']} would likely help "
                f"{', '.join(item['symbol'] for item in top_holdings[:2]) or 'the mapped book'} on current weights."
            )
        else:
            scenario_summary = (
                f"{definition['label']} looks relatively balanced against the current portfolio mix."
            )

        scenarios.append(
            {
                "key": definition["key"],
                "label": definition["label"],
                "description": definition["description"],
                "estimated_portfolio_impact_pct": _rounded_metric(estimated_impact, digits=2) or 0.0,
                "estimated_portfolio_impact_display": _format_signed_percent(estimated_impact, digits=2),
                "impact_class": _impact_class(estimated_impact),
                "summary": scenario_summary,
                "top_holdings": top_holdings,
                "top_sectors": top_sectors,
            }
        )

    has_mapped_exposure = any(item["top_holdings"] or item["top_sectors"] for item in scenarios)
    worst_drawdown = min(scenarios, key=lambda item: item["estimated_portfolio_impact_pct"], default=None)
    summary = (
        "Scenario stress tests will populate once weighted holdings are mapped into sectors."
        if not has_mapped_exposure
        else (
            f"Current portfolio looks most exposed to {worst_drawdown['label']} "
            f"({worst_drawdown['estimated_portfolio_impact_display']}) under the static sector-shock assumptions."
            if worst_drawdown is not None
            else "Scenario stress tests will appear once weighted holdings are available."
        )
    )

    return {
        "methodology": (
            "Static sector-shock test using current holdings weights and inferred sector mapping. "
            "Useful for sensitivity checks, not forecasting."
        ),
        "summary": summary,
        "scenarios": scenarios,
    }


def _build_briefing_influence(
    *,
    profile: UserProfile,
    top_positions: list[dict[str, Any]],
    sector_exposure: list[dict[str, Any]],
    region_emphasis: list[dict[str, Any]],
    next_morning_send_local: str,
) -> dict[str, Any]:
    ranked_holdings = sorted(
        top_positions,
        key=lambda row: (
            -((row["weight_pct"] or 0.0) + (2.5 if row["watchlist_role"] == "primary" else 1.0 if row["watchlist_role"] == "secondary" else 0.0)),
            row["symbol"],
        ),
    )
    top_holdings = [
        {
            "symbol": row["symbol"],
            "weight_display": row["weight_display"],
            "watchlist_role": row["watchlist_role"],
            "sector_label": row["sector_label"],
        }
        for row in ranked_holdings[:3]
    ]

    ranked_sectors = sorted(
        sector_exposure,
        key=lambda row: -(((row["portfolio_pct"] or 0.0) * 0.7) + ((row["coverage_pct"] or 0.0) * 0.3)),
    )
    top_sectors = [
        {
            "label": row["label"],
            "portfolio_display": row["portfolio_display"],
            "coverage_display": row["coverage_display"],
        }
        for row in ranked_sectors[:3]
    ]

    lead_region = region_emphasis[0] if region_emphasis else {
        "key": profile.home_region,
        "label": display_label(profile.home_region),
        "display": "Not provided",
    }

    section_drivers = {
        "portfolio_focus": (
            "Portfolio Focus will lean on "
            + ", ".join(item["symbol"] for item in top_holdings[:2])
            + "."
            if profile.morning_section_enabled("portfolio_focus") and top_holdings
            else "Portfolio Focus is currently disabled or lacks weighted holdings."
        ),
        "sector_scan": (
            "Sector Scan is most likely to emphasize "
            + ", ".join(item["label"] for item in top_sectors[:2])
            + "."
            if profile.morning_section_enabled("sector_scan") and top_sectors
            else "Sector Scan is currently disabled or lacks sector overlap."
        ),
        "global_news": (
            f"Global context will lean on {lead_region['label']}."
            if profile.morning_section_enabled("global_news")
            else "Global News & Geopolitics is currently disabled."
        ),
    }

    holdings_text = ", ".join(item["symbol"] for item in top_holdings[:2]) or "direct holdings"
    sector_text = ", ".join(item["label"] for item in top_sectors[:2]) or "broad sector coverage"
    summary = (
        f"If nothing changes, the next brief at {next_morning_send_local} will lean on "
        f"{lead_region['label']} context, direct holdings like {holdings_text}, and sectors such as {sector_text}."
    )

    return {
        "top_holdings": top_holdings,
        "top_sectors": top_sectors,
        "lead_region": {
            "key": lead_region["key"],
            "label": lead_region["label"],
            "display": lead_region["display"],
        },
        "section_drivers": section_drivers,
        "summary": summary,
    }


def _build_analyzer_warning_rows(validations: list[dict[str, str]]) -> list[dict[str, str]]:
    warning_titles = {
        "holdings_weight_sum": "Holdings Weight Check",
        "watchlist_primary_empty": "Primary Watchlist Empty",
        "region_weights_empty": "Region Emphasis Missing",
    }
    analyzer_codes = {"holdings_weight_sum", "watchlist_primary_empty", "region_weights_empty"}
    warnings: list[dict[str, str]] = []
    for item in validations:
        code = item.get("code", "")
        if code.startswith("delivery_missing_"):
            title = "Delivery Channel Missing"
        elif code in analyzer_codes:
            title = warning_titles.get(code, "Analyzer Warning")
        else:
            continue
        warnings.append({"code": code, "title": title, "message": item.get("message", "")})
    return warnings


def _build_briefing_impact_preview(
    *,
    profile: UserProfile,
    top_positions: list[dict[str, Any]],
    top_sector_label: str,
    top_region_label: str,
    next_morning_send_local: str,
) -> str:
    focus_positions = [
        f"{row['symbol']} ({row['weight_display']})"
        for row in top_positions[:2]
        if row.get("weight_pct") is not None
    ]
    focus_text = (
        f"Direct portfolio attention will skew toward {', '.join(focus_positions)}."
        if focus_positions
        else "No weighted holdings are configured yet, so direct holding emphasis is limited."
    )
    sector_text = top_sector_label if top_sector_label != "Not provided" else "balanced sector coverage"
    region_text = top_region_label if top_region_label != "Not provided" else display_label(profile.home_region)
    primary_count = len(profile.watchlist_primary)
    morning_channels = ", ".join(profile.channels_for("morning")) or "no enabled morning channels"
    portfolio_focus = (
        "Portfolio focus remains enabled in the morning brief."
        if profile.morning_section_enabled("portfolio_focus")
        else "Portfolio focus is disabled in the morning brief."
    )
    return (
        f"Tomorrow's brief is set to lead with {region_text} context and {sector_text} exposure. "
        f"{focus_text} Primary watchlist has {primary_count} symbol(s). "
        f"Next scheduled morning send is {next_morning_send_local} via {morning_channels}. "
        f"{portfolio_focus}"
    )


def _build_executive_summary(
    *,
    profile: UserProfile,
    holdings_count: int,
    total_weight: float | None,
    largest_position: dict[str, Any] | None,
    top_sector_label: str,
    top_region_label: str,
    next_morning_send_local: str,
) -> str:
    largest_text = "No weighted lead position is configured yet"
    if largest_position and largest_position.get("weight_display") != "Not provided":
        largest_text = f"{largest_position['symbol']} is the anchor holding at {largest_position['weight_display']}"

    sector_text = top_sector_label if top_sector_label != "Not provided" else "balanced sector exposure"
    region_text = top_region_label if top_region_label != "Not provided" else display_label(profile.home_region)
    total_weight_text = _format_percent(total_weight) if total_weight is not None else "Not provided"
    return (
        f"{holdings_count} active holding(s), {total_weight_text} weighted in total. "
        f"{largest_text}. Sector tilt is strongest in {sector_text}, while editorial emphasis leads with "
        f"{region_text}. Next local morning brief is due at {next_morning_send_local}."
    )


def _watchlist_role(symbol: str, profile: UserProfile) -> str:
    if symbol in set(profile.watchlist_primary):
        return "primary"
    if symbol in set(profile.watchlist_secondary):
        return "secondary"
    if symbol in set(profile.watchlist_monitor):
        return "monitor"
    return "none"


def _rounded_metric(value: float | None, *, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _float_or_none(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_percent(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "Not provided"
    return f"{float(value):.{digits}f}%"


def _format_signed_percent(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "Not provided"
    return f"{float(value):+.{digits}f}%"


def _format_gap(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return "Not provided"
    if abs(float(value)) < 0.05:
        return "0.0% vs 100%"
    direction = "below" if value > 0 else "above"
    return f"{abs(float(value)):.{digits}f}% {direction} 100%"


def _display_or_not_provided(value: str | None) -> str:
    text = str(value or "").strip()
    return text or "Not provided"


def _bar_pct(value: float | None, max_value: float | None) -> float:
    if value is None or not max_value or max_value <= 0:
        return 0.0
    return round(max(0.0, min(float(value) / float(max_value) * 100.0, 100.0)), 1)


def _impact_class(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value <= -0.25:
        return "negative"
    if value >= 0.25:
        return "positive"
    return "neutral"


def _normalized_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")
