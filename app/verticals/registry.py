"""Registry for vertical-intelligence plugins."""

from __future__ import annotations

from app.verticals.plugins.healthcare import HealthcareVerticalPlugin


def load_vertical_plugins() -> dict[str, object]:
    plugins = [HealthcareVerticalPlugin()]
    return {plugin.vertical_key: plugin for plugin in plugins}

