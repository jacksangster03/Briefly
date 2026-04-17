"""Benchmark configuration persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import BenchmarkConfig
from app.db.session import get_session

BENCHMARK_TYPES = [
    {"key": "market_index", "label": "Market Index Benchmark"},
    {"key": "policy_blend", "label": "Policy Blend Benchmark"},
    {"key": "custom_blend", "label": "Custom Blend Benchmark"},
]


def benchmark_types() -> list[dict[str, str]]:
    return [dict(item) for item in BENCHMARK_TYPES]


def default_benchmark_config() -> dict[str, Any]:
    return {
        "benchmark_type": "market_index",
        "name": "",
        "base_symbol": "ACWI",
        "components": [],
        "notes": "",
    }


def load_benchmark_config(profile_name: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = (
            session.query(BenchmarkConfig)
            .filter(
                BenchmarkConfig.profile_name == profile_name,
                BenchmarkConfig.active.is_(True),
            )
            .order_by(BenchmarkConfig.updated_at.desc())
            .first()
        )
        if row is None:
            return None
        return _row_to_dict(row)


def save_benchmark_config(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    clean = default_benchmark_config()
    clean.update(payload or {})
    components = clean.get("components") or []
    if isinstance(components, str):
        text = components.strip()
        if text:
            try:
                components = json.loads(text)
            except json.JSONDecodeError:
                components = [{"definition": text}]
        else:
            components = []

    now = datetime.now(timezone.utc)
    with get_session() as session:
        row = (
            session.query(BenchmarkConfig)
            .filter(BenchmarkConfig.profile_name == profile_name)
            .order_by(BenchmarkConfig.updated_at.desc())
            .first()
        )
        if row is None:
            row = BenchmarkConfig(profile_name=profile_name, created_at=now)
            session.add(row)
        row.profile_name = profile_name
        row.benchmark_type = str(clean.get("benchmark_type") or "market_index").strip()
        row.name = _string_or_none(clean.get("name"))
        row.base_symbol = _string_or_none(clean.get("base_symbol"))
        row.components_json = components
        row.notes = _string_or_none(clean.get("notes"))
        row.active = True
        row.updated_at = now
        session.flush()
        return _row_to_dict(row)


def benchmark_summary(config: dict[str, Any] | None) -> dict[str, str]:
    if not config:
        return {
            "type": "unconfigured",
            "name": "No benchmark configured",
            "description": "Choose a market index, policy blend, or custom blend to anchor future relative analytics.",
        }
    benchmark_type = config.get("benchmark_type") or "market_index"
    name = config.get("name") or config.get("base_symbol") or "Custom benchmark"
    if benchmark_type == "policy_blend":
        description = "Policy benchmark using strategic asset-allocation targets as the reference mix."
    elif benchmark_type == "custom_blend":
        description = "Custom benchmark blend defined by user-entered components."
    else:
        description = f"Market index benchmark anchored to {config.get('base_symbol') or 'the chosen index'}."
    return {
        "type": benchmark_type,
        "name": name,
        "description": description,
    }


def _row_to_dict(row: BenchmarkConfig) -> dict[str, Any]:
    return {
        "benchmark_type": row.benchmark_type,
        "name": row.name or "",
        "base_symbol": row.base_symbol or "",
        "components": row.components_json or [],
        "notes": row.notes or "",
    }


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
