"""Registry for vertical-intelligence plugins."""

from __future__ import annotations

from app.verticals.plugins.ai_tech import AITechVerticalPlugin
from app.verticals.plugins.geopolitics import GeopoliticsVerticalPlugin
from app.verticals.plugins.healthcare import HealthcareVerticalPlugin


def load_vertical_plugins() -> dict[str, object]:
    plugins = [
        HealthcareVerticalPlugin(),
        GeopoliticsVerticalPlugin(),
        AITechVerticalPlugin(),
    ]
    return {plugin.vertical_key: plugin for plugin in plugins}
