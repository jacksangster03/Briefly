"""Canonical session metadata for all six Briefly sessions.

Single source of truth for session labels, focus lines, and time windows.
Used by the scheduler, schedule-status CLI, daily summary, and any
user-facing session display. Internal session keys are never changed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class SessionMeta:
    key: str
    label: str
    focus: str
    window_start: time
    window_end: time

    @property
    def window_str(self) -> str:
        return f"{self.window_start.strftime('%H:%M')}-{self.window_end.strftime('%H:%M')}"


# ---------------------------------------------------------------------------
# Canonical session list (ordered chronologically)
# ---------------------------------------------------------------------------

ALL_SESSIONS: tuple[SessionMeta, ...] = (
    SessionMeta(
        key="morning",
        label="Morning Briefing",
        focus="Asia overnight + Europe open + US prior close",
        window_start=time(6, 0),
        window_end=time(10, 30),
    ),
    SessionMeta(
        key="europe_midday",
        label="Europe Midday Check",
        focus="Europe session",
        window_start=time(10, 30),
        window_end=time(13, 30),
    ),
    SessionMeta(
        key="us_pre_open",
        label="US Pre-Open Setup",
        focus="US setup",
        window_start=time(13, 30),
        window_end=time(15, 30),
    ),
    SessionMeta(
        key="us_intraday_risk",
        label="US Intraday Risk Check",
        focus="US session",
        window_start=time(15, 30),
        window_end=time(17, 30),
    ),
    SessionMeta(
        key="into_close",
        label="Into Close Update",
        focus="Late US session",
        window_start=time(17, 30),
        window_end=time(22, 0),
    ),
    SessionMeta(
        key="closing_wrap",
        label="Closing Wrap / Next-Day Setup",
        focus="US close + next-day setup",
        window_start=time(22, 0),
        window_end=time(23, 59),
    ),
)

_BY_KEY: dict[str, SessionMeta] = {s.key: s for s in ALL_SESSIONS}

# Legacy aliases used only for display lookups; internal keys stay canonical.
_ALIASES: dict[str, str] = {
    "intraday": "us_intraday_risk",
    "midday": "europe_midday",
    "preopen": "us_pre_open",
    "close": "into_close",
}

# Asia coverage note shown in schedule-status and daily summary
ASIA_COVERAGE_NOTE = (
    "Asia is covered in Morning Briefing (Asia overnight + Europe open + US prior close). "
    "A dedicated Asia-open session can be added later if needed."
)


def get_session_meta(key: str) -> SessionMeta | None:
    """Return SessionMeta for a canonical key or alias. Returns None if not found."""
    normalised = (key or "").strip().lower()
    resolved = _ALIASES.get(normalised, normalised)
    return _BY_KEY.get(resolved)


def label_for(key: str) -> str:
    """Human label for a session key. Falls back to the key itself."""
    meta = get_session_meta(key)
    return meta.label if meta else key


def focus_for(key: str) -> str:
    """Focus line for a session key. Falls back to empty string."""
    meta = get_session_meta(key)
    return meta.focus if meta else ""
