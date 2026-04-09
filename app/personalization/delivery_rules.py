"""Load delivery and alert-threshold rules from YAML config."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.settings import Settings


@dataclass
class MorningRules:
    max_themes: int = 5
    max_sector_events: int = 3
    max_watchlist_events: int = 8
    max_earnings: int = 10


@dataclass
class IntradayRules:
    min_final_score: float = 0.40
    max_events_per_update: int = 7
    min_novelty_score: float = 0.30
    continuation_min_final_score: float = 0.58
    continuation_min_novelty_score: float = 0.45


@dataclass
class BreakingRules:
    min_final_score: float = 0.80
    material_update_min_final_score: float = 0.88
    max_per_hour: int = 3
    require_ticker: bool = False
    min_factual_confidence: float = 0.65
    high_priority_types: list[str] = field(default_factory=list)
    elevated_threshold_types: dict[str, float] = field(default_factory=dict)


@dataclass
class AlertRules:
    morning: MorningRules
    intraday: IntradayRules
    breaking: BreakingRules


def load_alert_rules(settings: Settings) -> AlertRules:
    path = Path(settings.configs_dir) / "alert_rules.yaml"
    config = _load_yaml(path)

    morning_cfg = config.get("morning_briefing", {})
    intraday_cfg = config.get("intraday_updates", {})
    breaking_cfg = config.get("breaking_alerts", {})

    return AlertRules(
        morning=MorningRules(
            max_themes=int(morning_cfg.get("max_themes", 5)),
            max_sector_events=int(morning_cfg.get("max_sector_events", 3)),
            max_watchlist_events=int(morning_cfg.get("max_watchlist_events", 8)),
            max_earnings=int(morning_cfg.get("max_earnings", 10)),
        ),
        intraday=IntradayRules(
            min_final_score=float(intraday_cfg.get("min_final_score", 0.40)),
            max_events_per_update=int(intraday_cfg.get("max_events_per_update", 7)),
            min_novelty_score=float(intraday_cfg.get("min_novelty_score", 0.30)),
            continuation_min_final_score=float(
                intraday_cfg.get("continuation_min_final_score", 0.58)
            ),
            continuation_min_novelty_score=float(
                intraday_cfg.get("continuation_min_novelty_score", 0.45)
            ),
        ),
        breaking=BreakingRules(
            min_final_score=float(breaking_cfg.get("min_final_score", 0.80)),
            material_update_min_final_score=float(
                breaking_cfg.get("material_update_min_final_score", 0.88)
            ),
            max_per_hour=int(breaking_cfg.get("max_per_hour", 3)),
            require_ticker=bool(breaking_cfg.get("require_ticker", False)),
            min_factual_confidence=float(breaking_cfg.get("min_factual_confidence", 0.65)),
            high_priority_types=list(breaking_cfg.get("high_priority_types", [])),
            elevated_threshold_types=dict(breaking_cfg.get("elevated_threshold_types", {})),
        ),
    )


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as handle:
        return yaml.safe_load(handle) or {}
