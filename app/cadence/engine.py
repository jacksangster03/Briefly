"""Cadence and decision engine for morning/intraday/breaking orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.cadence.state_store import has_cadence_marker
from app.cadence.us_market_calendar import session_for_ny_date
from app.schemas.briefings import BreakingClassification
from app.settings import Settings

ActionType = Literal[
    "send_morning",
    "send_intraday",
    "send_breaking",
    "schedule_followup",
    "no_action",
]


class CadenceDecision(BaseModel):
    """Structured decision object for runtime action routing."""

    action_type: ActionType
    reason: str
    local_time: str
    related_event_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class IntradayWindow:
    """Local pre-open window derived from 09:30 America/New_York."""

    ny_date: date
    us_cash_open_local: datetime
    preopen_start_local: datetime
    preopen_end_local: datetime
    is_market_open_day: bool
    is_half_day: bool
    reason: str


class CadenceEngine:
    """Time-zone-safe cadence helper aligned to local time + NY market clock."""

    def __init__(self, settings: Settings, *, local_timezone: str | None = None):
        self.settings = settings
        tz_name = local_timezone or settings.timezone
        self.local_tz = self._load_zone(tz_name, fallback="UTC")
        self.ny_tz = self._load_zone("America/New_York", fallback="UTC")

    @staticmethod
    def _load_zone(name: str, *, fallback: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return ZoneInfo(fallback)

    def now_local(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(self.local_tz)

    def local_iso(self, now: datetime | None = None) -> str:
        return self.now_local(now).isoformat()

    def morning_window(
        self,
        local_date: date,
        *,
        start_hhmm: str = "08:00",
        target_hhmm: str = "08:45",
        end_hhmm: str = "09:30",
    ) -> tuple[datetime, datetime, datetime]:
        start = _combine_local(local_date, start_hhmm, self.local_tz)
        target = _combine_local(local_date, target_hhmm, self.local_tz)
        end = _combine_local(local_date, end_hhmm, self.local_tz)
        return start, target, end

    def intraday_preopen_window(self, now: datetime | None = None) -> IntradayWindow:
        local_now = self.now_local(now)
        ny_now = local_now.astimezone(self.ny_tz)
        ny_date = ny_now.date()

        session = session_for_ny_date(ny_date)
        open_ny = datetime(
            ny_date.year,
            ny_date.month,
            ny_date.day,
            9,
            30,
            tzinfo=self.ny_tz,
        )
        open_local = open_ny.astimezone(self.local_tz)
        return IntradayWindow(
            ny_date=ny_date,
            us_cash_open_local=open_local,
            preopen_start_local=open_local - timedelta(minutes=20),
            preopen_end_local=open_local - timedelta(minutes=2),
            is_market_open_day=session.is_open_day,
            is_half_day=session.is_half_day,
            reason=session.reason,
        )


class DecisionEngine:
    """Cadence decisions for morning, intraday, and breaking actions."""

    def __init__(self, settings: Settings, *, profile_name: str, local_timezone: str | None = None):
        self.settings = settings
        self.profile_name = profile_name
        self.cadence = CadenceEngine(settings, local_timezone=local_timezone)

    def decide_morning(
        self,
        now: datetime | None = None,
        *,
        target_hhmm: str = "08:45",
        window_start_hhmm: str = "08:00",
        window_end_hhmm: str = "09:30",
    ) -> CadenceDecision:
        local_now = self.cadence.now_local(now)
        local_date = local_now.date()
        marker_key = f"morning:{local_date.isoformat()}"
        if has_cadence_marker(self.profile_name, marker_key):
            return _decision("no_action", "Morning already sent today", local_now)

        start, target, end = self.cadence.morning_window(
            local_date,
            start_hhmm=window_start_hhmm,
            target_hhmm=target_hhmm,
            end_hhmm=window_end_hhmm,
        )
        if local_now < start:
            return _decision("no_action", "Before local morning window", local_now)
        if local_now < target:
            return _decision("no_action", "Before morning target time", local_now)
        if local_now > end:
            return _decision("no_action", "Morning window passed; defer to next day", local_now)
        return _decision(
            "send_morning",
            "Within local morning window and unsent today",
            local_now,
            metadata={"marker_key": marker_key},
        )

    def decide_intraday(self, now: datetime | None = None) -> CadenceDecision:
        local_now = self.cadence.now_local(now)
        local_date = local_now.date()
        marker_key = f"intraday:{local_date.isoformat()}"
        if has_cadence_marker(self.profile_name, marker_key):
            return _decision("no_action", "Intraday already sent today", local_now)

        window = self.cadence.intraday_preopen_window(local_now)
        if not window.is_market_open_day:
            suffix = " (half day)" if window.is_half_day else ""
            return _decision("no_action", f"US market closed ({window.reason}){suffix}", local_now)
        if local_now < window.preopen_start_local:
            return _decision("no_action", "Before US pre-open local window", local_now)
        if local_now > window.preopen_end_local:
            return _decision("no_action", "US pre-open window passed; skip today", local_now)

        return _decision(
            "send_intraday",
            "Inside local US pre-open window and unsent today",
            local_now,
            metadata={
                "marker_key": marker_key,
                "us_cash_open_local": window.us_cash_open_local.isoformat(),
            },
        )

    def decide_breaking(
        self,
        *,
        classification: BreakingClassification,
        related_event_id: str | None = None,
        now: datetime | None = None,
    ) -> CadenceDecision:
        local_now = self.cadence.now_local(now)
        if classification.tier == "breaking":
            return _decision(
                "send_breaking",
                "Meets BREAKING gates (impact/immediacy/market linkage)",
                local_now,
                related_event_id=related_event_id,
            )
        if classification.tier == "high_priority":
            return _decision(
                "no_action",
                "High-priority but non-breaking; route to next summary",
                local_now,
                related_event_id=related_event_id,
            )
        return _decision(
            "no_action",
            "Does not meet BREAKING threshold",
            local_now,
            related_event_id=related_event_id,
        )

    def decide_schedule_followup(
        self,
        *,
        related_event_id: str | None = None,
        now: datetime | None = None,
        reason: str = "Initial BREAKING sent; schedule one follow-up check.",
    ) -> CadenceDecision:
        local_now = self.cadence.now_local(now)
        return _decision(
            "schedule_followup",
            reason,
            local_now,
            related_event_id=related_event_id,
        )


def _decision(
    action_type: ActionType,
    reason: str,
    local_now: datetime,
    *,
    related_event_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> CadenceDecision:
    return CadenceDecision(
        action_type=action_type,
        reason=reason,
        local_time=local_now.isoformat(),
        related_event_id=related_event_id,
        metadata=metadata or {},
    )


def _combine_local(local_date: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    return datetime.combine(local_date, time(hour=hour, minute=minute), tzinfo=tz)
