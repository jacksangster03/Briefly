"""Typed helpers for simulation payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SIMULATION_COUNT_PRESETS: dict[str, int] = {
    "fast": 500,
    "standard": 2500,
    "deep": 10000,
}

HORIZON_PRESETS: dict[str, tuple[int, str]] = {
    "1m": (21, "daily"),
    "3m": (63, "daily"),
    "6m": (126, "daily"),
    "1y": (252, "daily"),
    "3y": (36, "monthly"),
    "5y": (60, "monthly"),
    "10y": (120, "monthly"),
}

FREQUENCY_TO_ANNUAL_PERIODS: dict[str, int] = {
    "daily": 252,
    "weekly": 52,
    "monthly": 12,
}


@dataclass
class SimulationConfig:
    mode: str
    methods: list[str]
    frequency: str
    horizon_periods: int
    simulation_count: int
    assumption_source: str
    benchmark_symbol: str
    holdings: list[dict[str, Any]]
    macro_overrides: dict[str, float]
    start_value: float

