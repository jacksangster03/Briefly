"""Telegram-optimised message formatting for briefings and alerts.

All output is plain text (Telegram HTML parse mode). Optimised for
phone reading: short sections, clear labels, numbers first, sparse emoji.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.processing.cleaners import truncate
from app.briefing.global_news_selector import build_market_relevance_note
from app.briefing.quality_guard import BriefingQualityGuard, flags_from_briefing
from app.processing.article_quality import classify_article_type, event_company_confidence
from app.briefing.templates import (
    MAX_EARNINGS_DISPLAY,
    MAX_INTRADAY_EVENTS,
    MAX_SECTOR_EVENTS,
    MAX_THEMES,
    MAX_WATCHLIST_EVENTS,
    SECTION_HEADERS,
    TELEGRAM_MAX_LENGTH,
    format_change,
    format_context_price,
    format_compact_price,
    format_compact_price_with_level,
    format_price_line,
)
from app.schemas.briefings import (
    BreakingAlert,
    IntradayUpdate,
    MorningBriefing,
    session_mode_for,
)
from app.healthcare.schemas import HealthcareBriefingSection
from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    NormalisedEvent,
    QuoteData,
    SectorSnapshot,
)
from app.universe.ticker_metadata import company_name_for_ticker, format_company_ticker, format_company_ticker_list
from app.cadence.exchange_calendar import exchange_for_symbol, is_exchange_closed
from app.briefing.move_context import (
    ASSET_TYPE_COMMODITY,
    ASSET_TYPE_EQUITY_INDEX,
    ASSET_TYPE_FX,
    ASSET_TYPE_VOLATILITY_INDEX,
    compute_yield_context,
    format_move_context_line,
    format_watchlist_move_label,
    format_yield_context_line,
    move_context_from_quote,
)
from app.briefing.session_tape import (
    build_session_tape,
    build_rates_macro_tape,
    format_session_tape_text,
    format_watchlist_tape_compact,
)


def format_trigger_line(
    label: str,
    metric_value: float | None,
    threshold: float,
    direction: str,
    consequence: str,
) -> str:
    """Build a trigger line that reflects the current value relative to threshold.

    Parameters
    ----------
    label:
        Human-readable metric label, e.g. "US 10Y".
    metric_value:
        Current value. If None, the trigger line is suppressed entirely.
    threshold:
        The numeric threshold level.
    direction:
        "above" or "below".
    consequence:
        What the breach implies, e.g. "reinforce rates pressure".

    Returns
    -------
    str
        A trigger line string, or empty string when metric_value is None.
    """
    if metric_value is None:
        return ""
    if direction == "above":
        if metric_value >= threshold:
            return f"{label} is above {threshold:.2f}, {consequence} (now {metric_value:.2f})."
        return f"{label} above {threshold:.2f} would {consequence} (now {metric_value:.2f})."
    # direction == "below"
    if metric_value <= threshold:
        return f"{label} is below {threshold:.2f}, {consequence} (now {metric_value:.2f})."
    return f"{label} below {threshold:.2f} would {consequence} (now {metric_value:.2f})."


_VIX_TOKENS: frozenset[str] = frozenset({"VIX", "^VIX", "UVXY", "SVXY"})
_COMMODITY_TOKENS: frozenset[str] = frozenset({
    "GOLD", "GLD", "GC", "OIL", "WTI", "CRUDE", "CL", "NG",
    "SILVER", "SLV", "SI", "COPPER", "HG", "PLATINUM", "NATGAS",
})
_FX_TOKENS: frozenset[str] = frozenset({
    "DXY", "USDX", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "USDCHF", "USDCAD", "DOLLAR INDEX",
})

_TREASURY_YIELD_TOKENS: frozenset[str] = frozenset({
    "10Y US TREASURY", "2Y US TREASURY", "US10Y", "US2Y", "DGS10", "DGS2",
    "^TNX", "^TYX", "^FVX", "TREASURY YIELD", "GOVT YIELD",
    "10Y YIELD", "2Y YIELD", "10-YEAR", "2-YEAR",
})


def is_treasury_yield_quote(q) -> bool:
    """Return True if this QuoteData represents a Treasury yield (not a price instrument)."""
    key = f"{(q.symbol or '').upper()} {(q.display_name or '').upper()}"
    return any(token in key for token in _TREASURY_YIELD_TOKENS)


def _asset_type_for_quote(q) -> str:
    """Infer the move_context asset type from a QuoteData symbol/display_name."""
    sym = (q.symbol or "").upper()
    name = (q.display_name or "").upper()
    key = f"{sym} {name}"
    if any(t in key for t in _VIX_TOKENS) or "VIX" in key:
        return ASSET_TYPE_VOLATILITY_INDEX
    if any(t in key for t in _COMMODITY_TOKENS):
        return ASSET_TYPE_COMMODITY
    if any(t in key for t in _FX_TOKENS):
        return ASSET_TYPE_FX
    return ASSET_TYPE_EQUITY_INDEX


class TelegramFormatter:
    """Formats briefing objects into Telegram-ready text messages."""

    def __init__(self, timezone_name: str = "UTC"):
        try:
            self.local_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.local_tz = ZoneInfo("UTC")

    def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
        """Format a complete morning briefing. Returns a list of messages
        (split if exceeding Telegram's 4096 char limit).
        """
        sections = []
        is_weekend = briefing.session_mode in {"saturday", "sunday"}

        # Header
        date_str = briefing.generated_at.strftime("%a %d %b %Y")
        if briefing.session_title and briefing.session_key not in {"morning", ""}:
            header = briefing.session_title.upper()
        elif briefing.session_mode == "saturday":
            header = SECTION_HEADERS["weekend_title_saturday"]
        elif briefing.session_mode == "sunday":
            header = SECTION_HEADERS["weekend_title_sunday"]
        else:
            header = SECTION_HEADERS["morning_title"]
        tldr = briefing.dominant_tape_driver or briefing.market_setup_analysis or ""
        if tldr:
            tldr = tldr[:130] + ("…" if len(tldr) > 130 else "")
        sections.append(
            f"<b>{header}</b>\n{date_str}"
            + (f"\n<i>{tldr}</i>" if tldr else "")
        )
        if briefing.market_data_outage:
            sections.append(
                "<b>DATA OUTAGE / PROVIDER DEGRADED</b>\n"
                "Market data unavailable; no directional read generated. "
                "Provider data outage; see diagnostics."
            )

        session_key = (briefing.session_key or "morning").lower()
        is_morning = session_key == "morning"
        is_midday = session_key == "europe_midday"
        is_preopen = session_key == "us_pre_open"
        is_intraday = session_key == "us_intraday_risk"
        is_into_close = session_key == "into_close"
        is_closing = session_key == "closing_wrap"
        is_intraday_like = is_intraday or is_into_close

        if is_closing:
            verdict = briefing.market_setup_analysis or briefing.dominant_tape_driver or "Session closed with mixed cross-asset signals."
            sections.append("\n".join(["<b>DAY VERDICT</b>", truncate(verdict, 240)]))
            tags = [str(tag).replace("_", " ").upper() for tag in (briefing.market_setup_signal_tags or [])[:4]]
            driver_line = " | ".join(tags) if tags else "No single driver tag dominated; cross-asset context remained mixed."
            sections.append("\n".join(["<b>CONFIRMED DRIVERS</b>", "- " + driver_line]))

        if not is_morning:
            what_changed = self._format_what_changed(
                briefing.what_changed_lines,
                header=briefing.what_changed_header or "WHAT CHANGED",
            )
            if what_changed:
                sections.append(what_changed)
        if briefing.data_basis_lines:
            basis_lines = [f"- {line}" for line in briefing.data_basis_lines[:5]]
            sections.append("\n".join(["<b>DATA BASIS</b>"] + basis_lines))

        # Rates & Macro Tape: compact header block before macro policy watch
        rates_tape = build_rates_macro_tape(briefing)
        if rates_tape:
            sections.append(rates_tape)

        if briefing.macro_policy_watch:
            watch_lines = [
                str(line).strip()
                for line in str(briefing.macro_policy_watch).splitlines()
                if str(line).strip()
            ]
            if watch_lines and watch_lines[0].upper() == "MACRO POLICY WATCH":
                watch_lines = watch_lines[1:]
            sections.append(
                "\n".join(
                    [
                        "<b>MACRO POLICY WATCH</b>",
                        truncate("\n".join(watch_lines) if watch_lines else str(briefing.macro_policy_watch), 320),
                    ]
                )
            )
        clock_block = self._format_market_clock(briefing.market_clock_context or {})
        if clock_block:
            sections.append(clock_block)

        setup = self._format_market_setup(briefing) if (is_morning or is_preopen or is_closing) else self._format_session_snapshot(briefing)
        if setup:
            sections.append(setup)

        if is_morning or is_preopen or is_closing:
            macro = self._format_macro(briefing.macro_context)
            if macro:
                sections.append(macro)

        if is_morning or is_preopen or is_closing:
            commodities = self._format_commodity_strip(briefing.commodity_strip)
            if commodities:
                sections.append(commodities)

        # FX & Dollar Pulse section (deterministic, session-aware)
        fx_section = getattr(briefing, "fx_pulse_section", "") or ""
        if fx_section:
            sections.append(self._format_fx_section(fx_section, session_key))

        # Session Tape Recap blocks (US cash / Europe cash / watchlist)
        all_quotes = (
            list(getattr(briefing.market_setup, "index_quotes", []) or [])
            + list(getattr(briefing.market_setup, "macro_quotes", []) or [])
        )
        if is_closing or is_into_close:
            us_tape = build_session_tape(all_quotes, session_key, section="us_cash")
            us_tape_text = format_session_tape_text(us_tape, "us_cash", session_key)
            if us_tape_text:
                sections.append(us_tape_text)
            eu_tape = build_session_tape(all_quotes, session_key, section="europe_cash")
            eu_tape_text = format_session_tape_text(eu_tape, "europe_cash", session_key)
            if eu_tape_text:
                sections.append(eu_tape_text)
        elif is_midday:
            eu_tape = build_session_tape(all_quotes, session_key, section="europe_cash")
            eu_tape_text = format_session_tape_text(eu_tape, "europe_cash", session_key)
            if eu_tape_text:
                sections.append(eu_tape_text)
        # Watchlist session tape (compact, only when intraday OHLC available)
        watchlist_quotes = list(getattr(briefing, "watchlist_quotes", []) or [])
        if watchlist_quotes and (is_closing or is_into_close or is_intraday):
            wl_tape = build_session_tape(watchlist_quotes, session_key, section="watchlist")
            wl_tape_text = format_watchlist_tape_compact(wl_tape)
            if wl_tape_text:
                sections.append(wl_tape_text)

        show_regional = is_morning or is_midday or is_preopen or is_closing
        if not show_regional and briefing.regional_skew_summary:
            skew_lower = briefing.regional_skew_summary.lower()
            show_regional = any(token in skew_lower for token in ("split", "diverg", "regional"))
        regional = self._format_regional_lens(briefing.regional_lens, briefing.regional_skew_summary) if show_regional else ""
        if regional:
            sections.append(regional)

        impact_header = SECTION_HEADERS["portfolio_impact"]
        if is_intraday or is_into_close:
            impact_header = "PORTFOLIO CHECK"
        elif is_closing:
            impact_header = "PORTFOLIO ATTRIBUTION"
        elif session_key == "saturday_weekend_briefing":
            impact_header = "PORTFOLIO CLOSE READ"
        elif session_key == "sunday_weekend_watch":
            impact_header = "PORTFOLIO WEEKEND READ"
        impact = self._format_portfolio_impact(
            briefing.portfolio_impact_bullets,
            briefing.portfolio_action_posture,
            briefing.regime_context,
            briefing.positioning_alignment,
            briefing.geo_risk_level,
            briefing.geo_risk_summary,
            briefing.regime_shift,
            heading=impact_header,
        )
        if impact:
            sections.append(impact)

        trigger_block = self._format_watch_triggers(briefing)
        if trigger_block:
            sections.append(trigger_block)

        if is_morning or is_preopen or is_closing:
            global_news = self._format_global_news(briefing.global_news)
            if global_news:
                sections.append(global_news)
        elif not briefing.global_news:
            if briefing.news_data_outage:
                sections.append(
                    "\n".join(
                        [
                            f"<b>{SECTION_HEADERS['global_news']}</b>",
                            "News scan returned no usable items; provider diagnostics required.",
                        ]
                    )
                )
            else:
                since = self._since_label_from_header(briefing.what_changed_header)
                weekend_elevated_geo = (
                    is_weekend
                    and str(briefing.geo_risk_level or "").strip().lower()
                    in {"elevated", "high", "severe"}
                )
                empty_line = (
                    "No new material headlines since the last weekend scan; existing risk context remains active"
                    if weekend_elevated_geo
                    else (
                        f"No material new headlines since {since}."
                        if since
                        else "No material new headlines this session."
                    )
                )
                sections.append(
                    "\n".join(
                        [
                            f"<b>{SECTION_HEADERS['global_news']}</b>",
                            empty_line,
                        ]
                    )
                )

        themes = self._format_themes_for_mode(briefing.top_themes, briefing.session_mode)
        if themes and (is_morning or is_preopen or is_closing):
            sections.append(themes)

        portfolio_focus = self._format_portfolio_focus(briefing.portfolio_focus)
        if portfolio_focus and (is_morning or is_preopen or is_closing):
            sections.append(portfolio_focus)

        healthcare = self._format_healthcare_intelligence(briefing.healthcare_intelligence, session_key=session_key)
        if healthcare:
            sections.append(healthcare)

        if is_weekend:
            week_ahead = self._format_week_ahead(briefing)
            if week_ahead:
                sections.append(week_ahead)

        if is_morning or is_closing:
            sector = self._format_sector_scan(briefing.sector_scan, briefing.session_mode)
            if sector:
                sections.append(sector)

        earnings = self._format_earnings(briefing.earnings_calendar, briefing.earnings_relevance)
        if earnings and (is_morning or is_preopen or is_closing):
            sections.append(earnings)

        # Watchlist
        watchlist = self._format_watchlist(
            briefing,
            briefing.watchlist_events,
            briefing.watchlist_quotes,
            briefing.session_mode,
            dominant_driver=briefing.dominant_tape_driver,
            top_themes=briefing.top_themes,
        )
        if watchlist:
            sections.append(watchlist)

        # Footer
        footer_line = (
            f"{SECTION_HEADERS['footer']} | "
            f"{briefing.events_fetched} fetched, "
            f"{briefing.events_after_dedup} unique, "
            f"{briefing.events_sent} sent"
        )
        if briefing.news_data_outage:
            footer_line += " | provider outage (raw fetch 0)"
        elif briefing.news_raw_fetched > 0 and briefing.events_fetched <= 0:
            footer_line += f" | {briefing.news_raw_fetched} raw fetched, 0 selected after filters"
        sections.append(f"<i>{footer_line}</i>")
        provider_health = (briefing.data_freshness or {}).get("Provider Health", "").strip()
        if provider_health:
            note = self._provider_health_note(provider_health)
            if note:
                sections.append(f"<i>{note}</i>")

        sections = self._apply_quality_guards(sections, briefing=briefing)
        full_text = "\n\n".join(sections)
        return self._split_message(full_text)

    @staticmethod
    def _format_fx_section(fx_text: str, session: str) -> str:
        """Wrap the FX pulse text in an appropriate section header.

        Only inserts a full header block for morning and closing sessions;
        intraday sessions receive a compact inline line.
        """
        if not fx_text or not fx_text.strip():
            return ""
        session = (session or "morning").lower().strip()
        _intraday_like = session in {"us_intraday_risk", "into_close"}
        if _intraday_like:
            # One-liner: no bold header, just italic inline
            return f"<i>{fx_text.strip()}</i>"
        # Short block (midday, pre-open) and full block (morning, closing):
        # use a bold header only for the full block sessions
        _full_sessions = {"morning", "closing_wrap", "saturday_weekend_briefing", "sunday_weekend_watch"}
        if session in _full_sessions:
            lines = [line.strip() for line in fx_text.strip().splitlines() if line.strip()]
            if lines:
                header = f"<b>{lines[0]}</b>"
                rest = "\n".join(lines[1:])
                return f"{header}\n{rest}" if rest else header
            return ""
        # Short block: no header, just the text
        return fx_text.strip()

    def _format_market_clock(self, ctx: dict) -> str:
        if not ctx:
            return ""
        open_now = ctx.get("open_now") or []
        recently_closed = ctx.get("recently_closed") or []
        opening_next = ctx.get("opening_next") or []
        open_src = str(ctx.get("open_now_source") or "computed")
        closed_src = str(ctx.get("recently_closed_source") or "computed")
        next_src = str(ctx.get("opening_next_source") or "computed")
        focus = (ctx.get("focus") or "").strip()
        lines = ["<b>MARKET CLOCK</b>"]
        if open_now:
            label = "Open now" if open_src == "computed" else "Open now context"
            lines.append(f"{label}: {', '.join(str(x) for x in open_now[:4])}")
        if recently_closed:
            label = "Recently closed" if closed_src == "computed" else "Recently closed context"
            lines.append(f"{label}: {', '.join(str(x) for x in recently_closed[:4])}")
        if opening_next:
            label = "Opening next" if next_src == "computed" else "Opening next context"
            lines.append(f"{label}: {', '.join(str(x) for x in opening_next[:4])}")
        if focus:
            lines.append(f"Session focus: {focus}")
        return "\n".join(lines)

    @staticmethod
    def _since_label_from_header(header: str) -> str:
        text = (header or "").strip()
        token = "WHAT CHANGED SINCE "
        if text.upper().startswith(token):
            raw = text[len(token):].strip().title()
            return (
                raw.replace("Us ", "US ")
                .replace(" Us", " US")
                .replace("Uk ", "UK ")
                .replace(" Ema", " EMA")
                .replace(" Fda", " FDA")
            )
        return ""

    @staticmethod
    def _format_what_changed(lines: list[str], header: str = "WHAT CHANGED") -> str:
        compact = [str(line).strip() for line in (lines or []) if str(line).strip()]
        if not compact:
            return ""
        title = (header or "WHAT CHANGED").strip()
        return "\n".join([f"<b>{title}</b>"] + [f"- {line}" for line in compact[:6]])

    def _format_watch_triggers(self, briefing: MorningBriefing) -> str:
        session_key = (briefing.session_key or "morning").lower()
        if session_key == "morning":
            return ""
        if session_key in {"saturday_weekend_briefing", "sunday_weekend_watch"}:
            header = "MONDAY WATCHPOINTS"
        elif session_key == "closing_wrap":
            header = "TOMORROW SETUP"
        elif session_key in {"us_intraday_risk", "into_close"}:
            header = "WATCH INTO CLOSE"
        elif session_key == "us_pre_open":
            header = "OPENING TRIGGERS"
        else:
            header = "SESSION TRIGGERS"
        index_quotes = briefing.market_setup.index_quotes + briefing.market_setup.macro_quotes
        vix = next((float(q.current_price or 0.0) for q in index_quotes if "VIX" in (q.display_name or q.symbol or "").upper()), None)
        ten_y = next((float(q.current_price or 0.0) for q in index_quotes if "10Y" in (q.display_name or q.symbol or "").upper()), None)
        oil = next(
            (
                float(q.current_price or 0.0)
                for q in index_quotes
                if ("WTI" in (q.display_name or q.symbol or "").upper()) or ("CRUDE" in (q.display_name or q.symbol or "").upper())
            ),
            None,
        )
        triggers: list[str] = []
        vix_line = format_trigger_line("VIX", vix, 20.0, "above", "confirms broader risk-off pressure")
        if vix_line:
            triggers.append(vix_line)
        ten_y_line = format_trigger_line("US 10Y", ten_y, 4.45, "above", "reinforce rates pressure")
        if ten_y_line:
            triggers.append(ten_y_line)
        oil_line = format_trigger_line("WTI", oil, 107.0, "above", "signal escalating energy pressure")
        if oil_line:
            triggers.append(oil_line)
        triggers.append("Nasdaq turning negative would indicate the growth cushion is fading.")
        return "\n".join([f"<b>{header}</b>"] + [f"- {line}" for line in triggers[:4]])

    def _format_healthcare_intelligence(
        self,
        section: HealthcareBriefingSection | None,
        *,
        session_key: str,
    ) -> str:
        if section is None or not section.enabled:
            return ""
        if not section.items:
            if section.unavailable_reason:
                return "\n".join([f"<b>{SECTION_HEADERS['healthcare_intelligence']}</b>", section.unavailable_reason])
            return ""
        max_items = 4 if session_key == "morning" else 3
        lines = [f"<b>{SECTION_HEADERS['healthcare_intelligence']}</b>"]
        if section.read:
            lines.append(f"Read: {section.read}")
        for idx, item in enumerate(section.items[:max_items], 1):
            lines.append(f"{idx}. <b>{item.title}</b>")
            if item.summary:
                lines.append(f"   {truncate(item.summary, 180)}")
            if item.market_relevance:
                lines.append(f"   Why market-relevant: {truncate(item.market_relevance, 180)}")
            if item.portfolio_lens:
                lines.append(f"   Portfolio lens: {truncate(item.portfolio_lens, 180)}")
            meta: list[str] = []
            if item.company_display:
                meta.append(item.company_display)
            if item.asset_display:
                meta.append(item.asset_display)
            if item.source_line:
                meta.append(item.source_line)
            if item.published_at:
                meta.append(item.published_at.astimezone(self.local_tz).strftime("%H:%M %Z"))
            if meta:
                lines.append(f"   <i>{' | '.join(meta)}</i>")
        return "\n".join(lines)

    def format_intraday_update(self, update: IntradayUpdate) -> list[str]:
        """Format an hourly intraday update."""
        sections = []
        is_weekend = update.session_mode in {"saturday", "sunday"}
        title = (
            SECTION_HEADERS["weekend_intraday_title"]
            if is_weekend
            else SECTION_HEADERS["intraday_title"]
        )

        sections.append(
            f"<b>{title}</b> | {update.hour_label}"
        )

        # Quick market snapshot
        if update.market_snapshot:
            sections.append(self._format_intraday_market_snapshot(update.market_snapshot))

        global_risk = self._format_global_risk_update(update.global_risk_items)
        if global_risk:
            sections.append(global_risk)

        lead = self._build_intraday_lead(update)
        if lead:
            sections.append(lead)

        # New events
        for evt in update.new_events[:MAX_INTRADAY_EVENTS]:
            sections.append(self._format_single_event(evt))

        if not update.new_events:
            if is_weekend:
                sections.append("<i>No material weekend developments in this cycle.</i>")
            else:
                sections.append("<i>No material new developments this hour.</i>")

        sections.append(
            f"<i>{update.events_fetched} fetched, "
            f"{update.events_after_dedup} unique, "
            f"{update.events_sent} sent</i>"
        )

        full_text = "\n\n".join(sections)
        return self._split_message(full_text)

    def format_breaking_alert(self, alert: BreakingAlert) -> list[str]:
        """Format a breaking alert."""
        evt = alert.event
        classification = alert.classification
        company_label = self._company_label(evt)
        time_label = self._format_event_time(evt)
        generated_local = alert.generated_at.astimezone(self.local_tz) if alert.generated_at.tzinfo else alert.generated_at
        session_mode = session_mode_for(generated_local)
        reference_label = "Friday prior close" if session_mode in {"saturday", "sunday"} else "prior close"

        is_followup = getattr(evt, "event_type", None) == "followup"

        if is_followup:
            tier_header = "UPDATE"
        else:
            freshness_label = str((evt.raw_data or {}).get("breaking_label", "")).strip().upper()
            if freshness_label in {"BREAKING", "UPDATE", "CONTEXT", "LATE DISCOVERY"}:
                tier_header = freshness_label
            else:
                tier_header = ""
            if classification.category == "healthcare_biotech":
                tier_header = "BREAKING BIOTECH ALERT"
            elif not tier_header:
                tier_header = {
                    "breaking": SECTION_HEADERS["breaking_title"],
                    "high_priority": "HIGH PRIORITY",
                    "regular": "MARKET ALERT",
                    "ignore": "MARKET ALERT",
                }.get(classification.tier, SECTION_HEADERS["breaking_title"])

        sections = [
            f"<b>{tier_header}</b>",
            f"<b>{evt.title}</b>",
        ]

        if not is_followup:
            summary_clean = self._strip_cluster_suffix((evt.summary or "").strip())
            if summary_clean and not self._summary_duplicates_title(summary_clean, evt.title):
                sections.append(truncate(summary_clean, 500))

        if alert.reason:
            sections.append(f"<i>Why it matters:</i> {alert.reason}")

        if not is_followup:
            if classification.watch_assets:
                sections.append(f"<i>Watch:</i> {', '.join(classification.watch_assets[:5])}")
            if classification.confirm_signals:
                sections.append(f"<i>Confirm:</i> {classification.confirm_signals[0]}")
            if classification.invalidate_signals:
                sections.append(f"<i>Invalidate:</i> {classification.invalidate_signals[0]}")

        meta = []
        if company_label:
            meta.append(company_label)
        if time_label:
            meta.append(time_label)
        if meta:
            sections.append("<i>" + " | ".join(meta) + "</i>")

        if alert.market_context:
            ctx_lines = ["Market context:"]
            for q in alert.market_context[:4]:
                ctx_lines.append(
                    "  " + format_context_price(
                        q.display_name or q.symbol,
                        q.symbol,
                        q.current_price,
                        q.previous_close,
                        q.change_percent,
                        reference_label=reference_label,
                    )
                )
            sections.append("\n".join(ctx_lines))

        full_text = "\n\n".join(sections)
        return self._split_message(full_text)

    # -- Section formatters ---------------------------------------------------

    def _format_market_setup(self, briefing: MorningBriefing) -> str:
        is_weekend = briefing.session_mode in {"saturday", "sunday"}
        section_name = SECTION_HEADERS["weekend_setup"] if is_weekend else SECTION_HEADERS["market_setup"]
        lines = [f"<b>{section_name}</b>"]
        local_date = briefing.generated_at.astimezone(self.local_tz).date()

        session_mode = briefing.session_mode

        # Index quotes
        for q in briefing.market_setup.index_quotes:
            if is_treasury_yield_quote(q):
                continue  # rendered below via compute_yield_context
            name = self._friendly_instrument_label(q.display_name or q.symbol, q.symbol)
            asset_type = _asset_type_for_quote(q)
            ctx = move_context_from_quote(q, asset_type=asset_type, label=name, session_mode=session_mode)
            line = format_move_context_line(ctx)
            freshness_suffix = self._freshness_suffix(briefing, q)
            if freshness_suffix:
                line += freshness_suffix
            ex = exchange_for_symbol(q.symbol)
            if ex:
                closed, reason = is_exchange_closed(ex, local_date)
                if closed:
                    suffix = " [closed, prior close]"
                    if reason and reason != "weekend":
                        suffix = f" [closed, prior close · {reason}]"
                    line += suffix
            lines.append(line)

        # Macro instruments (gold, oil, USD, BTC)
        for q in briefing.market_setup.macro_quotes:
            if is_treasury_yield_quote(q):
                continue
            name = self._friendly_instrument_label(q.display_name or q.symbol, q.symbol)
            asset_type = _asset_type_for_quote(q)
            ctx = move_context_from_quote(q, asset_type=asset_type, label=name, session_mode=session_mode)
            line = format_move_context_line(ctx)
            freshness_suffix = self._freshness_suffix(briefing, q)
            if freshness_suffix:
                line += freshness_suffix
            lines.append(line)

        # Treasury yields from FRED — shown in basis points, never % change of yield
        setup = briefing.market_setup
        if setup.treasury_10y:
            y = setup.treasury_10y
            ctx = compute_yield_context(
                symbol="US10Y",
                label="US 10Y",
                current_yield=float(y.value),
                change_yield=float(y.change or 0.0),
                prev_yield=float(y.previous_value or 0.0),
                session_mode=session_mode,
            )
            lines.append(format_yield_context_line(ctx))
        if setup.treasury_2y:
            y = setup.treasury_2y
            ctx = compute_yield_context(
                symbol="US2Y",
                label="US 2Y",
                current_yield=float(y.value),
                change_yield=float(y.change or 0.0),
                prev_yield=float(y.previous_value or 0.0),
                session_mode=session_mode,
            )
            lines.append(format_yield_context_line(ctx))

        # Sector breadth (up/down count from SPDR ETFs)
        breadth_rows = briefing.market_setup.market_breadth
        if breadth_rows:
            up = sum(1 for b in breadth_rows if float(b.change_percent or 0) > 0)
            dn = len(breadth_rows) - up
            lines.append(f"Sectors: {up}↑ {dn}↓")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _format_macro(self, macro: list[MacroDataPoint]) -> str:
        if not macro:
            return ""
        lines = [f"<b>{SECTION_HEADERS['macro']}</b>"]
        for m in macro:
            val = f"{m.value:,.2f}" if abs(m.value) >= 1 else f"{m.value:.4f}"
            chg = ""
            if m.change is not None:
                sign = "+" if m.change >= 0 else ""
                chg = f" ({sign}{m.change:.4f})"
            lines.append(f"{m.name}: {val}{chg}")
        return "\n".join(lines)

    def _format_commodity_strip(self, strip: list[MacroDataPoint]) -> str:
        if not strip:
            return ""
        _ARROWS = {True: "↑", False: "↓"}
        lines = ["<b>COMMODITIES</b>"]
        for m in strip:
            chg_pct = m.change_percent
            if chg_pct is not None:
                arrow = _ARROWS[chg_pct >= 0]
                pct_str = f" {arrow}{abs(chg_pct):.2f}%"
            else:
                pct_str = ""
            val = f"{m.value:,.2f}"
            name = (m.name or m.series_id).replace(" (USD/bbl)", "").replace(" (USD/troy oz)", "").replace(" (USD/MMBtu)", "")
            lines.append(f"{name}: {val}{pct_str}")
        return "\n".join(lines)

    def _format_themes(self, themes: list[NormalisedEvent]) -> str:
        return self._format_themes_for_mode(themes, session_mode="weekday")

    def _format_global_news(self, events: list[NormalisedEvent]) -> str:
        if not events:
            return ""
        lines = [f"<b>{SECTION_HEADERS['global_news']}</b>"]
        used_notes: set[str] = set()
        for idx, evt in enumerate(events[:6], 1):
            lines.append(f"{idx}. <b>{evt.title}</b>")
            lines.append(f"   {build_market_relevance_note(evt, used_notes=used_notes)}")
            meta = self._build_event_meta(
                evt,
                include_company=True,
                include_time=True,
                include_cluster=True,
            )
            if meta:
                lines.append(f"   <i>{' | '.join(meta)}</i>")
        return "\n".join(lines)

    _REGION_FLAGS: dict[str, str] = {
        "us": "🇺🇸", "united states": "🇺🇸", "north america": "🇺🇸",
        "europe": "🇪🇺", "eu": "🇪🇺", "euro area": "🇪🇺", "eurozone": "🇪🇺",
        "uk": "🇬🇧", "united kingdom": "🇬🇧",
        "asia": "🌏", "apac": "🌏", "asia-pacific": "🌏",
        "china": "🇨🇳",
        "japan": "🇯🇵",
        "india": "🇮🇳",
        "middle east": "🌍", "mena": "🌍",
        "latam": "🌎", "latin america": "🌎",
        "russia": "🇷🇺",
        "emerging markets": "🌐", "em": "🌐",
    }

    def _region_flag(self, region: str) -> str:
        return self._REGION_FLAGS.get(region.lower().strip(), "")

    def _format_regional_lens(self, rows: list[dict[str, str]], skew_summary: str) -> str:
        if not rows and not skew_summary:
            return ""
        lines = [f"<b>{SECTION_HEADERS['regional_lens']}</b>"]
        if skew_summary:
            lines.append(skew_summary)
        for row in rows[:7]:
            region = row.get("region", "Region")
            flag = self._region_flag(region)
            prefix = f"{flag} " if flag else ""
            lines.append(
                f"- {prefix}<b>{region}</b>: {row.get('direction', 'mixed')} "
                f"({row.get('status', 'monitor')}) · {row.get('driver', 'mixed macro')} · "
                f"{row.get('implication', '')}"
            )
        return "\n".join(lines)

    def _format_portfolio_impact(
        self,
        bullets: list[str],
        posture: str,
        regime_context: str,
        positioning_alignment: str,
        geo_risk_level: str = "",
        geo_risk_summary: str = "",
        regime_shift: dict[str, str] | None = None,
        heading: str | None = None,
    ) -> str:
        if (
            not bullets
            and not regime_context
            and not positioning_alignment
            and not geo_risk_summary
            and not geo_risk_level
            and not (regime_shift or {})
        ):
            return ""
        lines = [f"<b>{heading or SECTION_HEADERS['portfolio_impact']}</b>"]
        if posture:
            posture_display = {
                "review_diagnostics": "review risk",
                "review_risk": "review risk",
            }.get(posture, posture.replace("_", " "))
            lines.append(f"Action posture: {posture_display}")
        for bullet in bullets[:3]:
            lines.append(f"- {bullet}")
        if geo_risk_level:
            lines.append(f"- Geo risk meter: {geo_risk_level}")
        if geo_risk_summary:
            lines.append(f"- {geo_risk_summary}")
        if regime_context:
            lines.append("")
            lines.append(f"<b>{SECTION_HEADERS['regime_context']}</b>")
            lines.append(regime_context)
        if positioning_alignment:
            lines.append(positioning_alignment)
        shift = regime_shift or {}
        if shift:
            shift_text = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(shift.items()))
            lines.append(f"Regime shift: {shift_text}")
        return "\n".join(lines)

    def _apply_quality_guards(self, sections: list[str], *, briefing: MorningBriefing) -> list[str]:
        """Deterministic non-blocking cleanup for user-facing output quality.

        Delegates to BriefingQualityGuard for all rules. Legacy inline checks
        are preserved as a belt-and-braces fallback before the guard runs.
        """
        # Resolve flags
        try:
            flags = flags_from_briefing(briefing)
        except Exception:
            flags = {}
        vix_available = bool(flags.get("vix_available", True))
        vix_stale = bool(flags.get("vix_stale", False))
        brent_stale = bool(flags.get("brent_stale", False))

        # Belt-and-braces: also check data_basis_lines for legacy stale markers
        basis_lines = [str(line or "") for line in (briefing.data_basis_lines or [])]
        if any("vix:" in line.lower() and "unavailable" in line.lower() for line in basis_lines):
            vix_available = False
        if any("brent" in line.lower() and "stale" in line.lower() for line in basis_lines):
            brent_stale = True

        guard = BriefingQualityGuard(
            vix_available=vix_available,
            vix_stale=vix_stale,
            brent_stale=brent_stale,
        )
        return guard.apply(sections)

    def _format_themes_for_mode(
        self,
        themes: list[NormalisedEvent],
        session_mode: str,
    ) -> str:
        section = (
            SECTION_HEADERS["weekend_themes"]
            if session_mode in {"saturday", "sunday"}
            else SECTION_HEADERS["themes"]
        )
        if not themes:
            return "\n".join(
                [
                    f"<b>{section}</b>",
                    "No high-confidence portfolio/watchlist themes passed relevance and source-quality filters this cycle.",
                ]
            )
        lines = [f"<b>{section}</b>"]
        for i, evt in enumerate(themes[:MAX_THEMES], 1):
            lines.append(f"{i}. <b>{evt.title}</b>")
            if evt.summary:
                lines.append(f"   {truncate(evt.summary, 200)}")
            meta = self._build_event_meta(
                evt,
                include_company=True,
                include_time=True,
                include_cluster=True,
            )
            if meta:
                lines.append(f"   <i>{' | '.join(meta)}</i>")
        return "\n".join(lines)

    @staticmethod
    def _provider_health_note(provider_health: str) -> str:
        """Collapse raw provider counters into a user-friendly one-line note."""
        chunks = [chunk.strip() for chunk in provider_health.split(",") if chunk.strip()]
        if not chunks:
            return ""
        parsed: dict[str, int] = {}
        for chunk in chunks:
            if ":" not in chunk:
                continue
            name, value = chunk.split(":", 1)
            try:
                parsed[name.strip().lower()] = int(value.strip())
            except ValueError:
                continue
        degraded: list[str] = []
        if parsed.get("gdelt", 1) == 0:
            degraded.append("GDELT unavailable")
        if parsed.get("fmp", 1) == 0:
            degraded.append("FMP unavailable")
        core_ok = parsed.get("finnhub", 0) > 0 or parsed.get("newsapi", 0) > 0
        if degraded:
            suffix = "core providers available" if core_ok else "core providers degraded"
            return f"Provider notes: {'; '.join(degraded)}; {suffix}."
        return ""

    def _format_sector_scan(
        self,
        sectors: list[SectorSnapshot],
        session_mode: str = "weekday",
    ) -> str:
        if not sectors:
            return ""
        lines = [f"<b>{SECTION_HEADERS['sectors']}</b>"]

        non_empty_sectors = [snap for snap in sectors if snap.top_events]
        empty_with_quotes = [snap for snap in sectors if not snap.top_events and snap.etf_quote]

        if not non_empty_sectors:
            lines.append("  <i>No high-trust sector-specific developments; sector move appears price-led rather than news-led.</i>")
            if empty_with_quotes:
                compact = self._format_empty_sector_compact(empty_with_quotes, session_mode)
                if compact:
                    lines.append(compact)
            return "\n".join(lines)

        for snap in non_empty_sectors:
            # Sector ETF performance
            if snap.etf_quote:
                q = snap.etf_quote
                pct_str = format_change(q.change, q.change_percent)
                lines.append(f"\n<b>{snap.display_name}</b> ({snap.etf_symbol} {pct_str})")
                freshness = self._format_quote_freshness_line(q, session_mode)
                if freshness:
                    lines.append(f"  <i>{freshness}</i>")
            else:
                lines.append(f"\n<b>{snap.display_name}</b>")

            # Top events in this sector
            for evt in snap.top_events[:MAX_SECTOR_EVENTS]:
                prefix = self._build_event_prefix(evt)
                if prefix:
                    lines.append(f"  {prefix} | {evt.title[:110]}")
                else:
                    lines.append(f"  {evt.title[:120]}")

        compact = self._format_empty_sector_compact(empty_with_quotes, session_mode)
        if compact:
            lines.append("")
            lines.append(compact)

        return "\n".join(lines)

    def _format_portfolio_focus(self, events: list[NormalisedEvent]) -> str:
        if not events:
            return ""
        lines = [f"<b>{SECTION_HEADERS['portfolio_focus']}</b>"]
        for event in events[:5]:
            lines.append(f"- <b>{event.title}</b>")
            if event.summary and not self._summary_duplicates_title(event.summary, event.title):
                lines.append(f"  {truncate(self._strip_cluster_suffix(event.summary), 180)}")
            meta = self._build_event_meta(
                event,
                include_company=True,
                include_time=True,
                include_cluster=True,
            )
            if meta:
                lines.append(f"  <i>{' | '.join(meta)}</i>")
        return "\n".join(lines)

    def _format_global_risk_update(self, events: list[NormalisedEvent]) -> str:
        if not events:
            return ""
        lines = [f"<b>{SECTION_HEADERS['global_risk_update']}</b>"]
        used_notes: set[str] = set()
        for evt in events[:3]:
            lines.append(f"- <b>{evt.title}</b>")
            lines.append(f"  {build_market_relevance_note(evt, used_notes=used_notes)}")
            meta = self._build_event_meta(
                evt,
                include_company=True,
                include_time=True,
                include_cluster=True,
            )
            if meta:
                lines.append(f"  <i>{' | '.join(meta)}</i>")
        return "\n".join(lines)

    def _format_earnings(self, earnings: list[EarningsEvent], relevance: dict[str, str] | None = None) -> str:
        if not earnings:
            return ""
        relevance = relevance or {}
        has_relevant = any(tag in {"portfolio", "watchlist"} for tag in relevance.values())
        if not has_relevant:
            return "\n".join(
                [
                    f"<b>{SECTION_HEADERS['earnings']}</b>",
                    "No portfolio-relevant earnings this week.",
                ]
            )
        grouped = self._group_earnings(earnings[:MAX_EARNINGS_DISPLAY])
        body: list[str] = []
        for label in ("Today", "Tomorrow", "This Week"):
            bucket = grouped.get(label, [])
            if not bucket:
                continue
            body.append(f"  <i>{label}</i>")
            for e in bucket:
                body.append(f"    {self._format_earnings_line(e, relevance)}")
        if not body:
            return ""
        return "\n".join([f"<b>{SECTION_HEADERS['earnings']}</b>", *body])

    def _format_earnings_line(self, e: EarningsEvent, relevance: dict[str, str]) -> str:
        """Format: (TICKER) Name, Sector — Qx YYYY (dd/mm/yyyy, time-of-day) [tag]."""
        sym = str(e.symbol or "").upper()
        name = (e.company_name or "").strip() or company_name_for_ticker(sym)
        if e.sector:
            head = f"({sym}) {name}, {e.sector}"
        else:
            head = f"({sym}) {name}"

        quarter = (e.fiscal_quarter or "").strip()

        date_str = ""
        if e.report_date:
            try:
                dt = datetime.strptime(e.report_date, "%Y-%m-%d")
                date_str = dt.strftime("%d/%m/%Y")
            except (TypeError, ValueError):
                date_str = ""

        time_label = {
            "bmo": "pre-market",
            "amc": "post-market",
            "dmh": "during-market",
            "during": "during-market",
        }.get(e.time, "")

        when = ""
        if date_str and time_label:
            when = f"({date_str}, {time_label})"
        elif date_str:
            when = f"({date_str})"

        estimates: list[str] = []
        if e.eps_estimate is not None:
            estimates.append(f"EPS est. {e.eps_estimate:.2f}")
        if e.revenue_estimate is not None:
            rev = e.revenue_estimate
            if rev >= 1_000_000_000:
                estimates.append(f"Rev est. {rev/1_000_000_000:.1f}B")
            elif rev >= 1_000_000:
                estimates.append(f"Rev est. {rev/1_000_000:.0f}M")
        if e.prior_quarter_surprise_pct is not None:
            sign = "+" if e.prior_quarter_surprise_pct >= 0 else ""
            beat = "beat" if e.prior_quarter_surprise_pct >= 0 else "miss"
            estimates.append(f"prior: {sign}{e.prior_quarter_surprise_pct:.1f}% {beat}")
        est_str = f" | {', '.join(estimates)}" if estimates else ""

        tag_source = relevance.get(sym) or e.relevance_tag
        tag = ""
        if tag_source == "portfolio":
            tag = " [portfolio]"
        elif tag_source == "watchlist":
            tag = " [watchlist]"

        parts = [head]
        if quarter:
            parts.append(f" — {quarter}")
        if when:
            parts.append(f" {when}")
        return f"{''.join(parts)}{est_str}{tag}".rstrip()

    def _format_watchlist(
        self,
        briefing: MorningBriefing,
        events: list[NormalisedEvent],
        quotes: list[QuoteData],
        session_mode: str = "weekday",
        *,
        dominant_driver: str = "",
        top_themes: list[NormalisedEvent] | None = None,
    ) -> str:
        parts = [f"<b>{SECTION_HEADERS['watchlist']}</b>"]

        # Watchlist quotes
        if quotes:
            q_lines = []
            live_count = 0
            for q in quotes[:10]:
                ctx = move_context_from_quote(
                    q,
                    asset_type=_asset_type_for_quote(q),
                    session_mode=session_mode,
                )
                label = format_watchlist_move_label(ctx)
                freshness_short = self._freshness_short_label(quote=q, briefing=briefing)
                suffix = f" {freshness_short}" if freshness_short else ""
                if freshness_short == "live":
                    live_count += 1
                q_lines.append(f"{q.display_name or q.symbol} {label}{suffix}")
            parts.append(" | ".join(q_lines))
            summary = self._watchlist_summary_line(
                quotes,
                events,
                dominant_driver=dominant_driver,
                top_themes=top_themes or [],
            )
            if summary:
                parts.append(f"<i>{summary}</i>")
            shown_quotes = quotes[:10]
            if shown_quotes and all(self._freshness_short_label(quote=q, briefing=briefing) == "prior close" for q in shown_quotes):
                parts.append("<i>All displayed watchlist moves are prior-close context (not live pre-market/intraday).</i>")
            intraday_like = (briefing.session_key or "").lower() in {"us_intraday_risk", "into_close"}
            if intraday_like and shown_quotes and live_count >= max(1, int(len(shown_quotes) * 0.7)):
                parts.append("<i>Watchlist basis changed from prior-close context to live intraday quotes.</i>")
            freshness = self._format_quotes_freshness_summary(quotes, session_mode)
            if freshness:
                parts.append(f"<i>{freshness}</i>")

        # Watchlist-relevant events
        if events:
            for evt in events[:MAX_WATCHLIST_EVENTS]:
                prefix = self._build_event_prefix(evt)
                if prefix:
                    parts.append(f"  {prefix} | {evt.title[:140]}")
                else:
                    parts.append(f"  {evt.title[:150]}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def _watchlist_summary_line(
        self,
        quotes: list[QuoteData],
        events: list[NormalisedEvent],
        *,
        dominant_driver: str = "",
        top_themes: list[NormalisedEvent] | None = None,
    ) -> str:
        if not quotes:
            return ""
        sorted_quotes = sorted(quotes, key=lambda q: float(q.change_percent or 0.0), reverse=True)
        top = sorted_quotes[0]
        bottom = sorted_quotes[-1]
        spread_pp = float(top.change_percent or 0.0) - float(bottom.change_percent or 0.0)
        positives = sum(1 for q in quotes if float(q.change_percent or 0.0) > 0.0)
        direction = "mostly green" if positives >= max(1, int(len(quotes) * 0.6)) else "mixed-to-red"
        catalyst = "no dominant catalyst yet"
        if dominant_driver:
            catalyst = truncate(dominant_driver, 120)
        else:
            best_evt = self._best_catalyst_event(events or [])
            if best_evt is None:
                best_evt = self._best_catalyst_event(top_themes or [])
            if best_evt is not None:
                catalyst = truncate(best_evt.title, 110)
        return (
            f"Watchlist is {direction}; leaders: {(top.display_name or top.symbol)} {float(top.change_percent or 0.0):+.2f}% "
            f"vs laggard {(bottom.display_name or bottom.symbol)} {float(bottom.change_percent or 0.0):+.2f}%. "
            f"Dispersion: {spread_pp:.2f}pp. Main catalyst: {catalyst}"
        )

    def _best_catalyst_event(self, events: list[NormalisedEvent]) -> NormalisedEvent | None:
        """Pick the strongest watchlist catalyst while suppressing low-signal headlines."""
        if not events:
            return None
        def _score(evt: NormalisedEvent) -> float:
            text = f"{evt.title} {evt.summary}".lower()
            quality = 0.0
            article_type = classify_article_type(evt.title, evt.summary, evt.url)
            if article_type == "hard_news":
                quality += 0.5
            elif article_type == "preview":
                quality -= 0.25
            elif article_type in {"seo", "listicle", "opinion"}:
                quality -= 0.6
            if any(term in text for term in ("earnings", "guidance", "fomc", "fed", "inflation", "yield", "oil", "hormuz", "iran")):
                quality += 0.3
            return float(evt.final_score or 0.0) + quality + (min(5, int(evt.cluster_size or 1)) * 0.03)

        ranked = sorted(
            events,
            key=_score,
            reverse=True,
        )
        for event in ranked:
            title = (event.title or "").strip()
            if not title:
                continue
            if self._is_low_signal_catalyst_title(title):
                continue
            return event
        return None

    @staticmethod
    def _is_low_signal_catalyst_title(title: str) -> bool:
        text = (title or "").lower()
        if classify_article_type(title) in {"preview", "listicle", "seo", "opinion"}:
            return True
        low_signal_terms = (
            "best cd rates",
            "apy",
            "checking account",
            "high-yield savings",
            "price target",
            "analyst note",
            "analysts love",
            "smart buy",
            "risky move",
            "losing its edge",
            "should you buy",
            "what's behind",
            "no-brainer",
            "price prediction",
        )
        return any(term in text for term in low_signal_terms)

    def _group_earnings(self, earnings: list[EarningsEvent]) -> dict[str, list[EarningsEvent]]:
        now_local = datetime.now(self.local_tz).date()
        grouped: dict[str, list[EarningsEvent]] = {"Today": [], "Tomorrow": [], "This Week": []}
        for event in earnings:
            label = "This Week"
            try:
                report_date = datetime.strptime(str(event.report_date or ""), "%Y-%m-%d").date()
                delta = (report_date - now_local).days
                if delta <= 0:
                    label = "Today"
                elif delta == 1:
                    label = "Tomorrow"
                elif delta <= 7:
                    label = "This Week"
            except ValueError:
                label = "This Week"
            grouped.setdefault(label, []).append(event)
        return grouped


    def _format_intraday_market_snapshot(self, quotes: list[QuoteData]) -> str:
        """Render intraday snapshot with exact levels + % change."""
        if not quotes:
            return ""
        lines: list[str] = []
        for q in quotes:
            label = self._friendly_instrument_label(q.display_name or q.symbol, q.symbol)
            if is_treasury_yield_quote(q):
                level = float(q.current_price or 0.0)
                bp = int(round(float(q.change or 0.0) * 100))
                bp_s = "flat" if bp == 0 else f"{bp:+d} bp"
                lines.append(f"{label} {level:.2f}% ({bp_s})")
                continue
            lines.append(
                format_compact_price_with_level(
                    label,
                    q.current_price,
                    q.change_percent,
                )
            )
        return " | ".join(lines)

    def _format_session_snapshot(self, briefing: MorningBriefing) -> str:
        """Compact snapshot for non-morning session updates."""
        priority_index: list[QuoteData] = []
        for quote in briefing.market_setup.index_quotes:
            name = (quote.display_name or quote.symbol or "").upper()
            if any(token in name for token in ("S&P", "NASDAQ", "STOXX", "DAX", "NIKKEI", "VIX")):
                priority_index.append(quote)
        if not priority_index:
            priority_index = list(briefing.market_setup.index_quotes[:5])
        priority_macro: list[QuoteData] = []
        for quote in briefing.market_setup.macro_quotes:
            name = (quote.display_name or quote.symbol or "").upper()
            if any(token in name for token in ("WTI", "BRENT", "GOLD", "10Y", "USD")):
                priority_macro.append(quote)
        line = self._format_intraday_market_snapshot(priority_index[:6] + priority_macro[:4])
        if not line:
            return ""
        return "\n".join(["<b>MARKET SNAPSHOT</b>", line])

    @staticmethod
    def _friendly_instrument_label(label: str, symbol: str) -> str:
        text = str(label or symbol or "").strip()
        if "^" in text:
            text = re.sub(r"\(\^?[A-Z0-9:=._-]+\)", "", text).strip()
            text = text.replace("^", "").strip()
        sym = str(symbol or "").upper().strip()
        if sym in {"SPY", "IVV", "VOO"} and ("S&P 500" in text or "SPX" in text):
            text = f"{text} [proxy]"
        elif sym in {"QQQ", "ONEQ"} and ("NASDAQ" in text or "COMP" in text):
            text = f"{text} [proxy]"
        elif sym in {"DIA"} and ("DOW" in text or "DJIA" in text):
            text = f"{text} [proxy]"
        elif sym in {"IWM"} and ("RUSSELL" in text or "RUT" in text):
            text = f"{text} [proxy]"
        return text or str(symbol or "").strip()

    def _format_empty_sector_compact(
        self,
        sectors: list[SectorSnapshot],
        session_mode: str,
    ) -> str:
        if not sectors:
            return ""
        ranked = sorted(
            sectors,
            key=lambda snap: abs(snap.etf_quote.change_percent) if snap.etf_quote else 0.0,
            reverse=True,
        )
        bits: list[str] = []
        for snap in ranked[:5]:
            quote = snap.etf_quote
            if not quote:
                continue
            pct = f"{quote.change_percent:+.2f}%"
            bits.append(f"{snap.display_name} ({snap.etf_symbol} {pct})")
        if not bits:
            return ""
        reference = "vs Friday close" if session_mode in {"saturday", "sunday"} else "vs prior close"
        return f"  <i>No high-trust developments: {' | '.join(bits)} ({reference})</i>"

    def _format_quotes_freshness_summary(
        self,
        quotes: list[QuoteData],
        session_mode: str,
    ) -> str:
        if not quotes:
            return ""
        latest = max(quotes, key=lambda quote: self._coerce_utc_ts(quote.timestamp))
        latest_ts = self._coerce_utc_ts(latest.timestamp)
        local_label = latest_ts.astimezone(self.local_tz).strftime("%H:%M %Z")
        source_counts: dict[str, int] = {}
        for quote in quotes:
            source = (quote.source or "unknown").lower()
            source_counts[source] = source_counts.get(source, 0) + 1
        source_summary = ", ".join(
            f"{source}({count})"
            for source, count in sorted(source_counts.items(), key=lambda item: item[0])
        )
        reference = "vs Friday close" if session_mode in {"saturday", "sunday"} else "vs prior close"
        return f"Quotes as of {local_label} | sources: {source_summary} | {reference}"

    def _format_quote_freshness_line(
        self,
        quote: QuoteData,
        session_mode: str,
    ) -> str:
        ts = self._coerce_utc_ts(quote.timestamp)
        local_label = ts.astimezone(self.local_tz).strftime("%H:%M %Z")
        source = (quote.source or "unknown").lower()
        reference = "vs Friday close" if session_mode in {"saturday", "sunday"} else "vs prior close"
        return f"As of {local_label} via {source} | {reference}"

    def _freshness_meta(self, briefing: MorningBriefing, quote: QuoteData) -> dict:
        key = (quote.symbol or "").upper().strip()
        return dict((briefing.quote_freshness or {}).get(key) or {})

    def _freshness_suffix(self, briefing: MorningBriefing, quote: QuoteData) -> str:
        meta = self._freshness_meta(briefing, quote)
        if not meta:
            return ""
        state = str(meta.get("freshness_state") or "")
        label = str(meta.get("freshness_label") or "").strip()
        if state in {"near_real_time", "live"}:
            return ""
        if not label:
            return ""
        return f" | {label}"

    def _freshness_short_label(self, *, quote: QuoteData, briefing: MorningBriefing) -> str:
        meta = self._freshness_meta(briefing, quote)
        if not meta:
            return ""
        state = str(meta.get("freshness_state") or "")
        if state in {"near_real_time", "live"}:
            return "live"
        if state == "prior_close":
            return "prior close"
        if state == "delayed":
            return "delayed"
        if state == "stale":
            return "stale"
        if state == "carried_forward":
            return "carried forward"
        if state == "unavailable":
            return "unavailable"
        return ""

    @staticmethod
    def _coerce_utc_ts(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _format_single_event(self, evt: NormalisedEvent) -> str:
        """Format a single event for intraday/alert context.

        Avoids duplicating the title as the summary, and only shows
        metadata that is genuinely differentiating (not boilerplate).
        """
        parts = [f"<b>{evt.title}</b>"]

        # Only show summary if it adds information beyond the title
        if evt.summary:
            summary_clean = self._strip_cluster_suffix(evt.summary.strip())
            if summary_clean and not self._summary_duplicates_title(summary_clean, evt.title):
                parts.append(truncate(summary_clean, 300))

        # Compact metadata: only include what differentiates this item
        meta = []
        company_label = self._company_label(evt)
        if company_label:
            meta.append(company_label)
        time_label = self._format_event_time(evt)
        if time_label:
            meta.append(time_label)
        if evt.update_status == "material_update":
            meta.append("Developing")
        if evt.cluster_size > 1:
            meta.append(f"{evt.cluster_size} reports")
        if evt.source == "sec_edgar":
            meta.append("Official filing")
        if evt.sectors:
            meta.append(", ".join(evt.sectors[:2]))
        if meta:
            parts.append("<i>" + " | ".join(meta) + "</i>")

        return "\n".join(parts)

    def _build_intraday_lead(self, update: IntradayUpdate) -> str:
        if update.session_mode in {"saturday", "sunday"}:
            return "<i>Weekend briefing: key developments while cash equity markets are closed.</i>"
        if not update.new_events:
            return ""

        top = update.new_events[0]
        text = f"{top.title} {top.summary}".lower()
        if any(k in text for k in ("iran", "hormuz", "israel", "oil", "opec", "saudi")):
            return "<i>Main thread: energy and geopolitical risk continue to drive cross-asset headlines.</i>"
        if any(k in text for k in ("powell", "fomc", "inflation", "yield", "rates", "fed")):
            return "<i>Main thread: rates and macro policy signals are leading the tape.</i>"
        if any(k in text for k in ("nvidia", "ai", "chip", "semiconductor", "meta", "amazon")):
            return "<i>Main thread: AI and mega-cap tech headlines remain in focus.</i>"
        return "<i>Main thread: the highest-impact developments this cycle are below.</i>"

    def _format_week_ahead(self, briefing: MorningBriefing) -> str:
        lines = [f"<b>{SECTION_HEADERS['week_ahead']}</b>"]

        text = " ".join(evt.title.lower() for evt in briefing.top_themes[:8])
        if any(k in text for k in ("iran", "hormuz", "oil", "opec", "saudi", "israel")):
            lines.append("  Energy and geopolitical headlines may drive Monday risk sentiment and oil-sensitive sectors.")
        if any(k in text for k in ("powell", "fomc", "inflation", "cpi", "ppi", "pce", "payroll")):
            lines.append("  Macro path remains central: watch inflation, labor, and central-bank communication.")
        if briefing.watchlist_quotes:
            focus = ", ".join((q.display_name or q.symbol) for q in briefing.watchlist_quotes[:4])
            lines.append(f"  Watchlist focus into next open: {focus}.")
        lines.append("  Futures, FX, crypto, and crude will shape the initial tone before US cash-market reopen.")
        return "\n".join(lines)

    def _company_label(self, evt: NormalisedEvent) -> str:
        if not evt.tickers:
            return ""
        if event_company_confidence(evt) < 0.75:
            return ""
        text = f"{evt.title} {evt.summary}".lower()
        valid: list[str] = []
        for ticker in evt.tickers:
            symbol = (ticker or "").upper().strip()
            if not symbol:
                continue
            company = company_name_for_ticker(symbol)
            # Unknown company mapping: keep the ticker.
            if company == symbol:
                valid.append(symbol)
                continue
            aliases = self._company_alias_tokens(company)
            if aliases and any(alias in text for alias in aliases):
                valid.append(symbol)
        return format_company_ticker_list(valid)

    @staticmethod
    def _company_alias_tokens(company: str) -> list[str]:
        base = (company or "").lower()
        if not base:
            return []
        trimmed = re.sub(
            r"\b(inc|inc\.|corp|corp\.|corporation|company|co|co\.|group|plc|ltd|limited|holdings?|sa|ag|nv)\b",
            " ",
            base,
        )
        trimmed = re.sub(r"[^a-z0-9&.\-\s]", " ", trimmed)
        compact = re.sub(r"\s+", " ", trimmed).strip()
        aliases: list[str] = []
        if compact:
            aliases.append(compact)
            parts = [p for p in compact.split(" ") if len(p) >= 4]
            if parts:
                aliases.append(parts[0])
        return list(dict.fromkeys(aliases))

    def _format_event_time(self, evt: NormalisedEvent) -> str:
        dt = evt.published_at
        if not dt:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(self.local_tz)
        return local_dt.strftime("%H:%M %Z")

    def _build_event_meta(
        self,
        evt: NormalisedEvent,
        *,
        include_company: bool = False,
        include_time: bool = False,
        include_cluster: bool = False,
    ) -> list[str]:
        meta: list[str] = []
        if include_company:
            company_label = self._company_label(evt)
            if company_label:
                meta.append(company_label)
        if include_time:
            time_label = self._format_event_time(evt)
            if time_label:
                meta.append(time_label)
        if include_cluster and evt.cluster_size > 1:
            meta.append(f"{evt.cluster_size} reports")
        return meta

    def _build_event_prefix(self, evt: NormalisedEvent) -> str:
        meta = self._build_event_meta(
            evt,
            include_company=True,
            include_time=True,
        )
        return " | ".join(meta)

    @staticmethod
    def _strip_cluster_suffix(summary: str) -> str:
        """Remove any "[+N related]" suffix that clustering may have appended."""
        if "[+" in summary:
            summary = summary[: summary.rfind("[+")].rstrip(" .,")
        return summary

    @staticmethod
    def _summary_duplicates_title(summary: str, title: str) -> bool:
        """Return True if the summary is essentially the title repeated.

        Uses a 0.70 token-overlap threshold so paraphrased repetition
        (e.g. "Oando plans $750M drilling campaign" vs "Oando to launch
        a $750 million drilling programme") is still caught.
        """
        s = summary.lower().strip()
        t = title.lower().strip()
        for sep in (" - ", " | ", "  "):
            if sep in s:
                s = s[: s.rfind(sep)].strip()
            if sep in t:
                t = t[: t.rfind(sep)].strip()
        if not s or not t:
            return True
        if s in t or t in s:
            return True
        s_tokens = set(s.split())
        t_tokens = set(t.split())
        if not s_tokens or not t_tokens:
            return True
        overlap = len(s_tokens & t_tokens) / max(len(s_tokens), len(t_tokens))
        return overlap >= 0.70

    # -- Message splitting ----------------------------------------------------

    @staticmethod
    def _split_message(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
        """Split a long message into chunks that fit Telegram's limit."""
        if len(text) <= max_len:
            return [text]

        messages = []
        while text:
            if len(text) <= max_len:
                messages.append(text)
                break

            # Find a good split point (double newline, then single newline)
            split_at = text.rfind("\n\n", 0, max_len)
            if split_at == -1:
                split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = max_len

            messages.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        return messages
