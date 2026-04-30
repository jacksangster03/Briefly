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
from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    NormalisedEvent,
    QuoteData,
    SectorSnapshot,
)
from app.universe.ticker_metadata import company_name_for_ticker, format_company_ticker, format_company_ticker_list


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
        if briefing.session_mode == "saturday":
            header = SECTION_HEADERS["weekend_title_saturday"]
        elif briefing.session_mode == "sunday":
            header = SECTION_HEADERS["weekend_title_sunday"]
        else:
            header = SECTION_HEADERS["morning_title"]
        sections.append(
            f"<b>{header}</b>\n{date_str}"
        )

        # Market setup
        setup = self._format_market_setup(briefing)
        if setup:
            sections.append(setup)

        # Macro context
        macro = self._format_macro(briefing.macro_context)
        if macro:
            sections.append(macro)

        regional = self._format_regional_lens(briefing.regional_lens, briefing.regional_skew_summary)
        if regional:
            sections.append(regional)

        impact = self._format_portfolio_impact(
            briefing.portfolio_impact_bullets,
            briefing.portfolio_action_posture,
            briefing.regime_context,
            briefing.positioning_alignment,
        )
        if impact:
            sections.append(impact)

        global_news = self._format_global_news(briefing.global_news)
        if global_news:
            sections.append(global_news)

        # Top themes
        themes = self._format_themes_for_mode(briefing.top_themes, briefing.session_mode)
        if themes:
            sections.append(themes)

        portfolio_focus = self._format_portfolio_focus(briefing.portfolio_focus)
        if portfolio_focus:
            sections.append(portfolio_focus)

        if is_weekend:
            week_ahead = self._format_week_ahead(briefing)
            if week_ahead:
                sections.append(week_ahead)

        # Sector scan
        sector = self._format_sector_scan(briefing.sector_scan, briefing.session_mode)
        if sector:
            sections.append(sector)

        # Earnings calendar
        earnings = self._format_earnings(briefing.earnings_calendar, briefing.earnings_relevance)
        if earnings:
            sections.append(earnings)

        # Watchlist
        watchlist = self._format_watchlist(
            briefing.watchlist_events,
            briefing.watchlist_quotes,
            briefing.session_mode,
        )
        if watchlist:
            sections.append(watchlist)

        # Footer
        sections.append(
            f"<i>{SECTION_HEADERS['footer']} | "
            f"{briefing.events_fetched} fetched, "
            f"{briefing.events_after_dedup} unique, "
            f"{briefing.events_sent} sent</i>"
        )

        full_text = "\n\n".join(sections)
        return self._split_message(full_text)

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

        # Index quotes
        for q in briefing.market_setup.index_quotes:
            name = self._friendly_instrument_label(q.display_name or q.symbol, q.symbol)
            lines.append(format_price_line(name, q.current_price, q.change, q.change_percent))

        # Macro instruments (gold, oil, USD, BTC)
        for q in briefing.market_setup.macro_quotes:
            name = self._friendly_instrument_label(q.display_name or q.symbol, q.symbol)
            lines.append(format_price_line(name, q.current_price, q.change, q.change_percent))

        # Treasury yields from FRED
        setup = briefing.market_setup
        if setup.treasury_10y:
            chg = f" ({format_change(setup.treasury_10y.change or 0, 0)})" if setup.treasury_10y.change else ""
            lines.append(f"US 10Y: {setup.treasury_10y.value:.3f}%{chg}")
        if setup.treasury_2y:
            chg = f" ({format_change(setup.treasury_2y.change or 0, 0)})" if setup.treasury_2y.change else ""
            lines.append(f"US 2Y: {setup.treasury_2y.value:.3f}%{chg}")
        if briefing.market_setup_analysis:
            lines.append("")
            lines.append(f"<i>Setup read:</i> {briefing.market_setup_analysis}")

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

    def _format_regional_lens(self, rows: list[dict[str, str]], skew_summary: str) -> str:
        if not rows and not skew_summary:
            return ""
        lines = [f"<b>{SECTION_HEADERS['regional_lens']}</b>"]
        if skew_summary:
            lines.append(skew_summary)
        for row in rows[:7]:
            lines.append(
                f"- <b>{row.get('region', 'Region')}</b>: {row.get('direction', 'mixed')} "
                f"({row.get('status', 'monitor')}) · Driver: {row.get('driver', 'mixed macro')} · "
                f"{row.get('implication', '')}"
            )
        return "\n".join(lines)

    def _format_portfolio_impact(
        self,
        bullets: list[str],
        posture: str,
        regime_context: str,
        positioning_alignment: str,
    ) -> str:
        if not bullets and not regime_context and not positioning_alignment:
            return ""
        lines = [f"<b>{SECTION_HEADERS['portfolio_impact']}</b>"]
        if posture:
            lines.append(f"Action posture: {posture.replace('_', ' ')}")
        for bullet in bullets[:3]:
            lines.append(f"- {bullet}")
        if regime_context:
            lines.append("")
            lines.append(f"<b>{SECTION_HEADERS['regime_context']}</b>")
            lines.append(regime_context)
        if positioning_alignment:
            lines.append(positioning_alignment)
        return "\n".join(lines)

    def _format_themes_for_mode(
        self,
        themes: list[NormalisedEvent],
        session_mode: str,
    ) -> str:
        if not themes:
            return ""
        section = (
            SECTION_HEADERS["weekend_themes"]
            if session_mode in {"saturday", "sunday"}
            else SECTION_HEADERS["themes"]
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
            lines.append("  <i>No high-trust sector developments in this cycle.</i>")
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
        lines = [f"<b>{SECTION_HEADERS['earnings']}</b>"]
        grouped = self._group_earnings(earnings[:MAX_EARNINGS_DISPLAY])
        total = sum(len(items) for items in grouped.values())
        relevant = sum(1 for item in earnings[:MAX_EARNINGS_DISPLAY] if str(item.symbol or "").upper() in relevance)
        lines.append(f"Upcoming: {total} earnings ({relevant} portfolio/watchlist relevant).")
        for label in ("Today", "Tomorrow", "This Week"):
            bucket = grouped.get(label, [])
            if not bucket:
                continue
            lines.append(f"  <i>{label}</i>")
            for e in bucket:
                time_label = {"bmo": "pre-market", "amc": "post-market", "during": "during-market"}.get(e.time, "")
                est = f" (est. ${e.eps_estimate:.2f})" if e.eps_estimate else ""
                base_name = (e.company_name or "").strip() or company_name_for_ticker(e.symbol)
                display = f"{base_name} ({e.symbol})" if base_name.upper() != e.symbol.upper() else format_company_ticker(e.symbol)
                tag = ""
                normalized_symbol = str(e.symbol or "").upper()
                if relevance.get(normalized_symbol) == "portfolio":
                    tag = " [portfolio]"
                elif relevance.get(normalized_symbol) == "watchlist":
                    tag = " [watchlist]"
                lines.append(f"    {display} — {e.fiscal_quarter} {time_label}{est}{tag}".rstrip())
        return "\n".join(lines)

    def _format_watchlist(
        self,
        events: list[NormalisedEvent],
        quotes: list[QuoteData],
        session_mode: str = "weekday",
    ) -> str:
        parts = [f"<b>{SECTION_HEADERS['watchlist']}</b>"]

        # Watchlist quotes
        if quotes:
            q_lines = [format_compact_price(q.display_name or q.symbol, q.change_percent)
                       for q in quotes[:10]]
            parts.append(" | ".join(q_lines))
            summary = self._watchlist_summary_line(quotes, events)
            if summary:
                parts.append(f"<i>{summary}</i>")
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

    def _watchlist_summary_line(self, quotes: list[QuoteData], events: list[NormalisedEvent]) -> str:
        if not quotes:
            return ""
        sorted_quotes = sorted(quotes, key=lambda q: float(q.change_percent or 0.0), reverse=True)
        top = sorted_quotes[0]
        bottom = sorted_quotes[-1]
        positives = sum(1 for q in quotes if float(q.change_percent or 0.0) > 0.0)
        direction = "mostly green" if positives >= max(1, int(len(quotes) * 0.6)) else "mixed-to-red"
        catalyst = (events[0].title[:88] + "...") if events and len(events[0].title) > 88 else (events[0].title if events else "no dominant catalyst yet")
        return (
            f"Watchlist is {direction}; leaders: {(top.display_name or top.symbol)} {float(top.change_percent or 0.0):+.2f}% "
            f"vs laggard {(bottom.display_name or bottom.symbol)} {float(bottom.change_percent or 0.0):+.2f}%. "
            f"Main catalyst: {catalyst}"
        )

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
        lines = [
            format_compact_price_with_level(
                self._friendly_instrument_label(q.display_name or q.symbol, q.symbol),
                q.current_price,
                q.change_percent,
            )
            for q in quotes
        ]
        return " | ".join(lines)

    @staticmethod
    def _friendly_instrument_label(label: str, symbol: str) -> str:
        text = str(label or symbol or "").strip()
        if "^" in text:
            text = re.sub(r"\(\^?[A-Z0-9:=._-]+\)", "", text).strip()
            text = text.replace("^", "").strip()
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
        return format_company_ticker_list(evt.tickers)

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
