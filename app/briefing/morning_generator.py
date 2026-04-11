"""Morning briefing generator: orchestrates data fetching, processing,
and assembly of the pre-market morning briefing.
"""

from __future__ import annotations

from datetime import datetime
import re

from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.data_sources.news_data import NewsDataService
from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.theme_builder import build_top_themes
from app.logger import get_logger
from app.personalization.delivery_rules import load_alert_rules
from app.personalization.user_profile import UserProfile
from app.processing.pipeline import is_actionable_event, process_event_stream
from app.schemas.briefings import MarketSetup, MorningBriefing, session_mode_for
from app.schemas.events import NormalisedEvent, SectorSnapshot
from app.settings import Settings
from app.universe.sector_universe import SectorUniverse
from app.universe.ticker_metadata import company_name_for_ticker

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

    def generate(self) -> MorningBriefing:
        """Generate the full morning briefing."""
        logger.info("Generating morning briefing...")
        now = datetime.now()
        briefing = MorningBriefing(
            generated_at=now,
            session_mode=session_mode_for(now),
        )

        # 1. Market setup (quotes for indices + macro instruments)
        briefing.market_setup = self._build_market_setup()

        # 2. Macro context from FRED
        briefing.macro_context = self.macro_svc.get_morning_macro()
        ten_y, two_y = self.macro_svc.get_treasury_yields()
        briefing.market_setup.treasury_10y = ten_y
        briefing.market_setup.treasury_2y = two_y

        # 3. Fetch all news/events
        all_events = self.news_svc.fetch_all(
            watchlist=self.profile.all_watchlist_tickers[:20]  # limit API calls
        )
        briefing.events_fetched = len(all_events)

        # 4. Process, cluster, classify, and rank
        # Sector enrichment runs inside the pipeline after ticker resolution
        # so sector tags reflect the cleaned ticker set, not spurious feeds.
        scored = process_event_stream(
            all_events,
            self.profile,
            self.settings,
            sector_lookup=self.universe.sectors_for_ticker,
        )
        briefing.events_after_dedup = len(scored)

        # 7. Assemble sections
        briefing.top_themes = self._build_top_themes(scored, briefing.session_mode)
        briefing.portfolio_focus = self._build_portfolio_focus(scored)
        briefing.sector_scan = self._build_sector_scan(scored, briefing.session_mode)
        briefing.earnings_calendar = self._fetch_earnings()
        briefing.watchlist_events = self._filter_watchlist_events(scored)
        briefing.watchlist_quotes = self._fetch_watchlist_quotes()
        briefing.portfolio_quotes = self._fetch_portfolio_quotes()
        self._dedupe_cross_section_events(briefing)
        if self._should_build_charts():
            briefing.chart_assets = MorningChartBuilder(
                profile=self.profile,
                market_data=self.market_svc,
            ).build(briefing)
        sent_ids: set[str] = set()
        for event in briefing.top_themes + briefing.watchlist_events + briefing.portfolio_focus:
            sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        for snapshot in briefing.sector_scan:
            for event in snapshot.top_events:
                sent_ids.add(event.cluster_id or event.content_hash or event.event_id)
        briefing.events_sent = len(sent_ids)

        logger.info(
            "Morning briefing ready: %d fetched, %d deduped, %d themes, %d sectors, %d watchlist",
            briefing.events_fetched,
            briefing.events_after_dedup,
            len(briefing.top_themes),
            len(briefing.sector_scan),
            len(briefing.watchlist_events),
        )
        return briefing

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

        # Macro instrument quotes (gold, oil, USD, BTC)
        macro_symbols = self.universe.all_macro_symbols
        if macro_symbols:
            quotes = self.market_svc.get_quotes(macro_symbols)
            name_map = {m.symbol: m.display for m in self.universe.macro_instruments}
            for q in quotes:
                q.display_name = name_map.get(q.symbol, q.symbol)
            setup.macro_quotes = quotes

        return setup

    def _build_top_themes(
        self,
        scored_events: list[NormalisedEvent],
        session_mode: str,
    ) -> list[NormalisedEvent]:
        """Build top themes with an editorial trust gate."""
        return build_top_themes(
            scored_events,
            max_themes=self.rules.max_themes,
            editorial_gate=lambda evt: self._is_editorially_trustworthy(
                evt,
                session_mode,
                section="top_themes",
            ),
        )

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
                if not self._is_editorially_trustworthy(event, session_mode, section="sector_scan"):
                    continue
                sector_events.append(event)
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
        """Fetch today's earnings calendar."""
        if not self.news_svc.finnhub or not self.news_svc.finnhub.is_configured():
            return []
        return self.news_svc.finnhub.get_earnings_calendar(days_ahead=1)[: self.rules.max_earnings]

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
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=True):
                _push(event)

        for event in scored_events:
            if len(selected) >= MAX_PORTFOLIO_FOCUS:
                break
            if not is_actionable_event(event):
                continue
            if event.final_score < 0.62:
                continue
            if not (concentrated_sectors and any(sector in concentrated_sectors for sector in event.sectors)):
                continue
            if self._portfolio_focus_worthy(event, portfolio_symbols, require_direct=False):
                _push(event)

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
            return True
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
            selected.append(event)
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
        title_lower = self._normalise_text(event.title)
        text_lower = self._normalise_text(f"{event.title} {event.summary}")
        source_name = self._normalise_text(str(event.raw_data.get("source_name", "")))
        url_lower = self._normalise_text(event.url)
        is_weekend = session_mode in {"saturday", "sunday"}
        has_catalyst = self._is_material_portfolio_catalyst(event, text_lower)
        has_readthrough = self._has_portfolio_readthrough_signal(event, text_lower)

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

    def _dedupe_cross_section_events(self, briefing: MorningBriefing) -> None:
        """Keep a story from repeating across multiple morning sections."""
        seen_keys: set[str] = set()
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
