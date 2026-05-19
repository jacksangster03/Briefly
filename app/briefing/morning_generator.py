"""Morning briefing generator: orchestrates data fetching, processing,
and assembly of the pre-market morning briefing.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re
from zoneinfo import ZoneInfo

from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.global_news_selector import select_global_market_events
from app.briefing.market_setup_interpreter import interpret_market_setup
from app.briefing.macro_policy_service import (
    build_macro_policy_dashboard,
    build_macro_policy_watch_summary,
    should_include_macro_policy_watch,
)
from app.briefing.portfolio_impact import build_portfolio_impact
from app.briefing.regime_context import build_regime_context, compute_geo_risk_level
from app.briefing.regime_tracker import classify_regime, persist_regime_snapshot
from app.briefing.session_delta import split_events_against_previous_snapshot, split_news_since_previous
from app.briefing.session_freshness import build_data_basis_lines, build_freshness_map
from app.briefing.session_quality import compute_session_quality
from app.briefing.session_snapshot import build_what_changed_lines, load_previous_snapshot, snapshot_metrics
from app.briefing.trust_contract import (
    active_index_quotes,
    freshness_block,
    resolve_canonical_prices,
    run_pre_send_lints,
    section_confidence,
)
from app.briefing.regional_lens import build_regional_lens
from app.briefing.theme_builder import build_top_themes
from app.briefing.news_classifier import annotate_news_events, should_suppress_low_signal
from app.briefing.llm_news_classifier import run_llm_news_classifier_shadow
from app.briefing.valuation_lens import ValuationLens
from app.briefing.session_diagnosis import build_session_diagnosis
from app.verticals.engine import build_vertical_section
from app.logger import get_logger
from app.db.session import get_session
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.article_quality import (
    classify_section_fit,
    classify_article_type,
    classify_source_quality,
    classify_source_tier,
    event_company_confidence,
    has_hard_catalyst,
    is_low_quality_for_section,
    is_clickbait_headline,
    neutralize_headline,
)
from app.processing.pipeline import is_actionable_event, process_event_stream
from app.schemas.briefings import MarketSetup, MorningBriefing, session_mode_for
from app.schemas.events import EarningsEvent, MacroDataPoint, NormalisedEvent, PricePoint, QuoteData, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse
from app.universe.ticker_metadata import TICKER_DISPLAY_NAMES, company_name_for_ticker

logger = get_logger("morning_gen")

MACRO_BLEED_TERMS = (
    "powell",
    "fed",
    "inflation",
    "treasury",
    "yield",
    "oil",
    "crude",
    "hormuz",
    "iran",
    "geopolitical",
    "ceasefire",
    "opec",
    "middle east",
    "war",
)
MAX_PORTFOLIO_FOCUS = 5
MAX_GLOBAL_NEWS = 6
PORTFOLIO_DIRECT_MIN_SCORE = 2.0
PORTFOLIO_READTHROUGH_MIN_SCORE = 1.6
TRUST_TOP_THEMES_MIN_SCORE = 1.0
TRUST_SECTOR_SCAN_MIN_SCORE = 0.9
PORTFOLIO_CATALYST_EVENT_TYPES = {
    "earnings",
    "guidance",
    "fda_decision",
    "m_and_a",
    "current_report",
    "annual_report",
    "quarterly_report",
    "ownership_disclosure",
    "macro_release",
    "fed_decision",
    "geopolitical",
    "regulatory",
    "filing",
}
PORTFOLIO_CATALYST_TERMS = (
    "earnings",
    "guidance",
    "outlook",
    "forecast",
    "sec filing",
    "8-k",
    "10-q",
    "10-k",
    "fda",
    "approval",
    "investigation",
    "probe",
    "settlement",
    "contract",
    "order",
    "partnership",
    "acquisition",
    "merger",
    "capex",
    "production",
    "output",
    "supply",
    "demand",
    "pipeline",
    "hormuz",
    "tariff",
    "sanction",
    "recall",
    "launch",
)
SEC_MATERIAL_FILING_TERMS = (
    "earnings",
    "guidance",
    "outlook",
    "acquisition",
    "merger",
    "transaction",
    "ceo",
    "cfo",
    "resign",
    "appoint",
    "bankruptcy",
    "restructuring",
    "buyback",
    "repurchase",
    "dividend",
    "offering",
    "financing",
    "credit facility",
    "lawsuit",
    "litigation",
    "regulatory approval",
    "fda",
    "major contract",
    "investor presentation",
)
PORTFOLIO_READTHROUGH_TERMS = (
    "inflation",
    "cpi",
    "ppi",
    "jobs",
    "payroll",
    "jobless",
    "fomc",
    "powell",
    "federal reserve",
    "rate cut",
    "rate hike",
    "treasury yield",
    "oil",
    "crude",
    "hormuz",
    "opec",
    "sanction",
    "ceasefire",
    "tariff",
    "export restriction",
    "supply chain",
)
PORTFOLIO_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "dirt cheap",
    "outraged",
)
PORTFOLIO_SOFT_PENALTY_PATTERNS = (
    "analysts love",
    "can't agree on a direction",
    "stock is a buy",
    "stock is cheap",
    "betting against",
    "name-drops",
    "says he owns",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
)
PORTFOLIO_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
WATCHLIST_MIN_EDITORIAL_SCORE = 1.0
WATCHLIST_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "your friends will find out and it will be embarrassing",
)
WATCHLIST_SOFT_PENALTY_PATTERNS = (
    "dirt cheap",
    "analysts love",
    "can't agree on a direction",
    "betting against",
    "name-drops",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
    "outraged",
    "smart buy",
    "risky move",
    "losing its edge",
    "should you buy",
    "what's behind",
    "no-brainer",
    "price prediction",
)
WATCHLIST_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
TRUST_HARD_BLOCK_PATTERNS = (
    "travel and security",
    "picked a winner",
    "grabbing gains",
    "grab headlines",
    "move to california",
    "despite high taxes",
    "going out of business",
    "youtuber",
    "still paying",
    "salary",
    "your friends will find out and it will be embarrassing",
    "smart buy",
    "risky move",
    "losing its edge",
    "should you buy",
    "what's behind",
    "no-brainer",
    "price prediction",
)
TRUST_SOFT_PENALTY_PATTERNS = (
    "dirt cheap",
    "analysts love",
    "can't agree on a direction",
    "betting against",
    "name-drops",
    "reiterates buy",
    "buy rating",
    "price target",
    "wall street",
    "outraged",
)
TRUST_SOFT_PENALTY_REGEXES = [
    re.compile(r"\b(?:best|top|worst)\b.{0,35}\bstock\b"),
    re.compile(r"\b(?:buy|sell|hold)\b.{0,25}\bstock\b"),
    re.compile(r"\b(?:is|are)\b.{0,25}\b(?:cheap|undervalued|overvalued)\b"),
]
WEEKEND_LOW_TRUST_SOURCES = (
    "motley fool",
    "fool.com",
    "investorplace",
    "benzinga",
    "zacks",
    "thestreet",
)
TRUST_MARKET_LINK_TERMS = (
    "inflation",
    "rates",
    "yield",
    "treasury",
    "fed",
    "ecb",
    "fomc",
    "oil",
    "crude",
    "gold",
    "dollar",
    "fx",
    "earnings",
    "guidance",
    "sanction",
    "tariff",
    "hormuz",
    "iran",
    "ceasefire",
    "shipping",
    "blockade",
    "geopolitical",
)
TRUST_PERSONAL_FINANCE_PATTERNS = (
    "best cd rates",
    "apy",
    "high-yield savings",
    "checking account",
    "personal finance",
)


def _compute_earnings_surprise(event: EarningsEvent) -> EarningsEvent:
    """Compute surprise_percent when estimate and actual are both available."""
    if (
        event.surprise_percent is None
        and event.eps_estimate is not None
        and event.eps_actual is not None
        and event.eps_estimate != 0.0
    ):
        surprise = (event.eps_actual - event.eps_estimate) / abs(event.eps_estimate) * 100
        event.surprise_percent = round(surprise, 2)
    return event


def enrich_earnings_from_yfinance(events: list[EarningsEvent]) -> list[EarningsEvent]:
    """Backfill missing earnings estimates/actuals from yfinance where possible."""
    try:
        import yfinance as yf
    except ImportError:
        return events

    needs_enrich = [event for event in events if event.eps_estimate is None or event.eps_actual is None]
    symbols = list({event.symbol for event in needs_enrich if event.symbol})
    if not symbols:
        return events

    def _fetch_one(symbol: str):
        try:
            df = yf.Ticker(symbol).earnings_dates
            return symbol, df if df is not None and not df.empty else None
        except Exception:
            return symbol, None

    from concurrent.futures import ThreadPoolExecutor, as_completed
    yf_data: dict[str, object] = {}
    max_workers = min(8, len(symbols))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, df = future.result()
            yf_data[sym] = df

    for event in events:
        df = yf_data.get(event.symbol)
        if df is None:
            _compute_earnings_surprise(event)
            continue
        try:
            for date_idx, row in df.iterrows():
                date_str = str(date_idx)[:10]
                if date_str != event.report_date:
                    continue
                if event.eps_estimate is None:
                    estimate = row["EPS Estimate"]
                    if estimate is not None and str(estimate) != "nan":
                        event.eps_estimate = float(estimate)
                if event.eps_actual is None:
                    actual = row["Reported EPS"]
                    if actual is not None and str(actual) != "nan":
                        event.eps_actual = float(actual)
                break
        except Exception:
            pass
        _compute_earnings_surprise(event)

    return events


class MorningBriefingGenerator:
    """Builds the morning briefing by fetching data from all services,
    processing events, and assembling the final briefing object.
    """

    def __init__(
        self,
        settings: Settings,
        profile: UserProfile,
        universe: SectorUniverse,
        market_data: MarketDataService,
        news_data: NewsDataService,
        macro_data: MacroDataService,
    ):
        self.settings = settings
        self.profile = profile
        self.universe = universe
        self.market_svc = market_data
        self.news_svc = news_data
        self.macro_svc = macro_data
        self.rules = load_alert_rules(settings).morning

    def generate(
        self,
        *,
        session_key: str = "morning",
        session_title: str = "Morning Briefing",
    ) -> MorningBriefing:
        """Generate the full morning briefing."""
        logger.info("Generating %s...", (session_title or "Morning Briefing"))
        now = datetime.now(timezone.utc)
        briefing = MorningBriefing(
            generated_at=now,
            session_mode=session_mode_for(now),
            session_key=session_key,
            session_title=session_title,
        )

        # 1. Market setup (quotes for indices + macro instruments)
        briefing.market_setup = self._build_market_setup()

        # 2. Macro context: FRED core + ECB/Eurostat when configured
        briefing.macro_context = self.macro_svc.get_morning_macro()
        briefing.macro_context.extend(self.macro_svc.get_ecb_snapshot())
        briefing.macro_context.extend(self.macro_svc.get_eurostat_snapshot())
        try:
            briefing.commodity_strip = self.macro_svc.get_commodity_strip()
            gold_q = self.market_svc.get_quote("GC=F")
            if gold_q and gold_q.current_price > 0:
                prev = gold_q.previous_close or None
                briefing.commodity_strip.insert(0, MacroDataPoint(
                    series_id="GC=F",
                    name="Gold (USD/troy oz)",
                    value=round(gold_q.current_price, 2),
                    previous_value=round(prev, 2) if prev else None,
                    change=round(gold_q.change, 2) if gold_q.change is not None else None,
                    change_percent=round(gold_q.change_percent, 4) if gold_q.change_percent is not None else None,
                    date=gold_q.timestamp.strftime("%Y-%m-%d"),
                    source="yfinance",
                ))
        except Exception:
            briefing.commodity_strip = []
        # Reconcile: override FRED commodity values with live yfinance prices where available.
        # This eliminates the T+1 lag conflict between FRED settlement and live futures prices.
        self._reconcile_commodity_sources(briefing)
        ten_y, two_y = self.macro_svc.get_treasury_yields()
        briefing.market_setup.treasury_10y = ten_y
        briefing.market_setup.treasury_2y = two_y
        briefing.canonical_prices = resolve_canonical_prices(briefing)
        active_setup = self._active_market_setup_view(briefing)
        setup_interpretation = interpret_market_setup(active_setup, briefing.macro_context)
        briefing.market_setup_analysis = setup_interpretation.narrative
        briefing.dominant_tape_driver = setup_interpretation.dominant_driver
        briefing.market_setup_analysis_confidence = setup_interpretation.confidence
        briefing.market_setup_signal_tags = setup_interpretation.tags

        # 2b. FX & Dollar Pulse (profile-aware, deterministic, gracefully degrades)
        try:
            from app.fx.basket import build_fx_basket
            from app.fx.panel import fetch_fx_panel
            from app.fx.signals import build_fx_signals
            from app.briefing.fx_section import should_include_fx, build_fx_section_text

            _profile_dict = {
                "home_region": getattr(self.profile, "home_region", "spain"),
                "base_currency": getattr(self.profile, "base_currency", "EUR"),
                "market_focus": getattr(self.profile, "market_focus_region", ""),
                "market_region": getattr(self.profile, "market_region", ""),
            }
            _settings_dict = {
                "fred_api_key": getattr(self.settings, "fred_api_key", ""),
            }
            # Build context for optional pairs (oil shock, commodity shock)
            _fx_context: dict = {}
            for q in briefing.market_setup.macro_quotes:
                label = (q.display_name or q.symbol or "").upper()
                if ("WTI" in label or "CRUDE" in label) and q.change_percent is not None:
                    if abs(float(q.change_percent)) >= 3.5:
                        _fx_context["commodity_shock"] = "oil"
                        _fx_context["oil_shock"] = True
            _fx_basket = build_fx_basket(_profile_dict, _settings_dict, _fx_context)
            _fx_panel = fetch_fx_panel(_fx_basket, _settings_dict)
            # Cross-asset context for materiality scoring
            _xasset_ctx: dict = {}
            for q in briefing.market_setup.macro_quotes:
                label = (q.display_name or q.symbol or "").upper()
                if "GOLD" in label and q.change_percent is not None:
                    _xasset_ctx["gold_change_pct"] = float(q.change_percent)
                if ("WTI" in label or "CRUDE" in label) and q.change_percent is not None:
                    _xasset_ctx["oil_change_pct"] = float(q.change_percent)
            if briefing.market_setup.treasury_10y and briefing.market_setup.treasury_10y.change is not None:
                _xasset_ctx["us_10y_change_bps"] = float(briefing.market_setup.treasury_10y.change) * 100.0
            _fx_signals = build_fx_signals(_fx_panel, _xasset_ctx)
            briefing.fx_materiality_score = _fx_signals.materiality_score
            _session_key = (session_key or "morning").lower()
            if should_include_fx(_fx_signals, _session_key, _profile_dict):
                briefing.fx_pulse_section = build_fx_section_text(
                    _fx_panel, _fx_signals, _profile_dict, _session_key
                )
            else:
                logger.debug(
                    "FX Pulse suppressed for session=%s materiality=%s score=%d",
                    _session_key,
                    _fx_signals.fx_materiality,
                    _fx_signals.materiality_score,
                )
        except Exception:
            logger.debug("FX Pulse build failed; skipping FX section", exc_info=True)

        # 3. Fetch all news/events
        all_events = self.news_svc.fetch_all(
            watchlist=self.profile.all_watchlist_tickers[:20]  # limit API calls
        )
        briefing.events_fetched = len(all_events)
        hub_stats = dict(getattr(getattr(self.news_svc, "global_hub", None), "last_run_stats", {}) or {})
        briefing.news_raw_fetched = int(hub_stats.get("fetched_total", 0) or 0)
        briefing.news_after_fingerprint_dedup = int(hub_stats.get("deduped_total", 0) or 0)

        # 4. Process, cluster, classify, and rank
        # Sector enrichment runs inside the pipeline after ticker resolution
        # so sector tags reflect the cleaned ticker set, not spurious feeds.
        scored = process_event_stream(
            all_events,
            self.profile,
            self.settings,
            sector_lookup=self.universe.sectors_for_ticker,
        )
        annotate_news_events(
            scored,
            now=briefing.generated_at.astimezone(timezone.utc),
            breaking_max_age_hours=max(1, int(getattr(self.settings, "news_breaking_max_age_hours", 6))),
        )
        try:
            briefing.events_pool = scored  # audit-only visibility hook
        except Exception:
            pass
        briefing.events_after_dedup = len(scored)
        eligible = [event for event in scored if not event.already_sent and event.update_status == "new"]
        try:
            local_date = briefing.generated_at.astimezone(
                ZoneInfo(self.profile.timezone or self.settings.timezone or "Europe/Madrid")
            ).date()
            llm_news_diag = run_llm_news_classifier_shadow(
                settings=self.settings,
                profile_name=self.profile.name,
                session_key=briefing.session_key or "morning",
                local_date=local_date,
                events=eligible,
            )
            if llm_news_diag:
                briefing.data_freshness["llm_news_classifier"] = (
                    "shadow_executed"
                    if llm_news_diag.get("executed")
                    else str(llm_news_diag.get("reason", "shadow_skipped"))
                )
        except Exception:
            logger.debug("llm news shadow classifier failed", exc_info=True)

        # 7. Assemble sections
        briefing.global_news = self._build_global_news(eligible, briefing.session_mode)
        briefing.top_themes = self._build_top_themes(eligible, briefing.session_mode)
        briefing.portfolio_focus = self._build_portfolio_focus(eligible)
        briefing.sector_scan = self._build_sector_scan(eligible, briefing.session_mode)
        briefing.earnings_calendar = self._fetch_earnings()
        briefing.earnings_relevance = self._build_earnings_relevance(briefing.earnings_calendar)
        briefing.watchlist_events = self._filter_watchlist_events(eligible)
        briefing.watchlist_quotes = self._fetch_watchlist_quotes()
        briefing.portfolio_quotes = self._fetch_portfolio_quotes()
        healthcare_candidates = self._healthcare_candidate_events(
            eligible=eligible,
            briefing=briefing,
        )
        briefing.healthcare_intelligence = build_vertical_section(
            profile=self.profile,
            vertical_key="healthcare",
            session_key=briefing.session_key,
            candidate_events=healthcare_candidates,
        )
        if should_include_macro_policy_watch(
            profile=self.profile,
            session_key=briefing.session_key or "morning",
        ):
            try:
                macro_payload = build_macro_policy_dashboard(
                    profile=self.profile,
                    settings=self.settings,
                    macro_data_service=self.macro_svc,
                )
                briefing.macro_policy_watch = build_macro_policy_watch_summary(
                    macro_payload,
                    profile=self.profile,
                    session_key=briefing.session_key or "morning",
                )
            except Exception:
                logger.debug("macro policy watch helper unavailable", exc_info=True)
        briefing.valuation_lens_lines = self._build_valuation_lens(briefing)[:3]
        self._apply_session_profile(briefing)
        # Incremental non-morning content: prioritize new developments since prior session.
        try:
            if (briefing.session_key or "morning").lower() != "morning":
                tz = ZoneInfo(self.profile.timezone or self.settings.timezone or "Europe/Madrid")
                local_date = briefing.generated_at.astimezone(tz).date()
                delta = split_news_since_previous(
                    profile_name=self.profile.name,
                    session_key=briefing.session_key,
                    local_date=local_date,
                    timezone_name=self.profile.timezone,
                    events=briefing.global_news,
                )
                if delta.new_news_items:
                    briefing.global_news = delta.new_news_items[:MAX_GLOBAL_NEWS]
                elif delta.carried_forward_items:
                    briefing.global_news = delta.carried_forward_items[:2]
                    briefing.data_basis_lines.append(
                        f"Global news: carried forward from {delta.previous_session_label or 'previous session'} (no material new headlines)"
                    )
                else:
                    briefing.global_news = []
                    if delta.previous_session_label:
                        briefing.data_basis_lines.append(
                            f"Global news: no material new headlines since {delta.previous_session_label}"
                        )
                briefing.what_changed_header = delta.what_changed_header

                # Apply the same deterministic split to other event-heavy sections.
                new_themes, _rep_themes, carried_themes, prev_label = split_events_against_previous_snapshot(
                    profile_name=self.profile.name,
                    session_key=briefing.session_key,
                    local_date=local_date,
                    timezone_name=self.profile.timezone,
                    events=briefing.top_themes,
                )
                briefing.top_themes = (new_themes or carried_themes)[:3]

                new_watch, _rep_watch, carried_watch, prev_label_watch = split_events_against_previous_snapshot(
                    profile_name=self.profile.name,
                    session_key=briefing.session_key,
                    local_date=local_date,
                    timezone_name=self.profile.timezone,
                    events=briefing.watchlist_events,
                )
                briefing.watchlist_events = (new_watch or carried_watch)[:4]

                new_pf, _rep_pf, carried_pf, _prev_pf = split_events_against_previous_snapshot(
                    profile_name=self.profile.name,
                    session_key=briefing.session_key,
                    local_date=local_date,
                    timezone_name=self.profile.timezone,
                    events=briefing.portfolio_focus,
                )
                briefing.portfolio_focus = (new_pf or carried_pf)[:4]

                if not briefing.top_themes and prev_label:
                    briefing.data_basis_lines.append(
                        f"Top themes: no material new items since {prev_label}"
                    )
                if not briefing.watchlist_events and prev_label_watch:
                    briefing.data_basis_lines.append(
                        f"Watchlist events: no material new items since {prev_label_watch}"
                    )
        except Exception:
            logger.debug("session delta comparison failed; falling back to full section set", exc_info=True)
        active_setup = self._active_market_setup_view(briefing)
        setup_interpretation = interpret_market_setup(
            active_setup,
            briefing.macro_context,
            global_news=briefing.global_news,
        )
        briefing.market_setup_analysis = setup_interpretation.narrative
        briefing.dominant_tape_driver = setup_interpretation.dominant_driver
        briefing.market_setup_analysis_confidence = setup_interpretation.confidence
        briefing.market_setup_signal_tags = setup_interpretation.tags

        geo_level, geo_raw_level, geo_summary = self._build_geo_risk_meter(briefing)
        briefing.geo_risk_level = geo_level
        briefing.geo_risk_raw_level = geo_raw_level
        briefing.geo_risk_summary = geo_summary
        regime_snapshot, regime_shift = self._track_regime_snapshot(briefing)
        briefing.regime_snapshot = regime_snapshot
        briefing.regime_shift = regime_shift
        sq = compute_session_quality(
            market_breadth=briefing.market_setup.market_breadth,
            index_quotes=active_setup.index_quotes,
            macro_context=briefing.macro_context,
            commodity_strip=briefing.commodity_strip,
            geo_risk_level=briefing.geo_risk_level,
        )
        briefing.session_quality_score = round(sq.score, 4)
        briefing.session_quality_bucket = sq.bucket
        briefing.session_quality_color_hex = sq.color_hex
        briefing.session_quality_label = sq.label
        briefing.dominant_tape_driver = self._harmonize_dominant_driver(briefing)
        regional_lens, regional_skew = build_regional_lens(
            index_quotes=active_setup.index_quotes,
            global_news=briefing.global_news,
        )
        briefing.regional_lens = regional_lens
        briefing.regional_skew_summary = regional_skew
        impact_bullets, action_posture = build_portfolio_impact(
            profile=self.profile,
            global_news=briefing.global_news,
            top_themes=briefing.top_themes,
            portfolio_focus=briefing.portfolio_focus,
            setup_tags=briefing.market_setup_signal_tags,
        )
        briefing.portfolio_impact_bullets = impact_bullets
        briefing.portfolio_action_posture = action_posture
        briefing.applied_news_stack = self._build_applied_news_stack(briefing)
        diagnosis = build_session_diagnosis(briefing)
        briefing.session_diagnosis = diagnosis.to_dict()
        briefing.trigger_board = dict(diagnosis.trigger_board or {})
        briefing.dominant_tape_driver = diagnosis.one_sentence_diagnosis
        if briefing.market_data_outage:
            briefing.session_quality_label = "Data degraded"
            briefing.session_quality_bucket = "DATA_DEGRADED"
            briefing.session_quality_score = -1.0
        regime_context, alignment = build_regime_context(
            profile_name=self.profile.name,
            current_setup_tags=briefing.market_setup_signal_tags,
        )
        briefing.regime_context = regime_context
        briefing.positioning_alignment = alignment
        self._dedupe_cross_section_events(briefing)
        if self._should_build_charts():
            chart_briefing = briefing.model_copy(deep=True)
            chart_briefing.market_setup = active_setup.model_copy(deep=True)
            briefing.chart_assets = MorningChartBuilder(
                profile=self.profile,
                market_data=self.market_svc,
                macro_data_svc=self.macro_svc,
            ).build(chart_briefing)
            briefing.morning_chart_bundle = chart_briefing.morning_chart_bundle
            briefing.morning_chart_selection = chart_briefing.morning_chart_selection
        # Final contract checks before delivery rendering.
        briefing.canonical_prices = resolve_canonical_prices(briefing)
        briefing.contract_warnings = run_pre_send_lints(
            briefing,
            timezone_name=self.profile.timezone,
        )
        # Demote low-confidence ticker/company mismatch themes from user-facing sections.
        if briefing.contract_warnings:
            mismatch_titles: set[str] = set()
            for warning in briefing.contract_warnings:
                text = str(warning or "")
                if "Ticker/company mismatch risk on '" not in text:
                    continue
                try:
                    title = text.split("Ticker/company mismatch risk on '", 1)[1].split("'", 1)[0].strip().lower()
                except Exception:
                    title = ""
                if title:
                    mismatch_titles.add(title)
            if mismatch_titles and briefing.top_themes:
                filtered: list[NormalisedEvent] = []
                for evt in briefing.top_themes:
                    conf = event_company_confidence(evt)
                    title = (evt.title or "").strip().lower()
                    if conf < 0.75 and any(title.startswith(prefix) for prefix in mismatch_titles):
                        continue
                    filtered.append(evt)
                briefing.top_themes = filtered
        briefing.section_confidence = section_confidence(
            briefing,
            timezone_name=self.profile.timezone,
        )
        briefing.data_freshness = freshness_block(
            briefing,
            timezone_name=self.profile.timezone,
        )
        # Deterministic freshness metadata for rows/watchlist and session data-basis banner.
        try:
            all_quotes = (
                list(briefing.market_setup.index_quotes)
                + list(briefing.market_setup.macro_quotes)
                + list(briefing.watchlist_quotes)
                + list(briefing.portfolio_quotes)
            )
            briefing.quote_freshness = build_freshness_map(
                quotes=all_quotes,
                generated_at=briefing.generated_at,
                session_key=briefing.session_key,
                timezone_name=self.profile.timezone,
            )
            computed_basis = build_data_basis_lines(
                session_key=briefing.session_key,
                generated_at=briefing.generated_at,
                timezone_name=self.profile.timezone,
                index_quotes=list(briefing.market_setup.index_quotes),
                macro_quotes=list(briefing.market_setup.macro_quotes),
                watchlist_quotes=list(briefing.watchlist_quotes),
            )
            # Preserve any incremental notes we already appended above.
            briefing.data_basis_lines = computed_basis + [
                line for line in briefing.data_basis_lines if line not in computed_basis
            ]
            if briefing.data_basis_lines:
                briefing.data_freshness["Data basis"] = " | ".join(briefing.data_basis_lines)
        except Exception:
            logger.debug("session freshness classification failed; keeping default freshness block", exc_info=True)
        current_snapshot = snapshot_metrics(briefing)
        _prev_ts, previous_snapshot = load_previous_snapshot(
            profile_name=self.profile.name,
            session_key=briefing.session_key,
            before=briefing.generated_at,
        )
        briefing.what_changed_lines = build_what_changed_lines(
            previous=previous_snapshot,
            current=current_snapshot,
        )
        session_key_norm = (briefing.session_key or "morning").lower()
        if session_key_norm == "morning":
            briefing.what_changed_header = "OVERNIGHT / PRIOR SESSION CHANGE"
            # Morning is the broad-context pass: suppress generic placeholder noise.
            if briefing.what_changed_lines == ["No prior comparable snapshot available."]:
                briefing.what_changed_lines = []
        elif session_key_norm in {"saturday_weekend_briefing", "sunday_weekend_watch"}:
            briefing.what_changed_header = "FRIDAY CLOSE / WEEKEND UPDATE"
            cleaned: list[str] = []
            for line in briefing.what_changed_lines:
                text = str(line or "").strip()
                if not text:
                    continue
                # Remove unchanged/noise deltas such as "X: a -> a (flat)".
                if "->" in text and "(flat" in text.lower():
                    continue
                if "->" in text and "unchanged but still important" in text.lower():
                    continue
                cleaned.append(text)
            if not cleaned or cleaned == ["No prior comparable snapshot available."]:
                cleaned = [
                    "No material cross-asset change since Friday close; focus remains on weekend headlines and Monday futures."
                ]
            briefing.what_changed_lines = cleaned[:6]
        provider_health = self._provider_health_summary()
        if provider_health:
            briefing.data_freshness["Provider Health"] = provider_health
        market_quote_count = len(list(briefing.market_setup.index_quotes) + list(briefing.market_setup.macro_quotes))
        has_macro_points = bool(briefing.macro_context or briefing.commodity_strip)
        has_breadth = bool(briefing.market_setup.market_breadth)
        briefing.market_data_outage = market_quote_count == 0 and not has_macro_points and not has_breadth

        provider_health_lower = str(provider_health or "").lower()
        provider_outage_signals = ("unavailable:", "core providers degraded", "provider failures")
        global_outage = briefing.news_raw_fetched <= 0 and briefing.events_fetched <= 0
        briefing.news_data_outage = global_outage and any(token in provider_health_lower for token in provider_outage_signals)
        if briefing.news_data_outage:
            briefing.news_pipeline_status = "provider_outage_raw_fetch_0"
        elif briefing.news_raw_fetched > 0 and briefing.events_after_dedup <= 0:
            briefing.news_pipeline_status = "raw_fetched_but_filtered_to_0"
        else:
            briefing.news_pipeline_status = "ok"

        if briefing.market_data_outage:
            briefing.market_setup_analysis = "Market data unavailable; no directional read generated."
            briefing.dominant_tape_driver = "Provider data outage; market tape unavailable."
            briefing.market_setup_signal_tags = ["data_outage"]
            briefing.data_freshness["Market data"] = "outage"
            briefing.data_basis_lines.append("Market data unavailable; no directional read generated.")
            briefing.data_basis_lines.append("Provider data outage; see diagnostics.")

        if briefing.news_data_outage:
            briefing.data_freshness["News pipeline"] = "provider_outage_raw_fetch_0"
            briefing.data_basis_lines.append("News scan returned no usable items; provider diagnostics required.")
        elif briefing.news_raw_fetched > 0 and briefing.events_fetched <= 0:
            briefing.data_freshness["News pipeline"] = "raw_fetched_but_selected_0"
            briefing.data_basis_lines.append(
                f"News: {briefing.news_raw_fetched} fetched, 0 selected after filters/suppression."
            )
        else:
            briefing.data_freshness["News pipeline"] = (
                f"raw={briefing.news_raw_fetched}, merged={briefing.events_fetched}, deduped={briefing.events_after_dedup}"
            )
        sent_ids: set[str] = set()
        for event in (
            briefing.global_news
            + briefing.top_themes
            + briefing.watchlist_events
            + briefing.portfolio_focus
        ):
            sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        for snapshot in briefing.sector_scan:
            for event in snapshot.top_events:
                sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        briefing.events_sent = len(sent_ids)

        logger.info(
            "%s ready: %d fetched, %d deduped, %d themes, %d sectors, watchlist_events=%d",
            (briefing.session_title or "Morning Briefing"),
            briefing.events_fetched,
            briefing.events_after_dedup,
            len(briefing.top_themes),
            len(briefing.sector_scan),
            len(briefing.watchlist_events),
        )
        return briefing

    def _build_valuation_lens(self, briefing: MorningBriefing) -> list[str]:
        """Delegate to ValuationLens module. Disabled by default."""
        enabled = bool(self.profile.delivery.get("include_valuation_lens", False))
        max_items = int(self.profile.delivery.get("valuation_lens_max_items", 3) or 3)
        lens = ValuationLens(enabled=enabled, max_items=max_items)
        return lens.build_lines(briefing)

    def _apply_session_profile(self, briefing: MorningBriefing) -> None:
        """Scale content density by session type so non-morning briefs stay concise."""
        key = (briefing.session_key or "morning").lower()
        if key == "morning":
            return
        if key == "saturday_weekend_briefing":
            briefing.global_news = list(briefing.global_news[:5])
            briefing.top_themes = list(briefing.top_themes[:3])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:4])
            briefing.watchlist_events = list(briefing.watchlist_events[:4])
            briefing.sector_scan = list(briefing.sector_scan[:2])
            briefing.macro_context = list(briefing.macro_context[:4])
            briefing.commodity_strip = list(briefing.commodity_strip[:4])
            self._trim_market_snapshot_quotes(briefing, max_index=8, max_macro=5)
            return
        if key == "sunday_weekend_watch":
            briefing.global_news = list(briefing.global_news[:4])
            briefing.top_themes = list(briefing.top_themes[:2])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:2])
            briefing.watchlist_events = list(briefing.watchlist_events[:3])
            briefing.sector_scan = []
            briefing.earnings_calendar = []
            briefing.macro_context = list(briefing.macro_context[:2])
            briefing.commodity_strip = list(briefing.commodity_strip[:2])
            self._trim_market_snapshot_quotes(briefing, max_index=4, max_macro=3)
            return
        watch_symbols = {symbol.upper() for symbol in (self.profile.all_watchlist_tickers + self.profile.portfolio_symbols)}
        local_now = briefing.generated_at.astimezone(timezone.utc)
        today = local_now.date()
        tomorrow = today + timedelta(days=1)

        # Session-specific earnings scope: next 24h + watchlist/portfolio relevance.
        filtered_earnings: list[EarningsEvent] = []
        for evt in briefing.earnings_calendar:
            is_relevant = bool(evt.is_relevant or evt.symbol.upper() in watch_symbols or evt.relevance_tag in {"portfolio", "watchlist"})
            if not evt.report_date:
                if is_relevant:
                    filtered_earnings.append(evt)
                continue
            try:
                report_date = datetime.strptime(evt.report_date, "%Y-%m-%d").date()
            except Exception:
                report_date = today
            if report_date in {today, tomorrow} and is_relevant:
                filtered_earnings.append(evt)
        briefing.earnings_calendar = filtered_earnings[:8]

        if key == "europe_midday":
            briefing.global_news = list(briefing.global_news[:3])
            briefing.top_themes = list(briefing.top_themes[:2])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:3])
            briefing.watchlist_events = list(briefing.watchlist_events[:3])
            briefing.sector_scan = []
            briefing.macro_context = []
            briefing.commodity_strip = list(briefing.commodity_strip[:3])
            self._trim_market_snapshot_quotes(briefing, max_index=7, max_macro=4)
            return

        if key == "us_pre_open":
            briefing.global_news = list(briefing.global_news[:3])
            briefing.top_themes = list(briefing.top_themes[:3])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:3])
            briefing.watchlist_events = list(briefing.watchlist_events[:4])
            briefing.sector_scan = []
            briefing.macro_context = list(briefing.macro_context[:4])
            briefing.commodity_strip = list(briefing.commodity_strip[:3])
            self._trim_market_snapshot_quotes(briefing, max_index=8, max_macro=5)
            return

        if key in {"us_intraday_risk", "into_close"}:
            briefing.global_news = list(briefing.global_news[:2])
            briefing.top_themes = list(briefing.top_themes[:2])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:3])
            briefing.watchlist_events = list(briefing.watchlist_events[:4])
            briefing.sector_scan = []
            briefing.macro_context = []
            briefing.commodity_strip = list(briefing.commodity_strip[:3])
            self._trim_market_snapshot_quotes(briefing, max_index=6, max_macro=4)
            if briefing.healthcare_intelligence and briefing.healthcare_intelligence.items:
                briefing.healthcare_intelligence.items = list(briefing.healthcare_intelligence.items[:3])
            return

        if key == "closing_wrap":
            briefing.global_news = list(briefing.global_news[:4])
            briefing.top_themes = list(briefing.top_themes[:3])
            briefing.portfolio_focus = list(briefing.portfolio_focus[:4])
            briefing.watchlist_events = list(briefing.watchlist_events[:4])
            briefing.macro_context = list(briefing.macro_context[:4])
            briefing.commodity_strip = list(briefing.commodity_strip[:3])
            self._trim_market_snapshot_quotes(briefing, max_index=8, max_macro=5)
            return

    @staticmethod
    def _healthcare_candidate_events(
        *,
        eligible: list[NormalisedEvent],
        briefing: MorningBriefing,
    ) -> list[NormalisedEvent]:
        seen: set[str] = set()
        merged: list[NormalisedEvent] = []
        for event in (
            list(eligible)
            + list(briefing.global_news)
            + list(briefing.top_themes)
            + list(briefing.portfolio_focus)
            + list(briefing.watchlist_events)
        ):
            key = event.cluster_id or event.content_hash or event.event_id
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
        return merged

    @staticmethod
    def _trim_market_snapshot_quotes(briefing: MorningBriefing, *, max_index: int, max_macro: int) -> None:
        """Keep a concise, session-focused market snapshot table."""
        priority_index_tokens = ("S&P", "NASDAQ", "DOW", "RUSSELL", "STOXX", "DAX", "NIKKEI", "HANG SENG", "VIX")
        priority_macro_tokens = ("10Y", "2Y", "WTI", "BRENT", "GOLD", "USD")

        def _rank(name: str, tokens: tuple[str, ...]) -> int:
            text = (name or "").upper()
            for idx, token in enumerate(tokens):
                if token in text:
                    return idx
            return len(tokens) + 1

        index_quotes = sorted(
            briefing.market_setup.index_quotes,
            key=lambda q: (_rank(q.display_name or q.symbol, priority_index_tokens), abs(float(q.change_percent or 0.0)) * -1),
        )
        macro_quotes = sorted(
            briefing.market_setup.macro_quotes,
            key=lambda q: (_rank(q.display_name or q.symbol, priority_macro_tokens), abs(float(q.change_percent or 0.0)) * -1),
        )
        selected_index = list(index_quotes[:max_index])
        vix_row = next((q for q in index_quotes if "VIX" in f"{q.display_name} {q.symbol}".upper()), None)
        if vix_row is not None and all("VIX" not in f"{q.display_name} {q.symbol}".upper() for q in selected_index):
            if selected_index:
                selected_index = selected_index[:-1] + [vix_row]
            else:
                selected_index = [vix_row]
        briefing.market_setup.index_quotes = selected_index
        briefing.market_setup.macro_quotes = macro_quotes[:max_macro]

    @staticmethod
    def _harmonize_dominant_driver(briefing: MorningBriefing) -> str:
        """Ensure dominant driver wording does not conflict with regime label/tags."""
        driver = (briefing.dominant_tape_driver or "").strip()
        if not driver:
            driver = "No single equity catalyst dominates; the tape is balanced across macro factors."
        low_driver = driver.lower()
        if "no single dominant driver identified" in low_driver:
            driver = "No single equity catalyst dominates; the tape is balanced across macro factors."
            low_driver = driver.lower()

        session_label = (briefing.session_quality_label or "").lower()
        tags = {str(t).lower() for t in (briefing.market_setup_signal_tags or [])}
        has_cluster = any(
            token in session_label
            for token in ("energy", "commodity", "rates", "breadth divergence", "geo risk")
        ) or bool(tags & {"commodity_pressure", "rates_headwind", "cross_region_divergence", "risk_off"})
        if has_cluster and "no single equity catalyst dominates" in low_driver:
            return "Regional divergence and risk-factor pressure are leading the tape; no single equity catalyst dominates."
        return driver

    # -- Section builders -----------------------------------------------------

    def _build_market_setup(self) -> MarketSetup:
        """Fetch quotes for indices and macro instruments."""
        setup = MarketSetup()

        # Index quotes
        index_symbols = self.universe.all_index_symbols
        if index_symbols:
            quotes = self.market_svc.get_quotes(index_symbols)
            # Attach display names from universe
            name_map = {i.symbol: i.display for i in self.universe.indices}
            for q in quotes:
                q.display_name = name_map.get(q.symbol, q.symbol)
            setup.index_quotes = quotes
            self._ensure_vix_quote(setup)

        # Macro instrument quotes (gold, oil, USD, BTC)
        macro_symbols = self.universe.all_macro_symbols
        if macro_symbols:
            quotes = self.market_svc.get_quotes(macro_symbols)
            name_map = {m.symbol: m.display for m in self.universe.macro_instruments}
            for q in quotes:
                q.display_name = name_map.get(q.symbol, q.symbol)
            setup.macro_quotes = quotes

        # Breadth proxy (index + sectors) from yfinance fallback provider.
        yfinance = getattr(self.market_svc, "yfinance", None)
        if yfinance and yfinance.is_configured():
            breadth_rows = []
            try:
                # S&P 500 proxy breadth context (volume vs 20D average).
                core = yfinance.get_index_breadth("SPY")
                if core is not None:
                    core.display_name = core.display_name or "S&P 500 Proxy Breadth (SPY)"
                    breadth_rows.append(core)
            except Exception:
                pass
            try:
                breadth_rows.extend(yfinance.get_sector_breadth())
            except Exception:
                pass
            setup.market_breadth = breadth_rows

        return setup

    def _ensure_vix_quote(self, setup: MarketSetup) -> None:
        """Keep VIX available as a core risk instrument across sessions.

        Falls back to latest available history point when live quote fetch misses.
        """
        has_vix = any("VIX" in f"{q.display_name} {q.symbol}".upper() for q in (setup.index_quotes or []))
        if has_vix:
            return
        history = self.market_svc.get_price_history("^VIX", period="5d", interval="1d")
        if not history:
            return
        latest: PricePoint = history[-1]
        close = float(latest.close or 0.0)
        if close <= 0:
            return
        prev_close = float(history[-2].close) if len(history) > 1 else close
        change = close - prev_close
        change_pct = ((close / prev_close) - 1.0) * 100.0 if prev_close else 0.0
        setup.index_quotes.append(
            QuoteData(
                symbol="^VIX",
                display_name="VIX",
                current_price=close,
                change=change,
                change_percent=change_pct,
                previous_close=prev_close,
                timestamp=latest.timestamp,
                source="yfinance_history_fallback",
            )
        )

    def _build_top_themes(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str,
    ) -> list[NormalisedEvent]:
        """Build top themes with an editorial trust gate."""
        preferred_symbols = {
            symbol.upper()
            for symbol in (
                list(self.profile.portfolio_symbols)
                + list(self.profile.all_watchlist_tickers)
            )
            if symbol
        }
        themes = build_top_themes(
            scored_events,
            max_themes=self.rules.max_themes,
            editorial_gate=lambda evt: self._is_editorially_trustworthy(
                evt,
                session_mode,
                section="top_themes",
            ),
            preferred_symbols=preferred_symbols,
        )
        cleaned: list[NormalisedEvent] = []
        for evt in themes:
            if should_suppress_low_signal(evt):
                continue
            conf = event_company_confidence(evt)
            if conf < 0.70 and evt.tickers:
                # Low-confidence ticker/company mapping in Top Themes is usually noisy.
                continue
            cleaned.append(self._apply_news_hygiene(evt, section="portfolio_watchlist_themes"))
        return cleaned

    def _build_applied_news_stack(self, briefing: MorningBriefing) -> list[dict[str, object]]:
        """Build a compact deterministic morning applied-news stack."""
        if (briefing.session_key or "morning").lower() != "morning":
            return []
        pools: list[tuple[str, list[NormalisedEvent]]] = [
            ("macro_rates", list(briefing.top_themes or [])),
            ("regional", list(briefing.global_news or [])),
            ("sector", [evt for snap in (briefing.sector_scan or []) for evt in (snap.top_events or [])]),
            ("watchlist", list(briefing.watchlist_events or [])),
            ("portfolio", list(briefing.portfolio_focus or [])),
            ("event_risk", list(briefing.top_themes or [])),
            ("geopolitical", list(briefing.global_news or [])),
        ]
        keywords: dict[str, tuple[str, ...]] = {
            "macro_rates": ("yield", "treasury", "inflation", "fed", "ecb", "rates", "bond"),
            "regional": ("europe", "us", "asia", "china", "japan", "eurozone"),
            "sector": ("sector", "semiconductor", "energy", "financial", "tech", "bank"),
            "watchlist": tuple(symbol.lower() for symbol in self.profile.all_watchlist_tickers[:20]),
            "portfolio": tuple(symbol.lower() for symbol in self.profile.portfolio_symbols[:20]),
            "event_risk": ("cpi", "pce", "payroll", "fomc", "ecb", "earnings", "guidance"),
            "geopolitical": ("iran", "israel", "hormuz", "sanction", "war", "missile", "ceasefire"),
        }
        built: list[dict[str, object]] = []
        used_ids: set[str] = set()
        for bucket, events in pools:
            evt = next(
                (
                    candidate
                    for candidate in events
                    if candidate.event_id not in used_ids
                    and self._applied_news_match(candidate, keywords.get(bucket, ()))
                ),
                None,
            )
            if evt is None:
                continue
            used_ids.add(evt.event_id)
            built.append(
                {
                    "bucket": bucket,
                    "headline": evt.title,
                    "why_it_matters": self._applied_news_why(bucket, evt),
                    "affected_assets": list(evt.tickers[:4]),
                    "source_count": int(evt.cluster_size or 1),
                    "price_confirmation": self._applied_news_confirmation(bucket, briefing),
                }
            )
            if len(built) >= 7:
                break
        return built

    @staticmethod
    def _applied_news_match(event: NormalisedEvent, keys: tuple[str, ...]) -> bool:
        if not keys:
            return False
        text = f"{event.title} {event.summary}".lower()
        return any(k and k in text for k in keys)

    @staticmethod
    def _applied_news_confirmation(bucket: str, briefing: MorningBriefing) -> str:
        tags = {str(tag).lower() for tag in (briefing.market_setup_signal_tags or [])}
        if bucket == "macro_rates":
            return "confirmed" if ("rates_headwind" in tags or "rates_supportive" in tags) else "unconfirmed"
        if bucket == "geopolitical":
            return "partial" if briefing.geo_risk_level else "unconfirmed"
        if bucket in {"watchlist", "portfolio"}:
            return "confirmed" if (briefing.portfolio_quotes or briefing.watchlist_quotes) else "unconfirmed"
        return "optional"

    @staticmethod
    def _applied_news_why(bucket: str, event: NormalisedEvent) -> str:
        if bucket == "macro_rates":
            return "Rates context can reset valuation-sensitive growth and duration risk."
        if bucket == "regional":
            return "Regional dispersion helps explain mixed index direction."
        if bucket == "sector":
            return "Sector leadership affects breadth quality and index resilience."
        if bucket == "watchlist":
            names = ", ".join(event.tickers[:3]) or "watchlist names"
            return f"Direct watchlist relevance for {names}."
        if bucket == "portfolio":
            names = ", ".join(event.tickers[:3]) or "portfolio names"
            return f"Portfolio transmission risk through {names}."
        if bucket == "event_risk":
            return "Upcoming catalysts can invalidate or reinforce the setup."
        if bucket == "geopolitical":
            return "Headline risk matters only if cross-asset confirmation follows."
        return "Market relevance under review."

    def _build_global_news(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str,
    ) -> list[NormalisedEvent]:
        """Select high-trust, market-linked global/geopolitical stories."""
        selected = select_global_market_events(
            scored_events,
            session_mode=session_mode,
            max_items=MAX_GLOBAL_NEWS,
            region_weights=self.profile.coverage_weights,
            actionable_check=is_actionable_event,
        )
        cleaned: list[NormalisedEvent] = []
        for event in selected:
            if should_suppress_low_signal(event):
                continue
            if classify_section_fit(event) != "global_macro_geo":
                continue
            if event.event_type in {"ipo", "company_news"} and not has_hard_catalyst(event.event_type, event.title, event.summary):
                # Company-level deal/news belongs in portfolio/watchlist or sector sections.
                continue
            evt = self._apply_news_hygiene(event, section="global_macro_geo")
            if is_clickbait_headline(evt.title) and not has_hard_catalyst(evt.event_type, evt.title, evt.summary):
                continue
            cleaned.append(evt)
        return cleaned

    def _build_sector_scan(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str = "weekday",
    ) -> list[SectorSnapshot]:
        """Build sector snapshots with ETF quotes and top events per sector.

        Each event is placed in only one sector: the first matching sector
        in the user's weighted sector order. This prevents the same story
        from appearing in multiple sections (e.g. an Apple/JPMorgan AI
        partnership showing up under both Tech and Financials).
        """
        etf_symbols = self.universe.all_sector_etfs
        etf_quotes = {}
        if etf_symbols:
            for q in self.market_svc.get_quotes(etf_symbols):
                etf_quotes[q.symbol] = q

        weighted_sectors = sorted(
            self.universe.sectors,
            key=lambda s: self.profile.sector_weights.get(s.key, 0.3),
            reverse=True,
        )

        placed_event_ids: set[str] = set()
        snapshots = []
        for sector in weighted_sectors[:12]:
            sector_tickers = set(sector.key_names)
            sector_events = []
            for event in scored_events:
                if event.event_id in placed_event_ids:
                    continue
                if not is_actionable_event(event):
                    continue
                ticker_matches = sum(1 for ticker in event.tickers if ticker in sector_tickers)
                has_sector_tag = sector.key in event.sectors
                if not (ticker_matches or has_sector_tag):
                    continue
                if self._looks_like_macro_bleed(event) and ticker_matches < 2 and event.source != "sec_edgar":
                    continue
                if should_suppress_low_signal(event):
                    continue
                if not self._is_editorially_trustworthy(event, session_mode, section="sector_scan"):
                    continue
                sector_events.append(self._apply_news_hygiene(event, section="sector_signals"))
                placed_event_ids.add(event.event_id)

            etf_quote = etf_quotes.get(sector.etf)
            snap = SectorSnapshot(
                sector_key=sector.key,
                display_name=sector.display_name,
                etf_symbol=sector.etf,
                etf_quote=etf_quote,
                top_events=sector_events[: self.rules.max_sector_events],
            )
            snapshots.append(snap)

        return snapshots

    @staticmethod
    def _looks_like_macro_bleed(event: NormalisedEvent) -> bool:
        text = f"{event.title} {event.summary}".lower()
        return any(term in text for term in MACRO_BLEED_TERMS)

    def _fetch_earnings(self):
        """Fetch upcoming earnings, enrich with relevance/sector/cap, and filter.

        Keeps only events where the symbol is portfolio/watchlist relevant OR
        sits in the curated large-cap universe. Microcaps and unknowns are
        dropped before assembly so the briefing never carries noise.
        """
        if not self.news_svc.finnhub or not self.news_svc.finnhub.is_configured():
            return []

        # Pull a wider window than max_earnings since filtering will trim it.
        raw = self.news_svc.finnhub.get_earnings_calendar(days_ahead=7)

        portfolio = {h.symbol.upper() for h in self.profile.portfolio_holdings}
        watchlist = {s.upper() for s in self.profile.all_watchlist_tickers}
        large_cap_universe = set(TICKER_DISPLAY_NAMES.keys())

        ticker_to_sector: dict[str, str] = {}
        for sector_def in self.universe.sectors:
            for ticker in getattr(sector_def, "key_names", []) or []:
                ticker_to_sector.setdefault(ticker.upper(), sector_def.display_name)

        enriched: list[EarningsEvent] = []
        for event in raw:
            sym = (event.symbol or "").upper()
            if not sym:
                continue
            if sym in portfolio:
                event.is_relevant = True
                event.relevance_tag = "portfolio"
            elif sym in watchlist:
                event.is_relevant = True
                event.relevance_tag = "watchlist"
            if not event.company_name:
                event.company_name = company_name_for_ticker(sym)
            event.sector = ticker_to_sector.get(sym, event.sector or "")
            if sym in large_cap_universe:
                event.market_cap_bucket = "large"
            if event.is_relevant or event.market_cap_bucket == "large":
                enriched.append(event)

        enriched = enriched[: self.rules.max_earnings]
        enriched = [_compute_earnings_surprise(event) for event in enriched]

        prior_surprise_cache: dict[str, float | None] = {}
        finnhub = self.news_svc.finnhub
        if finnhub and finnhub.is_configured():
            for event in enriched:
                if event.prior_quarter_surprise_pct is not None:
                    continue
                symbol = (event.symbol or "").upper()
                if not symbol:
                    continue
                if symbol not in prior_surprise_cache:
                    prior_surprise_cache[symbol] = finnhub.get_prior_quarter_surprise_pct(symbol)
                event.prior_quarter_surprise_pct = prior_surprise_cache[symbol]

        missing = [
            event for event in enriched
            if event.is_relevant and (event.eps_estimate is None or event.eps_actual is None)
        ]
        if missing:
            enrich_earnings_from_yfinance(missing)

        return enriched

    def _track_regime_snapshot(self, briefing: MorningBriefing) -> tuple[dict[str, str], dict[str, str]]:
        vix_level = None
        for quote in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "vix" in text:
                vix_level = float(quote.current_price or 0.0)
                break
        oil_delta = 0.0
        for quote in briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "wti" in text or "crude" in text:
                oil_delta = float(quote.change_percent or 0.0)
                break
        ten_y_delta_bps = None
        if briefing.market_setup.treasury_10y and briefing.market_setup.treasury_10y.change is not None:
            ten_y_delta_bps = float(briefing.market_setup.treasury_10y.change) * 100.0
        breadth_ratio = None
        if briefing.market_setup.market_breadth:
            positive = sum(1 for row in briefing.market_setup.market_breadth if float(row.change_percent or 0.0) > 0.0)
            breadth_ratio = positive / max(1, len(briefing.market_setup.market_breadth))

        decision = classify_regime(
            setup_tags=briefing.market_setup_signal_tags,
            vix_level=vix_level,
            breadth_ratio=breadth_ratio,
            oil_delta_pct=oil_delta,
            ten_y_delta_bps=ten_y_delta_bps,
        )
        shift = persist_regime_snapshot(
            profile_name=self.profile.name,
            decision=decision,
            setup_tags=briefing.market_setup_signal_tags,
            vix_level=vix_level,
            geo_risk_level=briefing.geo_risk_level,
        )
        snapshot = {
            "risk_regime": decision.risk_regime,
            "trend_regime": decision.trend_regime,
            "factor_regime": decision.factor_regime,
        }
        return snapshot, shift

    def _active_market_setup_view(self, briefing: MorningBriefing) -> MarketSetup:
        """Copy market setup and exclude closed benchmarks from active breadth logic."""
        return briefing.market_setup.model_copy(
            update={
                "index_quotes": active_index_quotes(
                    briefing,
                    timezone_name=self.profile.timezone,
                )
            }
        )

    def _reconcile_commodity_sources(self, briefing: MorningBriefing) -> None:
        """Override FRED commodity strip values with live yfinance quotes where available.

        FRED oil/gas series are T+1 settlement prices; the live macro quotes from
        yfinance reflect current futures. Using the live price everywhere ensures the
        market-setup table, commodity strip, geo-risk meter, and session quality score
        all show the same WTI/Brent value.

        Gold is already inserted from yfinance as the first commodity strip element
        and is left untouched here.
        """
        live_wti: QuoteData | None = None
        live_brent: QuoteData | None = None
        for q in briefing.market_setup.macro_quotes:
            label = (q.display_name or q.symbol or "").upper()
            if ("WTI" in label or "CRUDE" in label) and "BRENT" not in label and q.current_price:
                live_wti = q
            elif "BRENT" in label and q.current_price:
                live_brent = q

        today = briefing.generated_at.strftime("%Y-%m-%d")
        for i, pt in enumerate(briefing.commodity_strip):
            pt_key = ((pt.name or "") + " " + (pt.series_id or "")).upper()
            live_q: QuoteData | None = None
            if ("WTI" in pt_key or "DCOILWTICO" in pt_key) and "BRENT" not in pt_key:
                live_q = live_wti
            elif "BRENT" in pt_key or "DCOILBRENTEU" in pt_key:
                live_q = live_brent
            # Gold (GC=F) is already yfinance-sourced — skip
            if live_q is not None and live_q.current_price > 0:
                briefing.commodity_strip[i] = MacroDataPoint(
                    series_id=pt.series_id,
                    name=pt.name,
                    value=round(live_q.current_price, 2),
                    change=round(live_q.change, 2) if live_q.change is not None else None,
                    change_percent=round(live_q.change_percent, 4) if live_q.change_percent is not None else None,
                    date=today,
                    source="yfinance_live",
                )

    def _build_geo_risk_meter(self, briefing: MorningBriefing) -> tuple[str, str, str]:
        from app.briefing.regime_context import _GEO_LEVEL_ORDER

        vix_level = None
        for quote in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if "vix" in text:
                vix_level = float(quote.current_price or 0.0)
                break

        canonical = dict(briefing.canonical_prices or {})

        def _canon_float(key: str, field: str) -> float | None:
            row = dict(canonical.get(key) or {})
            val = row.get(field)
            if val is None:
                return None
            try:
                return float(val)
            except Exception:
                return None

        oil_delta = _canon_float("WTI", "change_percent")
        oil_level = _canon_float("WTI", "value")
        gold_delta = _canon_float("GOLD", "change_percent")
        usd_delta = 0.0
        brent_delta = _canon_float("BRENT", "change_percent")
        brent_level = _canon_float("BRENT", "value")
        natgas_delta = 0.0
        for quote in briefing.market_setup.macro_quotes:
            text = f"{quote.display_name} {quote.symbol}".lower()
            if ("wti" in text or "crude" in text) and (oil_level is None or oil_level <= 0):
                oil_delta = float(quote.change_percent or 0.0)
                oil_level = float(quote.current_price or 0.0)
            elif "brent" in text and (brent_level is None or brent_level <= 0):
                brent_delta = float(quote.change_percent or 0.0)
                brent_level = float(quote.current_price or 0.0)
            elif "natural gas" in text or "ng1:com" in text or "ng=f" in text:
                natgas_delta = float(quote.change_percent or 0.0)
            elif "gold" in text and (gold_delta is None or abs(gold_delta) < 1e-12):
                gold_delta = float(quote.change_percent or 0.0)
            elif "usd" in text or "dollar" in text or "dxy" in text:
                usd_delta = float(quote.change_percent or 0.0)
        # Prefer FRED commodity strip for oil level (more reliable than live quote)
        for pt in briefing.commodity_strip:
            key = (pt.name or pt.series_id or "").upper()
            if "WTI" in key or "DCOILWTICO" in key:
                if oil_level is None:
                    oil_level = float(pt.value or 0.0)
                break

        safe_haven_strength = max(0.0, float(gold_delta or 0.0)) + max(0.0, usd_delta)
        geo_terms = ("iran", "israel", "hormuz", "blockade", "missile", "ceasefire", "sanction", "shipping", "war", "attack", "strike", "invasion")
        events = briefing.global_news + briefing.top_themes

        # Pass density=None when no events; compute_geo_risk_level will label signal as stale
        density: float | None
        if not events:
            density = None
            has_geo_headlines = False
        else:
            geo_hits = 0
            has_geo_headlines = False
            for event in events:
                etext = f"{event.title} {event.summary}".lower()
                if any(term in etext for term in geo_terms):
                    geo_hits += max(1, int(event.cluster_size or 1))
                    has_geo_headlines = True
            density = geo_hits / max(1.0, float(sum(max(1, int(evt.cluster_size or 1)) for evt in events)))
        recent_geo_context = self._recent_geo_headline_context(hours=18)

        raw_level, summary = compute_geo_risk_level(
            vix_level=vix_level,
            oil_delta_pct=oil_delta,
            safe_haven_strength=safe_haven_strength,
            news_keyword_density=density,
        )
        level = raw_level

        # Oil move classification thresholds
        _OIL_SPIKE_THRESHOLD = 3.0    # abs % for "spiking"
        _OIL_ELEVATED_THRESHOLD = 1.0  # abs % for "elevated" / "under pressure"

        def _oil_move_label(delta: float) -> str:
            """Return a calibrated oil-move label based on magnitude."""
            abs_delta = abs(delta)
            if abs_delta > _OIL_SPIKE_THRESHOLD:
                return "oil spiking"
            if abs_delta > _OIL_ELEVATED_THRESHOLD:
                return "oil elevated" if delta > 0 else "oil under pressure"
            return "oil steady"

        # Floor rule: oil elevated + active geo headlines → at least ELEVATED, never LOW/MODERATE
        oil_is_elevated = (
            (oil_level is not None and oil_level > 90)
            or (oil_delta is not None and oil_delta >= 2.0)
        )
        if has_geo_headlines and oil_is_elevated:
            floor = "ELEVATED"
            try:
                if _GEO_LEVEL_ORDER.index(level) < _GEO_LEVEL_ORDER.index(floor):
                    level = floor
                    # Build a clean replacement summary — do not concatenate the raw model summary
                    vix_available_here = vix_level is not None and vix_level > 0
                    if vix_available_here:
                        vix_note = f"VIX {vix_level:.1f}"
                    else:
                        vix_note = "VIX unavailable"
                    haven_note = "haven neutral" if safe_haven_strength < 0.5 else f"haven +{safe_haven_strength:.2f}"
                    density_note = f"density {density:.2f}" if density is not None else "headline signal stale"
                    oil_label = _oil_move_label(oil_delta)
                    # Distinguish confirmed vs incomplete market confirmation
                    market_confirmed = (
                        vix_available_here and (vix_level or 0.0) >= 20.0 and oil_delta >= _OIL_ELEVATED_THRESHOLD
                    )
                    if market_confirmed:
                        confirmation_note = f"{vix_note} and {haven_note} confirm elevated stress."
                    elif not vix_available_here and safe_haven_strength < 0.5 and abs(oil_delta) < _OIL_ELEVATED_THRESHOLD:
                        confirmation_note = (
                            "Geo headline risk elevated; market confirmation incomplete "
                            f"({vix_note}, haven demand neutral)."
                        )
                    elif vix_available_here and (vix_level or 0.0) < 20.0:
                        confirmation_note = "Market signals are not yet confirming broader stress."
                    else:
                        confirmation_note = f"{vix_note} and {haven_note} do not confirm broad panic."
                    summary = (
                        f"Geo risk ELEVATED: {oil_label} at {oil_level:.0f} USD/bbl ({oil_delta:+.2f}%) "
                        f"with active Middle East/geopolitical headlines; "
                        f"{confirmation_note} "
                        f"Inputs: {vix_note}, oil {oil_delta:+.2f}%, {haven_note}, {density_note}."
                    )
            except ValueError:
                pass  # level not in order list (e.g. N/A) — leave unchanged

        # Nuance rule: stale headlines + rising VIX + weak Europe should not read as outright LOW.
        if level == "LOW":
            europe_moves = [
                float(q.change_percent or 0.0)
                for q in briefing.market_setup.index_quotes
                if any(tok in (q.display_name or q.symbol or "").upper() for tok in ("STOXX", "FTSE", "DAX", "CAC", "IBEX"))
            ]
            europe_avg = (sum(europe_moves) / len(europe_moves)) if europe_moves else 0.0
            vix_display = f"{vix_level:.2f}" if vix_level is not None else "n/a"
            vix_rising = (vix_level or 0.0) >= 17.0 and any(
                "VIX" in (q.display_name or q.symbol or "").upper() and float(q.change_percent or 0.0) >= 2.0
                for q in briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes
            )
            if density is None and vix_rising and europe_avg <= -0.5:
                level = "LOW-TO-MODERATE"
                summary = (
                    f"Geo risk LOW-TO-MODERATE, market-contained: VIX {vix_display} is rising and Europe is weak ({europe_avg:+.2f}%), "
                    f"but oil ({oil_delta:+.2f}%) and haven signals do not confirm a fresh shock."
                )
            energy_channel_active = (
                (oil_level is not None and oil_level > 90)
                or (brent_level is not None and brent_level > 100)
                or (oil_delta is not None and oil_delta >= 1.0)
                or (brent_delta is not None and brent_delta >= 1.0)
                or natgas_delta >= 4.0
            )
            if recent_geo_context and energy_channel_active and (vix_rising or europe_avg <= -0.4):
                level = "LOW-TO-MODERATE"
                brent_note = (
                    f"Brent {float(brent_delta):+.2f}%"
                    if (brent_level is not None and brent_level > 0) or (brent_delta is not None and abs(brent_delta) > 1e-12)
                    else "Brent unavailable"
                )
                oil_note = f"WTI {float(oil_delta):+.2f}%" if oil_delta is not None else "WTI unavailable"
                summary = (
                    f"Geo risk LOW-TO-MODERATE, market-contained: {oil_note} / {brent_note}"
                    f"{' / NatGas ' + format(natgas_delta, '+.2f') + '%' if natgas_delta else ''}, "
                    f"VIX {vix_display}, gold {float(gold_delta or 0.0):+.2f}%, with recent Middle East/Hormuz context still active."
                )

        return level, raw_level, summary

    def _recent_geo_headline_context(self, *, hours: int = 18) -> bool:
        """Geo lookback memory so stale cycle density doesn't erase same-day risk context."""
        try:
            from app.db.models import NormalisedEvent as NormalisedEventRow
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
            geo_terms = ("iran", "israel", "hormuz", "blockade", "missile", "ceasefire", "sanction", "shipping", "war", "attack", "strike", "invasion")
            with get_session() as session:
                rows = (
                    session.query(NormalisedEventRow.title, NormalisedEventRow.summary)
                    .filter(NormalisedEventRow.published_at.isnot(None))
                    .filter(NormalisedEventRow.published_at >= cutoff)
                    .order_by(NormalisedEventRow.published_at.desc(), NormalisedEventRow.id.desc())
                    .limit(200)
                    .all()
                )
            for title, summary in rows:
                text = f"{title or ''} {summary or ''}".lower()
                if any(term in text for term in geo_terms):
                    return True
        except Exception:
            return False
        return False

    def _build_earnings_relevance(self, earnings: list) -> dict[str, str]:
        portfolio = {holding.symbol.upper() for holding in self.profile.portfolio_holdings}
        watchlist = {symbol.upper() for symbol in self.profile.all_watchlist_tickers}
        relevance: dict[str, str] = {}
        for item in earnings:
            symbol = str(getattr(item, "symbol", "") or "").upper()
            if not symbol:
                continue
            if symbol in portfolio:
                relevance[symbol] = "portfolio"
            elif symbol in watchlist:
                relevance[symbol] = "watchlist"
        return relevance

    def _build_portfolio_focus(self, scored_events: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Select concise portfolio-first items for the morning briefing."""
        if not self.profile.has_portfolio:
            return []

        portfolio_symbols = set(self.profile.portfolio_symbols)
        concentrated_sectors = {
            sector
            for sector, weight in self.profile.portfolio_sector_weights.items()
            if weight >= 0.2
        }
        selected: list[NormalisedEvent] = []
        seen_tracking_ids: set[str] = set()

        def _push(event: NormalisedEvent) -> None:
            tracking_id = event.cluster_id or event.content_hash or event.event_id
            if tracking_id in seen_tracking_ids:
                return
            seen_tracking_ids.add(tracking_id)
            selected.append(event)

        for event in scored_events:
            if len(selected) >= MAX_PORTFOLIO_FOCUS:
                break
            if not is_actionable_event(event):
                continue
            if should_suppress_low_signal(event):
                continue
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=True):
                _push(self._apply_news_hygiene(event, section="portfolio_watchlist_themes"))

        for event in scored_events:
            if len(selected) >= MAX_PORTFOLIO_FOCUS:
                break
            if not is_actionable_event(event):
                continue
            if should_suppress_low_signal(event):
                continue
            if event.final_score < 0.62:
                continue
            if not (concentrated_sectors and any(sector in concentrated_sectors for sector in event.sectors)):
                continue
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=False):
                _push(self._apply_news_hygiene(event, section="portfolio_watchlist_themes"))

        return selected

    @staticmethod
    def _normalise_text(value: str) -> str:
        return (
            (value or "")
            .lower()
            .replace("’", "'")
            .replace("‘", "'")
            .replace("`", "'")
            .replace("—", "-")
            .replace("–", "-")
            .replace("\xa0", " ")
        )

    def _portfolio_focus_worthy(
        self,
        event: NormalisedEvent,
        portfolio_symbols: set[str],
        *,
        require_direct: bool,
    ) -> bool:
        score, has_direct, has_readthrough = self._portfolio_focus_editorial_score(
            event,
            portfolio_symbols,
        )
        if require_direct and not has_direct:
            return False
        if require_direct:
            return score >= PORTFOLIO_DIRECT_MIN_SCORE
        return has_readthrough and score >= PORTFOLIO_READTHROUGH_MIN_SCORE

    def _portfolio_focus_editorial_score(
        self,
        event: NormalisedEvent,
        portfolio_symbols: set[str],
    ) -> tuple[float, bool, bool]:
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        held_hits = [ticker for ticker in event.tickers if ticker in portfolio_symbols]
        has_direct = bool(held_hits)
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        has_readthrough = self._has_portfolio_readthrough_signal(event, text_lower)
        title_mentions_held = self._title_mentions_tickers_or_companies(title_lower, held_hits)

        # Held ticker appeared only indirectly (often in a side mention): reject
        # unless the event carries a hard catalyst.
        if has_direct and not title_mentions_held and not has_catalyst:
            return -99.0, has_direct, has_readthrough

        # Keep explicit low-value framing out of scarce PORTFOLIO FOCUS slots,
        # unless there is a genuinely material catalyst.
        if any(pattern in text_lower for pattern in PORTFOLIO_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return -99.0, has_direct, has_readthrough

        score = 0.0
        if has_direct:
            score += 1.8
        if title_mentions_held:
            score += 0.8
        if has_catalyst:
            score += 1.2
        if has_readthrough:
            score += 0.6
        if event.cluster_size >= 3:
            score += 0.2

        for pattern in PORTFOLIO_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.65
        if any(rx.search(title_lower) for rx in PORTFOLIO_SOFT_PENALTY_REGEXES):
            score -= 0.8
        if "?" in title_lower and not has_catalyst:
            score -= 0.35

        return score, has_direct, has_readthrough

    @staticmethod
    def _is_material_portfolio_catalyst(event: NormalisedEvent, text_lower: str) -> bool:
        if event.source == "sec_edgar":
            # Official filings still need materiality; suppress generic exhibit/admin filings.
            return any(term in text_lower for term in SEC_MATERIAL_FILING_TERMS)
        if event.event_type in PORTFOLIO_CATALYST_EVENT_TYPES:
            return True
        return any(term in text_lower for term in PORTFOLIO_CATALYST_TERMS)

    @staticmethod
    def _has_portfolio_readthrough_signal(event: NormalisedEvent, text_lower: str) -> bool:
        if event.event_type in {"macro_release", "fed_decision", "geopolitical", "regulatory"}:
            return True
        return any(term in text_lower for term in PORTFOLIO_READTHROUGH_TERMS)

    @staticmethod
    def _title_mentions_tickers_or_companies(title_lower: str, tickers: list[str]) -> bool:
        for ticker in tickers:
            if re.search(rf"\b{re.escape(ticker.lower())}\b", title_lower):
                return True
            company = company_name_for_ticker(ticker).lower()
            if company != ticker.lower() and company in title_lower:
                return True
        return False

    def _filter_watchlist_events(self, scored: list[NormalisedEvent]) -> list[NormalisedEvent]:
        """Filter events relevant to the user's watchlist."""
        watchlist_set = set(self.profile.all_watchlist_tickers)
        selected: list[NormalisedEvent] = []
        seen_tracking_ids: set[str] = set()
        for event in scored:
            if len(selected) >= self.rules.max_watchlist_events:
                break
            if not is_actionable_event(event):
                continue
            watchlist_hits = [ticker for ticker in event.tickers if ticker in watchlist_set]
            if not watchlist_hits:
                continue
            if not self._watchlist_event_worthy(event, watchlist_hits):
                continue

            tracking_id = event.cluster_id or event.content_hash or event.event_id
            if tracking_id in seen_tracking_ids:
                continue
            seen_tracking_ids.add(tracking_id)
            selected.append(self._apply_news_hygiene(event, section="portfolio_watchlist_themes"))
        return selected

    def _watchlist_event_worthy(
        self,
        event: NormalisedEvent,
        watchlist_hits: list[str],
    ) -> bool:
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        title_mentions_watchlist = self._title_mentions_tickers_or_companies(title_lower, watchlist_hits)

        if not title_mentions_watchlist and not has_catalyst:
            return False

        if any(pattern in text_lower for pattern in WATCHLIST_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return False

        score = 0.0
        score += 1.0  # direct watchlist hit
        if title_mentions_watchlist:
            score += 0.5
        if has_catalyst:
            score += 0.8
        if event.cluster_size >= 3:
            score += 0.2
        if event.source == "sec_edgar":
            score += 0.3

        for pattern in WATCHLIST_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.4
        if any(rx.search(title_lower) for rx in WATCHLIST_SOFT_PENALTY_REGEXES):
            score -= 0.6
        if "?" in title_lower and not has_catalyst:
            score -= 0.3

        return score >= WATCHLIST_MIN_EDITORIAL_SCORE

    def _is_editorially_trustworthy(
        self,
        event: NormalisedEvent,
        session_mode: str,
        *,
        section: str,
    ) -> bool:
        """Apply section-level trust gating for top themes and sector scan."""
        if event.raw_data is None:
            event.raw_data = {}
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        source_name = self._normalise_text(str(event.raw_data.get("source_name", "")))
        url_lower = self._normalise_text(event.url)
        is_weekend = session_mode in {"saturday", "sunday"}
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        has_readthrough = self._has_portfolio_readthrough_signal(event, text_lower)

        portfolio_symbols = {s.upper() for s in self.profile.portfolio_symbols}
        watchlist_symbols = {s.upper() for s in self.profile.all_watchlist_tickers}
        relevant_symbols = portfolio_symbols | watchlist_symbols
        has_relevant_ticker = any(t.upper() in relevant_symbols for t in event.tickers)

        article_type = classify_article_type(event.title, event.summary, event.url)
        source_quality = classify_source_quality(
            event.source,
            str(event.raw_data.get("source_name", "")),
            event.url,
        )
        source_tier, source_tier_label = classify_source_tier(
            event.source,
            str(event.raw_data.get("source_name", "")),
            event.url,
        )
        section_fit = classify_section_fit(event)
        event.raw_data["source_tier"] = source_tier
        event.raw_data["source_tier_label"] = source_tier_label
        event.raw_data["section_fit"] = section_fit
        trusted_source = source_quality in {"tier1_wire", "tier1_press", "sec_filing"}

        if is_clickbait_headline(event.title) and not has_catalyst:
            return False
        if source_tier >= 3 and not has_catalyst:
            return False

        if section == "top_themes":
            if section_fit == "global_macro_geo" and not has_relevant_ticker:
                return False
            # Hard-stop low-signal editorial formats unless they are directly relevant
            # and strongly corroborated by a trusted source.
            if article_type in {"listicle", "seo", "opinion"}:
                if not (has_relevant_ticker and has_catalyst and trusted_source):
                    return False
            if article_type == "preview" and not has_relevant_ticker:
                return False
            if source_quality == "blog" and not (has_relevant_ticker and has_catalyst):
                return False
            if (
                not has_relevant_ticker
                and not trusted_source
                and event.cluster_size < 3
                and not has_catalyst
            ):
                return False
            if event.event_type == "news_search" and not has_relevant_ticker and not has_catalyst:
                return False
        if section == "sector_scan":
            if section_fit not in {"sector_signals", "portfolio_watchlist"} and not has_catalyst:
                return False
            if article_type in {"preview", "opinion", "seo", "listicle"}:
                if not (has_catalyst and has_relevant_ticker and trusted_source):
                    return False
            if "awaiting verified operating details" in text_lower:
                return False

        if (
            is_low_quality_for_section(article_type, source_quality)
            and not has_relevant_ticker
            and not has_catalyst
        ):
            return False

        if (
            section == "top_themes"
            and not has_relevant_ticker
            and not has_catalyst
            and not has_readthrough
            and not any(term in text_lower for term in TRUST_MARKET_LINK_TERMS)
        ):
            return False

        if (
            section == "top_themes"
            and any(pattern in text_lower for pattern in TRUST_PERSONAL_FINANCE_PATTERNS)
            and not has_catalyst
        ):
            return False

        # Hard blocks for obvious low-value framing unless there is a hard catalyst.
        if any(pattern in text_lower for pattern in TRUST_HARD_BLOCK_PATTERNS) and not has_catalyst:
            return False

        score = 0.0
        if event.source == "sec_edgar":
            score += 1.2
        if event.factual_confidence_score >= 0.80:
            score += 0.8
        elif event.factual_confidence_score >= 0.65:
            score += 0.5
        else:
            score += 0.2
        if event.cluster_size >= 3:
            score += 0.35
        if has_catalyst:
            score += 0.8
        if has_readthrough:
            score += 0.35
        if event.final_score >= 0.75:
            score += 0.2

        for pattern in TRUST_SOFT_PENALTY_PATTERNS:
            if pattern in text_lower:
                score -= 0.45
        if any(rx.search(title_lower) for rx in TRUST_SOFT_PENALTY_REGEXES):
            score -= 0.6

        if is_weekend:
            if any(marker in source_name or marker in url_lower for marker in WEEKEND_LOW_TRUST_SOURCES):
                score -= 0.55
            if "?" in title_lower and not has_catalyst:
                score -= 0.25
            if (
                event.factual_confidence_score < 0.60
                and event.cluster_size <= 2
                and not has_catalyst
                and not has_readthrough
            ):
                return False

        threshold = TRUST_TOP_THEMES_MIN_SCORE if section == "top_themes" else TRUST_SECTOR_SCAN_MIN_SCORE
        return score >= threshold

    def _apply_news_hygiene(self, event: NormalisedEvent, *, section: str) -> NormalisedEvent:
        """Attach source-tier metadata + neutralized headline for user-facing sections."""
        evt = event.model_copy(deep=True)
        raw = dict(evt.raw_data or {})
        source_tier, source_tier_label = classify_source_tier(
            evt.source,
            str(raw.get("source_name", "")),
            evt.url,
        )
        raw["source_tier"] = source_tier
        raw["source_tier_label"] = source_tier_label
        raw["section_fit"] = classify_section_fit(evt)
        raw.setdefault("headline_original", evt.title)
        if is_clickbait_headline(evt.title):
            evt.title = neutralize_headline(
                evt.title,
                event_type=evt.event_type,
                ticker=(evt.tickers[0] if evt.tickers else ""),
            )
            raw["headline_neutralized"] = True
        else:
            raw["headline_neutralized"] = False
        if event_company_confidence(evt) < 0.75:
            raw["ticker_label_suppressed"] = True
        raw["briefing_section"] = section
        evt.raw_data = raw
        return evt

    def _dedupe_cross_section_events(self, briefing: MorningBriefing) -> None:
        """Keep a story from repeating across multiple morning sections."""
        seen_keys: set[str] = set()
        briefing.global_news = self._dedupe_event_list(briefing.global_news, seen_keys)
        briefing.portfolio_focus = self._dedupe_event_list(briefing.portfolio_focus, seen_keys)
        briefing.top_themes = self._dedupe_event_list(briefing.top_themes, seen_keys)

        for snapshot in briefing.sector_scan:
            snapshot.top_events = self._dedupe_event_list(snapshot.top_events, seen_keys)

        briefing.watchlist_events = self._dedupe_event_list(briefing.watchlist_events, seen_keys)

    def _dedupe_event_list(
        self,
        events: list[NormalisedEvent],
        seen_keys: set[str],
    ) -> list[NormalisedEvent]:
        deduped: list[NormalisedEvent] = []
        for event in events:
            key = self._event_dedupe_key(event)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(event)
        return deduped

    def _event_dedupe_key(self, event: NormalisedEvent) -> str:
        if event.cluster_id:
            return f"cluster:{event.cluster_id}"
        if event.content_hash:
            return f"hash:{event.content_hash}"
        title_key = self._normalise_text(event.title).strip()
        return f"title:{title_key}"

    def _fetch_watchlist_quotes(self) -> list:
        """Fetch quotes for primary watchlist tickers."""
        tickers = self.profile.watchlist_primary[:10]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)

    def _fetch_portfolio_quotes(self) -> list:
        """Fetch quotes for top held positions for richer delivery surfaces."""
        tickers = self.profile.portfolio_symbols[:6]
        if not tickers:
            return []
        return self.market_svc.get_quotes(tickers)

    def _should_build_charts(self) -> bool:
        """Avoid extra work unless at least one delivery/debug surface can use charts."""
        if not getattr(self.settings, "enable_charts", True):
            return False
        return bool(
            self.settings.email_configured
            or getattr(self.settings, "telegram_send_charts", False)
            or self.settings.show_output
        )

    def _provider_health_summary(self) -> str:
        hub = getattr(self.news_svc, "global_hub", None)
        stats = dict(getattr(hub, "last_run_stats", {}) or {})
        contributions = dict(stats.get("provider_contributions", {}) or {})
        if not contributions:
            return ""
        fetched_total = int(stats.get("fetched_total", 0) or 0)
        deduped_total = int(stats.get("deduped_total", 0) or 0)
        unavailable = sorted(name for name, count in contributions.items() if int(count or 0) <= 0)
        active = sorted(name for name, count in contributions.items() if int(count or 0) > 0)
        notes: list[str] = []
        if unavailable:
            notes.append("Unavailable: " + ", ".join(unavailable[:3]))
        if active:
            notes.append("Core providers active")
        if fetched_total <= 0:
            notes.append("Provider failures: raw fetch 0")
        elif deduped_total <= 0:
            notes.append("Fetched but deduped to 0")
        return " · ".join(notes) if notes else ""
