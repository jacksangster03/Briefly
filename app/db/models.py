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
    message_type = Column(String(50), nullable=False)  # session_brief:<session_key> | breaking | other
    channel = Column(String(50), nullable=False)        # telegram | email
    event_ids = Column(JSON, default=list)
    content_preview = Column(Text, default="")
    content_hash = Column(String(64))
    sent_at = Column(DateTime, default=_utcnow)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)


class SessionSendState(Base):
    """Per-session/channel idempotency and in-flight send claim state."""

    __tablename__ = "session_send_state"
    __table_args__ = (
        UniqueConstraint(
            "profile_name",
            "channel",
            "session_key",
            "local_date",
            "replay_namespace",
            name="uq_session_send_scope",
        ),
        UniqueConstraint("idempotency_key", name="uq_session_send_idempotency_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    channel = Column(String(50), nullable=False, index=True)
    session_key = Column(String(50), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    replay_namespace = Column(String(120), nullable=False, default="", index=True)
    message_type = Column(String(80), nullable=False)
    idempotency_key = Column(String(255), nullable=False, index=True)
    in_progress = Column(Boolean, default=True, index=True)
    success = Column(Boolean, default=False, index=True)
    command_source = Column(String(40), nullable=False, default="cli")
    process_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)


class DeliveryFailureAlertState(Base):
    """Dedupe/cooldown state for scheduler delivery-failure alerts."""

    __tablename__ = "delivery_failure_alert_state"
    __table_args__ = (
        UniqueConstraint(
            "profile_name",
            "local_date",
            "session_key",
            "failed_channels_hash",
            name="uq_delivery_failure_alert_scope",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    session_key = Column(String(80), nullable=False, index=True)
    failed_channels_hash = Column(String(64), nullable=False, index=True)
    failed_channels_json = Column(JSON, nullable=False, default=list)
    first_alerted_at = Column(DateTime, default=_utcnow, index=True)
    last_alerted_at = Column(DateTime, default=_utcnow, index=True)
    alert_count = Column(Integer, default=1)


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


class InvestorPolicy(Base):
    """Profile-level investment policy statement inputs."""

    __tablename__ = "investor_policy"
    __table_args__ = (
        UniqueConstraint("profile_name", name="uq_investor_policy_profile"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    investor_type = Column(String(80), nullable=True)
    base_currency = Column(String(12), nullable=True)
    investment_horizon_years = Column(Float, nullable=True)
    liquidity_need_percent = Column(Float, nullable=True)
    target_return_percent = Column(Float, nullable=True)
    max_volatility_percent = Column(Float, nullable=True)
    max_drawdown_percent = Column(Float, nullable=True)
    single_name_limit_percent = Column(Float, nullable=True)
    max_equity_percent = Column(Float, nullable=True)
    min_liquid_assets_percent = Column(Float, nullable=True)
    benchmark_policy = Column(Text, nullable=True)
    rebalancing_policy = Column(Text, nullable=True)
    prohibited_assets_json = Column(JSON, default=list)
    governance_review_frequency = Column(String(80), nullable=True)
    notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class StrategicAllocationTarget(Base):
    """Profile-level strategic asset allocation targets and allowed ranges."""

    __tablename__ = "strategic_allocation_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    asset_class = Column(String(80), nullable=False, index=True)
    target_weight_pct = Column(Float, nullable=True)
    min_weight_pct = Column(Float, nullable=True)
    max_weight_pct = Column(Float, nullable=True)
    role = Column(String(40), nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class BenchmarkConfig(Base):
    """Profile benchmark configuration used for future relative analytics."""

    __tablename__ = "benchmark_config"
    __table_args__ = (
        UniqueConstraint("profile_name", name="uq_benchmark_config_profile"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    benchmark_type = Column(String(40), nullable=False, default="market_index")
    name = Column(String(120), nullable=True)
    base_symbol = Column(String(32), nullable=True)
    components_json = Column(JSON, default=list)
    notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class PortfolioReturnSeries(Base):
    """Cached daily portfolio and benchmark returns per profile."""

    __tablename__ = "portfolio_return_series"
    __table_args__ = (
        UniqueConstraint("profile_name", "date", name="uq_portfolio_return_profile_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    date = Column(Date, nullable=False)
    portfolio_return_pct = Column(Float, nullable=True)
    benchmark_return_pct = Column(Float, nullable=True)
    active_return_pct = Column(Float, nullable=True)
    stale = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class BenchmarkPriceCache(Base):
    """Daily close prices for benchmark symbols."""

    __tablename__ = "benchmark_price_cache"
    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_benchmark_price_symbol_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(32), nullable=False, index=True)
    date = Column(Date, nullable=False)
    close_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class RiskMetricsSnapshot(Base):
    """Persisted scalar risk metrics per profile."""

    __tablename__ = "risk_metrics_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    computed_at = Column(DateTime, nullable=False)
    lookback_days = Column(Integer, nullable=False, default=252)
    sharpe_ratio = Column(Float, nullable=True)
    sortino_ratio = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    benchmark_max_drawdown_pct = Column(Float, nullable=True)
    volatility_pct = Column(Float, nullable=True)
    benchmark_volatility_pct = Column(Float, nullable=True)
    total_return_pct = Column(Float, nullable=True)
    benchmark_return_pct = Column(Float, nullable=True)
    active_return_pct = Column(Float, nullable=True)
    tracking_error_pct = Column(Float, nullable=True)
    information_ratio = Column(Float, nullable=True)
    risk_free_rate_pct = Column(Float, nullable=True)
    data_completeness_pct = Column(Float, nullable=True)
    rolling_30d_json = Column(Text, nullable=True)
    rolling_90d_json = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


class RegimeSnapshot(Base):
    """Persisted morning regime classification for auditability and transitions."""

    __tablename__ = "regime_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=_utcnow, index=True)
    risk_regime = Column(String(32), nullable=False, default="mixed", index=True)
    trend_regime = Column(String(32), nullable=False, default="continuation")
    factor_regime = Column(String(32), nullable=False, default="balanced")
    vix = Column(Float, nullable=True)
    hy_oas = Column(Float, nullable=True)
    trigger_notes = Column(Text, nullable=True)
    setup_tags = Column(JSON, default=list)
    geo_risk_level = Column(String(24), nullable=True)


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


class CMAEntry(Base):
    """Per-asset-class capital market assumptions for a profile."""

    __tablename__ = "cma_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    asset_class = Column(String(80), nullable=False)
    expected_return_pct = Column(Float, nullable=False, default=0.0)
    expected_volatility_pct = Column(Float, nullable=False, default=0.0)
    notes = Column(String(255), nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class CMACorrelation(Base):
    """Pairwise correlation entries for CMA asset classes."""

    __tablename__ = "cma_correlations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    asset_class_a = Column(String(80), nullable=False)
    asset_class_b = Column(String(80), nullable=False)
    correlation = Column(Float, nullable=False, default=0.0)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class RebalancingConfig(Base):
    """Per-profile rebalancing engine parameters."""

    __tablename__ = "rebalancing_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    method = Column(String(40), nullable=False, default="drift_threshold")
    drift_threshold_pct = Column(Float, nullable=True, default=5.0)
    frequency = Column(String(40), nullable=True, default="quarterly")
    portfolio_value = Column(Float, nullable=True)
    transaction_cost_bps = Column(Float, nullable=True, default=10.0)
    min_trade_pct = Column(Float, nullable=True, default=0.5)
    tax_aware = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class RebalanceProposal(Base):
    """Append-only log of generated rebalance proposals."""

    __tablename__ = "rebalance_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    proposed_at = Column(DateTime, nullable=False)
    status = Column(String(40), nullable=True)
    trigger_type = Column(String(40), nullable=True, default="manual")
    turnover_pct = Column(Float, nullable=True)
    estimated_cost_bps = Column(Float, nullable=True)
    portfolio_value = Column(Float, nullable=True)
    trades_json = Column(Text, nullable=True)
    config_snapshot_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class AttributionSnapshot(Base):
    """Persisted Brinson-Hood-Beebower attribution result per profile."""

    __tablename__ = "attribution_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    computed_at = Column(DateTime, nullable=False)
    method = Column(String(40), nullable=False, default="brinson_cma")
    benchmark_return_pct = Column(Float, nullable=True)
    portfolio_return_pct = Column(Float, nullable=True)
    active_return_pct = Column(Float, nullable=True)
    allocation_effect_pct = Column(Float, nullable=True)
    selection_effect_pct = Column(Float, nullable=True)
    interaction_effect_pct = Column(Float, nullable=True)
    sector_attribution_json = Column(Text, nullable=True)
    asset_class_attribution_json = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


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


class SimulationRun(Base):
    """Simulation run metadata and configuration snapshot."""

    __tablename__ = "simulation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    name = Column(String(120), nullable=True)
    mode = Column(String(30), nullable=False, default="portfolio")  # portfolio | stock
    methods_json = Column(JSON, default=list)
    frequency = Column(String(20), nullable=False, default="monthly")  # daily | weekly | monthly
    horizon_periods = Column(Integer, nullable=False, default=12)
    simulation_count = Column(Integer, nullable=False, default=2500)
    assumption_source = Column(String(30), nullable=False, default="historical")  # historical | cma | manual
    benchmark_symbol = Column(String(32), nullable=True)
    status = Column(String(20), nullable=False, default="completed")  # completed | failed
    config_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class SimulationResult(Base):
    """Simulation output payload for a given run."""

    __tablename__ = "simulation_results"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_simulation_result_run"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False, index=True)
    profile_name = Column(String(80), nullable=False, index=True)
    summary_json = Column(Text, nullable=False)
    charts_json = Column(Text, nullable=False)
    metrics_json = Column(Text, nullable=False)
    scenarios_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, default=_utcnow)


class SimulationPreset(Base):
    """Saved simulation presets for repeated experimentation."""

    __tablename__ = "simulation_presets"
    __table_args__ = (
        UniqueConstraint("profile_name", "preset_name", name="uq_simulation_preset_profile_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    preset_name = Column(String(80), nullable=False)
    description = Column(String(255), nullable=True)
    config_json = Column(Text, nullable=False)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class UserFeedback(Base):
    """User feedback on individual chart signals and data quality.

    Captured via web UI thumbs-down buttons and Telegram inline keyboard callbacks.
    Used to bias chart priority scores in morning_charts.py after enough data accumulates.
    """

    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    chart_key = Column(String(80), nullable=False, index=True)
    label = Column(String(40), nullable=False, index=True)  # useful | not_relevant | wrong_data
    source = Column(String(20), nullable=False, default="web")  # web | telegram
    briefing_date = Column(Date, nullable=True, index=True)
    notes = Column(Text, nullable=True)
    regime_tags = Column(JSON, default=list)   # snapshot of regime at time of feedback
    created_at = Column(DateTime, default=_utcnow, index=True)


# ---------------------------------------------------------------------------
# Phase 7A: Fixed Income Analytics
# ---------------------------------------------------------------------------

class BondHoldingOverride(Base):
    """User-supplied per-holding bond parameters (duration, YTM, credit quality)."""

    __tablename__ = "bond_holding_overrides"
    __table_args__ = (
        UniqueConstraint("profile_name", "symbol", name="uq_bond_override_profile_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    modified_duration_yrs = Column(Float, nullable=True)
    ytm_override_pct = Column(Float, nullable=True)
    coupon_pct = Column(Float, nullable=True)
    maturity_date = Column(Date, nullable=True)
    credit_quality = Column(String(10), nullable=True)  # govt | ig | hy | em
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class BondPortfolioSnapshot(Base):
    """Append-only computed bond analytics result per profile."""

    __tablename__ = "bond_portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    computed_at = Column(DateTime, nullable=False)
    bond_holding_count = Column(Integer, nullable=False, default=0)
    total_bond_weight_pct = Column(Float, nullable=True)
    portfolio_duration_yrs = Column(Float, nullable=True)
    portfolio_ytm_pct = Column(Float, nullable=True)
    rate_sensitivity_pct = Column(Float, nullable=True)   # P&L for +100bps parallel shift
    quality_distribution_json = Column(Text, nullable=True)    # {"govt": 0.4, "ig": 0.45, ...}
    maturity_distribution_json = Column(Text, nullable=True)   # {"<1yr": 0.1, "1-3yr": 0.2, ...}
    holdings_detail_json = Column(Text, nullable=True)         # per-holding breakdown
    data_source_notes = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# Phase 7B: PDF Reports
# ---------------------------------------------------------------------------

class GeneratedReport(Base):
    """Metadata for generated portfolio PDF reports."""

    __tablename__ = "generated_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    report_type = Column(String(40), nullable=False, default="portfolio_summary")
    title = Column(String(200), nullable=False, default="Portfolio Report")
    filename = Column(Text, nullable=False)   # relative path under data/reports/
    sections_included_json = Column(Text, nullable=True)
    generated_at = Column(DateTime, nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# Phase 7C: ESG / SRI Scoring
# ---------------------------------------------------------------------------

class ESGScore(Base):
    """Cached ESG scores per holding per profile."""

    __tablename__ = "esg_scores"
    __table_args__ = (
        UniqueConstraint("profile_name", "symbol", "as_of_date", name="uq_esg_profile_symbol_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False)
    overall_score = Column(Float, nullable=True)   # 0-100 or None
    e_score = Column(Float, nullable=True)
    s_score = Column(Float, nullable=True)
    g_score = Column(Float, nullable=True)
    exclusion_flags_json = Column(JSON, default=list)   # ["tobacco", "weapons"]
    controversy_level = Column(Integer, nullable=True)  # 0-5
    provider = Column(String(30), nullable=False, default="yfinance")
    confidence = Column(String(10), nullable=False, default="low")  # high | low | none
    created_at = Column(DateTime, default=_utcnow)


class PortfolioESGSnapshot(Base):
    """Aggregate portfolio ESG result per profile, append-only."""

    __tablename__ = "portfolio_esg_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    computed_at = Column(DateTime, nullable=False)
    weighted_overall = Column(Float, nullable=True)
    weighted_e = Column(Float, nullable=True)
    weighted_s = Column(Float, nullable=True)
    weighted_g = Column(Float, nullable=True)
    coverage_pct = Column(Float, nullable=True)
    exclusion_count = Column(Integer, nullable=True, default=0)
    sri_alignment_label = Column(String(30), nullable=True)
    assessment_json = Column(Text, nullable=True)   # per-holding breakdown
    esg_config_json = Column(Text, nullable=True)   # snapshot of config used
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


class ESGConfig(Base):
    """Per-profile ESG screening preferences."""

    __tablename__ = "esg_configs"
    __table_args__ = (
        UniqueConstraint("profile_name", name="uq_esg_config_profile"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    enabled_screens_json = Column(JSON, default=list)   # ["tobacco", "weapons", "coal"]
    minimum_overall_score = Column(Float, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# Phase 7D: Multi-Currency Support
# ---------------------------------------------------------------------------

class FXRate(Base):
    """Cached daily FX rates."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "as_of_date", name="uq_fx_rate_pair_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_currency = Column(String(10), nullable=False, index=True)
    to_currency = Column(String(10), nullable=False, index=True)
    rate = Column(Float, nullable=False)
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class CurrencyExposure(Base):
    """Per-holding currency exposure for a profile."""

    __tablename__ = "currency_exposures"
    __table_args__ = (
        UniqueConstraint("profile_name", "symbol", name="uq_currency_exposure_profile_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    foreign_currency = Column(String(10), nullable=True)
    weight_pct_home_currency = Column(Float, nullable=True)
    fx_contribution_pct = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


class FXConfig(Base):
    """Per-profile FX analytics configuration."""

    __tablename__ = "fx_configs"
    __table_args__ = (
        UniqueConstraint("profile_name", name="uq_fx_config_profile"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    home_currency = Column(String(10), nullable=False, default="USD")
    hedge_policy = Column(String(20), nullable=False, default="unhedged")  # unhedged | partial | full
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow)


# ---------------------------------------------------------------------------
# Phase 8.9 Lite: Live session archive snapshots
# ---------------------------------------------------------------------------

class SessionArchiveSnapshot(Base):
    """Lightweight archive of live scheduler-generated session briefings.

    One row per profile/date/session. Only source_type='live_scheduler' rows
    are written by default. Backfills, dry runs, replays and manual sends are
    excluded. Pruned automatically after retention_days.
    """

    __tablename__ = "session_archive_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "profile_name", "local_date", "session_key",
            name="uq_session_archive_profile_date_session",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    session_key = Column(String(50), nullable=False, index=True)
    session_title = Column(String(120), nullable=True)
    generated_at_utc = Column(DateTime, nullable=True)
    generated_at_local_str = Column(String(32), nullable=True)   # "2026-05-06 10:29"
    timezone_name = Column(String(80), nullable=True)
    source_type = Column(String(30), nullable=False, default="live_scheduler")
    delivery_attempted = Column(Boolean, default=True)
    delivery_success = Column(Boolean, default=False)
    delivery_channels_json = Column(JSON, nullable=True)   # {"telegram": "sent", "email": "skipped"}
    delivery_status_json = Column(JSON, nullable=True)     # {"telegram": "delivered", ...}
    telegram_text = Column(Text, nullable=True)
    email_subject = Column(String(300), nullable=True)
    email_plain_text = Column(Text, nullable=True)
    email_html = Column(Text, nullable=True)
    market_summary_json = Column(JSON, nullable=True)      # compact index quote list
    macro_summary_json = Column(JSON, nullable=True)       # compact macro series list
    portfolio_summary_json = Column(JSON, nullable=True)   # compact portfolio focus list
    chart_selection_json = Column(JSON, nullable=True)     # compact chart selection list
    events_count = Column(Integer, nullable=True, default=0)
    created_at = Column(DateTime, default=_utcnow, index=True)


class LLMUsageLog(Base):
    """Append-only log of every LLM API call with token counts and estimated cost."""

    __tablename__ = "llm_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    called_at_utc = Column(DateTime, nullable=False, index=True)
    local_date = Column(Date, nullable=True, index=True)
    session_key = Column(String(50), nullable=True, index=True)
    model = Column(String(120), nullable=False)
    call_type = Column(String(50), nullable=False, default="email_render")
    mode = Column(String(20), nullable=False)          # live | shadow | fallback | disabled
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost_usd = Column(Float, nullable=True)  # None when rates not configured
    created_at = Column(DateTime, default=_utcnow)


class NewsClassifierLabel(Base):
    """Local-first supervised label store for ML news classifier development."""

    __tablename__ = "news_classifier_labels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, index=True)
    headline = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")
    source = Column(String(80), nullable=True, index=True)
    domain = Column(String(160), nullable=True, index=True)
    published_at = Column(DateTime, nullable=True, index=True)
    first_seen_at = Column(DateTime, nullable=True, index=True)
    tickers_json = Column(JSON, nullable=True)
    sectors_json = Column(JSON, nullable=True)
    deterministic_story_type = Column(String(80), nullable=True, index=True)
    deterministic_suppression_reason = Column(String(120), nullable=True, index=True)
    deterministic_breaking_eligible = Column(Boolean, nullable=True, index=True)
    deterministic_freshness_state = Column(String(80), nullable=True, index=True)
    deterministic_update_status = Column(String(80), nullable=True, index=True)
    deterministic_score = Column(Float, nullable=True)
    included_in_briefing = Column(Boolean, nullable=True, index=True)
    sent_as_breaking = Column(Boolean, nullable=True, index=True)
    session_key = Column(String(80), nullable=True, index=True)
    local_date = Column(Date, nullable=True, index=True)
    manual_story_type = Column(String(80), nullable=True, index=True)
    manual_suppression_reason = Column(String(120), nullable=True, index=True)
    manual_breaking_eligible = Column(Boolean, nullable=True, index=True)
    manual_ticker_mismatch_risk = Column(String(40), nullable=True, index=True)
    manual_stale_reprint_risk = Column(String(40), nullable=True, index=True)
    label_source = Column(String(20), nullable=False, default="deterministic", index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, index=True)


class NewsClassifierShadowRun(Base):
    """Shadow inference comparison rows for deterministic vs ML classifier."""

    __tablename__ = "news_classifier_shadow_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(80), nullable=False, index=True)
    event_id = Column(String(64), nullable=False, index=True)
    session_key = Column(String(80), nullable=True, index=True)
    local_date = Column(Date, nullable=True, index=True)
    model_name = Column(String(120), nullable=True)
    model_version = Column(String(80), nullable=True)
    deterministic_story_type = Column(String(80), nullable=True, index=True)
    ml_story_type = Column(String(80), nullable=True, index=True)
    deterministic_suppression_reason = Column(String(120), nullable=True, index=True)
    ml_suppression_reason = Column(String(120), nullable=True, index=True)
    deterministic_breaking_eligible = Column(Boolean, nullable=True, index=True)
    ml_breaking_eligible = Column(Boolean, nullable=True, index=True)
    ml_confidence = Column(Float, nullable=True)
    agreement = Column(Boolean, nullable=False, default=False, index=True)
    disagreement_reason = Column(String(160), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class VerticalRunDiagnostics(Base):
    """Best-effort audit trail for vertical engine runs (non-delivery state)."""

    __tablename__ = "vertical_run_diagnostics"
    __table_args__ = (
        UniqueConstraint(
            "profile_name",
            "local_date",
            "session_key",
            "vertical_key",
            name="uq_vertical_diag_profile_date_session_key",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_name = Column(String(80), nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)
    session_key = Column(String(80), nullable=False, index=True)
    vertical_key = Column(String(80), nullable=False, index=True)
    mode = Column(String(40), nullable=True, index=True)
    status = Column(String(40), nullable=True, index=True)
    activation_reason = Column(String(120), nullable=True)
    candidate_count = Column(Integer, nullable=True)
    included_count = Column(Integer, nullable=True)
    suppressed_count = Column(Integer, nullable=True)
    source_status_json = Column(JSON, nullable=True)
    portfolio_exposure_summary = Column(Text, nullable=True)
    watchlist_exposure_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, index=True)


class HealthcareSourceEventRecord(Base):
    """Persisted normalized official healthcare source events."""

    __tablename__ = "healthcare_source_events"
    __table_args__ = (
        UniqueConstraint("source_key", "stable_event_key", name="uq_healthcare_source_stable_event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String(80), nullable=False, index=True)
    source_tier = Column(String(40), nullable=False, default="official", index=True)
    source_event_id = Column(String(160), nullable=True, index=True)
    stable_event_key = Column(String(80), nullable=False, index=True)
    title = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")
    source_url = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True, index=True)
    discovered_at = Column(DateTime, nullable=True, index=True)
    company_name = Column(String(200), nullable=True)
    tickers_json = Column(JSON, nullable=True)
    drug_name = Column(String(200), nullable=True)
    condition = Column(String(200), nullable=True)
    regulator = Column(String(80), nullable=True)
    healthcare_event_type = Column(String(80), nullable=True, index=True)
    trial_phase = Column(String(80), nullable=True)
    trial_status = Column(String(80), nullable=True)
    severity = Column(String(40), nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    freshness_state = Column(String(40), nullable=True, index=True)
    suppress_reason = Column(String(160), nullable=True, index=True)
    raw_data_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
    updated_at = Column(DateTime, default=_utcnow, index=True)
