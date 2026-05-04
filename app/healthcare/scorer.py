"""Scoring logic for healthcare / biotech intelligence events."""

from __future__ import annotations

from typing import Any

from app.healthcare.schemas import HealthcareEvent
from app.healthcare.taxonomy import CRITICAL_EVENT_TYPES, SEVERITY_SCORE, SOURCE_QUALITY_SCORE
from app.personalization.user_profile import UserProfile


def score_healthcare_event(
    event: HealthcareEvent,
    *,
    profile: UserProfile,
    healthcare_prefs: dict[str, Any],
) -> HealthcareEvent:
    score = float(SEVERITY_SCORE.get(event.severity, 20.0))
    score += float(SOURCE_QUALITY_SCORE.get(event.source_quality, 0.4)) * 20.0

    watchlist = {symbol.upper() for symbol in profile.all_watchlist_tickers}
    holdings = {symbol.upper() for symbol in profile.portfolio_symbols}
    tickers = {symbol.upper() for symbol in event.company_tickers}
    pref_tickers = {str(symbol).upper() for symbol in healthcare_prefs.get("tickers", [])}
    pref_themes = {str(theme).lower() for theme in healthcare_prefs.get("themes", [])}

    if tickers & holdings:
        score += 16.0
    elif tickers & watchlist:
        score += 10.0
    if tickers & pref_tickers:
        score += 8.0

    text_tags = {tag.lower() for tag in (event.modality + event.therapy_areas)}
    high_signal_tags = {"glp-1", "peptide", "obesity", "api", "cdmo", "manufacturing", "incretin"}
    if text_tags & high_signal_tags:
        score += 9.0
    if text_tags & pref_themes:
        score += 6.0

    if event.trial_phase == "Phase 3":
        score += 10.0
    if event.regulator in {"FDA", "EMA"}:
        score += 7.0
    if event.event_type in CRITICAL_EVENT_TYPES:
        score += 8.0

    if event.source_quality == "low_signal":
        score -= 25.0
    if not event.company_tickers and not text_tags:
        score -= 18.0

    score = max(0.0, min(100.0, score))
    event.relevance_score = round(score, 2)
    event.market_relevance = _market_relevance_line(event)
    event.portfolio_lens = _portfolio_lens_line(event, holdings=holdings, watchlist=watchlist)
    return event


def severity_rank(severity: str) -> int:
    order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    return order.get((severity or "low").lower(), 1)


def _market_relevance_line(event: HealthcareEvent) -> str:
    if event.event_type in {"fda_approval", "fda_crl", "fda_safety", "ema_chmp"}:
        return "Regulatory outcomes can reprice revenue trajectories, risk premiums, and peer valuation multiples."
    if event.event_type in {"clinical_data", "trial_halt", "trial_start", "trial_completion"}:
        return "Clinical readouts and trial status changes are high-beta catalysts for biotech risk and sector leadership."
    if event.event_type == "biotech_financing":
        return "Biotech financing conditions influence development runway risk and risk appetite across small/mid-cap healthcare."
    if event.event_type in {"manufacturing_capacity", "api_supply_chain", "shortage"}:
        return "Supply-chain and capacity constraints can cap realized demand even when therapeutic demand remains strong."
    if event.event_type in {"m_and_a", "licensing_deal"}:
        return "Strategic deal flow can reset platform value, pipeline optionality, and competitive positioning."
    if event.event_type == "earnings_guidance":
        return "Guidance and margin commentary shape near-term healthcare earnings breadth and defensive-sector positioning."
    return "Healthcare headline flow can influence defensive rotation and idiosyncratic single-name volatility."


def _portfolio_lens_line(event: HealthcareEvent, *, holdings: set[str], watchlist: set[str]) -> str:
    tickers = {ticker.upper() for ticker in event.company_tickers}
    if tickers & holdings:
        focus = ", ".join(sorted(tickers & holdings)[:3])
        return f"Direct portfolio relevance: {focus}. Monitor position-level risk and follow-on catalyst timing."
    if tickers & watchlist:
        focus = ", ".join(sorted(tickers & watchlist)[:3])
        return f"Watchlist relevance: {focus}. Track follow-through versus peers and sector ETF response."
    if event.event_type == "biotech_financing":
        return "Financing sensitivity: funding conditions can drive biotech beta and dispersion even without immediate clinical data."
    if {"GLP-1", "peptide"} & set(event.modality):
        return "Theme relevance: GLP-1/peptide momentum remains a major healthcare earnings and capacity driver."
    if {"CDMO", "API", "manufacturing"} & set(event.modality):
        return "Theme relevance: manufacturing throughput and API availability can drive realization risk across obesity pipelines."
    return ""
