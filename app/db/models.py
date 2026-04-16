"""SQLAlchemy ORM models for Briefly persistence."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawEvent(Base):
    """Unprocessed events as received from providers."""

    __tablename__ = "raw_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False, index=True)
    source_id = Column(String(255))
    title = Column(Text, nullable=False)
    summary = Column(Text, default="")
    url = Column(Text, default="")
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=_utcnow)
    raw_data = Column(JSON, default=dict)
    content_hash = Column(String(64), index=True)


class NormalisedEvent(Base):
    """Processed, scored, deduplicated events."""

    __tablename__ = "normalised_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), unique=True, index=True)
    source = Column(String(50), index=True)
    source_type = Column(String(50))
    published_at = Column(DateTime, index=True)
    title = Column(Text, nullable=False)
    summary = Column(Text, default="")
    url = Column(Text, default="")
    tickers = Column(JSON, default=list)
    sectors = Column(JSON, default=list)
    regions = Column(JSON, default=list)
    event_type = Column(String(50), default="")
    sentiment = Column(Float, default=0.0)
    importance_score = Column(Float, default=0.0)
    novelty_score = Column(Float, default=0.0)
    personal_relevance_score = Column(Float, default=0.0)
    factual_confidence_score = Column(Float, default=0.5)
    attention_score = Column(Float, default=0.0)
    final_score = Column(Float, default=0.0)
    content_hash = Column(String(64), index=True)
    cluster_id = Column(String(36), nullable=True)
    score_explanation = Column(Text, default="")
    already_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class SentMessage(Base):
    """Record of every message delivered to the user."""

    __tablename__ = "sent_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_type = Column(String(50), nullable=False)  # morning_brief | intraday | breaking
    channel = Column(String(50), nullable=False)        # telegram | email
    event_ids = Column(JSON, default=list)
    content_preview = Column(Text, default="")
    content_hash = Column(String(64))
    sent_at = Column(DateTime, default=_utcnow)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)


class ProviderHealthLog(Base):
    """Per-call observability for data providers."""

    __tablename__ = "provider_health"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), nullable=False, index=True)
    endpoint = Column(String(255))
    latency_ms = Column(Integer)
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    items_returned = Column(Integer, default=0)
    timestamp = Column(DateTime, default=_utcnow)


class MarketSnapshot(Base):
    """Point-in-time price snapshot for indices, sectors, commodities."""

    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    display_name = Column(String(100), default="")
    price = Column(Float)
    change = Column(Float)
    change_percent = Column(Float)
    volume = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    previous_close = Column(Float, nullable=True)
    snapshot_type = Column(String(30), default="quote")  # quote | sector_etf | macro
    timestamp = Column(DateTime, default=_utcnow)


class PortfolioHolding(Base):
    """Persisted portfolio holdings used for personalization."""

    __tablename__ = "portfolio_holdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    weight_pct = Column(Float, nullable=True)
    shares = Column(Float, nullable=True)
    avg_cost = Column(Float, nullable=True)
    account = Column(String(80), nullable=True)
    bucket = Column(String(40), nullable=True)
    sector_override = Column(String(40), nullable=True)
    as_of_date = Column(Date, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class UserPreference(Base):
    """Per-profile control-plane overrides for watchlist, delivery, and sections."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("profile_name", "pref_key", name="uq_user_pref_profile_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    pref_key = Column(String(120), nullable=False, index=True)
    pref_value = Column(JSON, nullable=False, default=dict)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class CadenceMarker(Base):
    """Idempotency marker for once-per-day cadence sends."""

    __tablename__ = "cadence_markers"
    __table_args__ = (
        UniqueConstraint("profile_name", "marker_key", name="uq_cadence_marker_profile_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    marker_key = Column(String(120), nullable=False, index=True)
    action_type = Column(String(50), nullable=False, index=True)  # morning | intraday
    local_date = Column(Date, nullable=False, index=True)
    local_timezone = Column(String(80), nullable=False, default="UTC")
    sent_at_local = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class BreakingStoryState(Base):
    """State machine for breaking storyline lifecycle and follow-up control."""

    __tablename__ = "breaking_story_state"
    __table_args__ = (
        UniqueConstraint("profile_name", "storyline_key", "local_date", name="uq_breaking_story_profile_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    storyline_key = Column(String(80), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    state = Column(String(40), nullable=False, default="initial_sent", index=True)
    phase = Column(String(20), nullable=False, default="initial")  # initial | followup | escalation
    category = Column(String(60), nullable=False, default="macro")
    event_id = Column(String(36), nullable=True, index=True)
    event_title = Column(Text, nullable=False, default="")
    why_markets_care = Column(Text, nullable=False, default="")
    watch_symbols = Column(JSON, default=list)
    first_sent_at = Column(DateTime, nullable=False, default=_utcnow)
    followup_due_at = Column(DateTime, nullable=True, index=True)
    followup_sent_at = Column(DateTime, nullable=True)
    followup_attempts = Column(Integer, nullable=False, default=0)
    last_reason = Column(Text, nullable=False, default="")
    closed = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)
