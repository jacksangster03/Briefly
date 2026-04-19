"""Persistence repository for simulation runs and presets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import SimulationPreset, SimulationResult, SimulationRun
from app.db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_simulation_run(
    *,
    profile_name: str,
    config: dict[str, Any],
    summary: dict[str, Any],
    charts: dict[str, Any],
    metrics: dict[str, Any],
    scenarios: list[dict[str, Any]],
    status: str = "completed",
) -> dict[str, Any]:
    now = _utcnow()
    with get_session() as session:
        run = SimulationRun(
            profile_name=profile_name,
            name=str(config.get("name") or "").strip() or None,
            mode=str(config.get("mode") or "portfolio"),
            methods_json=list(config.get("methods") or []),
            frequency=str(config.get("frequency") or "monthly"),
            horizon_periods=int(config.get("horizon_periods") or 12),
            simulation_count=int(config.get("simulation_count") or 2500),
            assumption_source=str(config.get("assumption_source") or "historical"),
            benchmark_symbol=str(config.get("benchmark_symbol") or "").strip() or None,
            status=status,
            config_json=json.dumps(config, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        session.flush()
        result = SimulationResult(
            run_id=run.id,
            profile_name=profile_name,
            summary_json=json.dumps(summary, ensure_ascii=False),
            charts_json=json.dumps(charts, ensure_ascii=False),
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            scenarios_json=json.dumps(scenarios, ensure_ascii=False),
            created_at=now,
        )
        session.add(result)
        session.flush()
        return {"run_id": run.id}


def list_simulation_runs(profile_name: str, limit: int = 20) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = (
            session.query(SimulationRun)
            .filter(SimulationRun.profile_name == profile_name)
            .order_by(SimulationRun.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return [
            {
                "run_id": row.id,
                "name": row.name or "",
                "mode": row.mode,
                "methods": row.methods_json or [],
                "frequency": row.frequency,
                "horizon_periods": row.horizon_periods,
                "simulation_count": row.simulation_count,
                "assumption_source": row.assumption_source,
                "benchmark_symbol": row.benchmark_symbol or "",
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in rows
        ]


def get_simulation_run(profile_name: str, run_id: int) -> dict[str, Any] | None:
    with get_session() as session:
        run = (
            session.query(SimulationRun)
            .filter(
                SimulationRun.profile_name == profile_name,
                SimulationRun.id == run_id,
            )
            .first()
        )
        if run is None:
            return None
        result = (
            session.query(SimulationResult)
            .filter(
                SimulationResult.profile_name == profile_name,
                SimulationResult.run_id == run.id,
            )
            .first()
        )
        return {
            "run_id": run.id,
            "profile": profile_name,
            "config": _loads(run.config_json, {}),
            "status": run.status,
            "summary": _loads(result.summary_json if result else None, {}),
            "charts": _loads(result.charts_json if result else None, {}),
            "chart_data": _loads(result.charts_json if result else None, {}),
            "metrics": _loads(result.metrics_json if result else None, {}),
            "scenarios": _loads(result.scenarios_json if result else None, []),
            "created_at": run.created_at.isoformat() if run.created_at else "",
        }


def save_simulation_preset(
    *,
    profile_name: str,
    preset_name: str,
    description: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    now = _utcnow()
    normalized_name = (preset_name or "").strip().lower().replace(" ", "_")
    if not normalized_name:
        raise ValueError("Preset name is required")
    with get_session() as session:
        row = (
            session.query(SimulationPreset)
            .filter(
                SimulationPreset.profile_name == profile_name,
                SimulationPreset.preset_name == normalized_name,
            )
            .first()
        )
        if row is None:
            row = SimulationPreset(
                profile_name=profile_name,
                preset_name=normalized_name,
                created_at=now,
            )
            session.add(row)
        row.description = (description or "").strip() or None
        row.config_json = json.dumps(config, ensure_ascii=False)
        row.active = True
        row.updated_at = now
        session.flush()
        return {
            "preset_name": row.preset_name,
            "description": row.description or "",
            "config": _loads(row.config_json, {}),
        }


def list_simulation_presets(profile_name: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = (
            session.query(SimulationPreset)
            .filter(
                SimulationPreset.profile_name == profile_name,
                SimulationPreset.active.is_(True),
            )
            .order_by(SimulationPreset.preset_name.asc())
            .all()
        )
        return [
            {
                "preset_name": row.preset_name,
                "description": row.description or "",
                "config": _loads(row.config_json, {}),
            }
            for row in rows
        ]


def get_simulation_preset(profile_name: str, preset_name: str) -> dict[str, Any] | None:
    normalized_name = (preset_name or "").strip().lower().replace(" ", "_")
    with get_session() as session:
        row = (
            session.query(SimulationPreset)
            .filter(
                SimulationPreset.profile_name == profile_name,
                SimulationPreset.preset_name == normalized_name,
                SimulationPreset.active.is_(True),
            )
            .first()
        )
        if row is None:
            return None
        return {
            "preset_name": row.preset_name,
            "description": row.description or "",
            "config": _loads(row.config_json, {}),
        }


def _loads(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback
