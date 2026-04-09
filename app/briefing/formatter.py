"""Telegram-optimised message formatting for briefings and alerts.

All output is plain text (Telegram HTML parse mode). Optimised for
phone reading: short sections, clear labels, numbers first, sparse emoji.
"""

from __future__ import annotations

from datetime import datetime

from app.briefing.templates import (
    MAX_EARNINGS_DISPLAY,
    MAX_INTRADAY_EVENTS,
    MAX_SECTOR_EVENTS,
    MAX_THEMES,
    MAX_WATCHLIST_EVENTS,
    SECTION_HEADERS,
    TELEGRAM_MAX_LENGTH,
    format_change,
    format_compact_price,
    format_price_line,
)
from app.schemas.briefings import BreakingAlert, IntradayUpdate, MorningBriefing
from app.schemas.events import (
    EarningsEvent,
    MacroDataPoint,
    NormalisedEvent,
    QuoteData,
    SectorSnapshot,
)


class TelegramFormatter:
    """Formats briefing objects into Telegram-ready text messages."""

    def format_morning_briefing(self, briefing: MorningBriefing) -> list[str]:
        """Format a complete morning briefing. Returns a list of messages
        (split if exceeding Telegram's 4096 char limit).
        """
        sections = []

        # Header
        date_str = briefing.generated_at.strftime("%a %d %b %Y")
        sections.append(
            f"<b>{SECTION_HEADERS['morning_title']}</b>\n{date_str}"
        )

        # Market setup
        setup = self._format_market_setup(briefing)
        if setup:
            sections.append(setup)

        # Macro context
        macro = self._format_macro(briefing.macro_context)
        if macro:
            sections.append(macro)

        # Top themes
        themes = self._format_themes(briefing.top_themes)
        if themes:
            sections.append(themes)

        # Sector scan
        sector = self._format_sector_scan(briefing.sector_scan)
        if sector:
            sections.append(sector)

        # Earnings calendar
        earnings = self._format_earnings(briefing.earnings_calendar)
        if earnings:
            sections.append(earnings)

        # Watchlist
        watchlist = self._format_watchlist(briefing.watchlist_events, briefing.watchlist_quotes)
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

        sections.append(
            f"<b>{SECTION_HEADERS['intraday_title']}</b> | {update.hour_label}"
        )

        # Quick market snapshot
        if update.market_snapshot:
            lines = [format_compact_price(q.display_name or q.symbol, q.change_percent)
                     for q in update.market_snapshot[:6]]
            sections.append(" | ".join(lines))

        # New events
        for evt in update.new_events[:MAX_INTRADAY_EVENTS]:
            sections.append(self._format_single_event(evt))

        if not update.new_events:
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
        tickers = ", ".join(evt.tickers) if evt.tickers else ""
        ticker_label = f" [{tickers}]" if tickers else ""

        sections = [
            f"<b>{SECTION_HEADERS['breaking_title']}{ticker_label}</b>",
            f"<b>{evt.title}</b>",
        ]

        if evt.summary:
            sections.append(evt.summary[:500])

        if alert.market_context:
            ctx_lines = [format_compact_price(q.display_name or q.symbol, q.change_percent)
                         for q in alert.market_context[:4]]
            sections.append("Context: " + " | ".join(ctx_lines))

        if alert.reason:
            sections.append(f"<i>Why it matters: {alert.reason}</i>")

        sections.append(
            f"<i>Score: {evt.final_score:.2f} | "
            f"Confidence: {evt.factual_confidence_score:.0%}</i>"
        )

        full_text = "\n\n".join(sections)
        return self._split_message(full_text)

    # -- Section formatters ---------------------------------------------------

    def _format_market_setup(self, briefing: MorningBriefing) -> str:
        lines = [f"<b>{SECTION_HEADERS['market_setup']}</b>"]

        # Index quotes
        for q in briefing.market_setup.index_quotes:
            name = q.display_name or q.symbol
            lines.append(format_price_line(name, q.current_price, q.change, q.change_percent))

        # Macro instruments (gold, oil, USD, BTC)
        for q in briefing.market_setup.macro_quotes:
            name = q.display_name or q.symbol
            lines.append(format_price_line(name, q.current_price, q.change, q.change_percent))

        # Treasury yields from FRED
        setup = briefing.market_setup
        if setup.treasury_10y:
            chg = f" ({format_change(setup.treasury_10y.change or 0, 0)})" if setup.treasury_10y.change else ""
            lines.append(f"US 10Y: {setup.treasury_10y.value:.3f}%{chg}")
        if setup.treasury_2y:
            chg = f" ({format_change(setup.treasury_2y.change or 0, 0)})" if setup.treasury_2y.change else ""
            lines.append(f"US 2Y: {setup.treasury_2y.value:.3f}%{chg}")

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
        if not themes:
            return ""
        lines = [f"<b>{SECTION_HEADERS['themes']}</b>"]
        for i, evt in enumerate(themes[:MAX_THEMES], 1):
            tickers = f" [{', '.join(evt.tickers[:3])}]" if evt.tickers else ""
            lines.append(f"{i}. <b>{evt.title}</b>{tickers}")
            if evt.summary:
                lines.append(f"   {evt.summary[:200]}")
        return "\n".join(lines)

    def _format_sector_scan(self, sectors: list[SectorSnapshot]) -> str:
        if not sectors:
            return ""
        lines = [f"<b>{SECTION_HEADERS['sectors']}</b>"]
        for snap in sectors:
            # Sector ETF performance
            if snap.etf_quote:
                q = snap.etf_quote
                pct_str = format_change(q.change, q.change_percent)
                lines.append(f"\n<b>{snap.display_name}</b> ({snap.etf_symbol} {pct_str})")
            else:
                lines.append(f"\n<b>{snap.display_name}</b>")

            # Top events in this sector
            for evt in snap.top_events[:MAX_SECTOR_EVENTS]:
                tickers = f" [{', '.join(evt.tickers[:2])}]" if evt.tickers else ""
                lines.append(f"  {evt.title[:120]}{tickers}")

        return "\n".join(lines)

    def _format_earnings(self, earnings: list[EarningsEvent]) -> str:
        if not earnings:
            return ""
        lines = [f"<b>{SECTION_HEADERS['earnings']}</b>"]
        for e in earnings[:MAX_EARNINGS_DISPLAY]:
            time_label = {"bmo": "pre", "amc": "post", "during": "during"}.get(e.time, "")
            est = f" (est. ${e.eps_estimate:.2f})" if e.eps_estimate else ""
            lines.append(f"  {e.symbol} {e.fiscal_quarter} {time_label}{est}")
        return "\n".join(lines)

    def _format_watchlist(
        self, events: list[NormalisedEvent], quotes: list[QuoteData]
    ) -> str:
        parts = [f"<b>{SECTION_HEADERS['watchlist']}</b>"]

        # Watchlist quotes
        if quotes:
            q_lines = [format_compact_price(q.display_name or q.symbol, q.change_percent)
                       for q in quotes[:10]]
            parts.append(" | ".join(q_lines))

        # Watchlist-relevant events
        if events:
            for evt in events[:MAX_WATCHLIST_EVENTS]:
                tickers = f"[{', '.join(evt.tickers[:2])}] " if evt.tickers else ""
                parts.append(f"  {tickers}{evt.title[:150]}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def _format_single_event(self, evt: NormalisedEvent) -> str:
        """Format a single event for intraday/alert context.

        Avoids duplicating the title as the summary, and only shows
        metadata that is genuinely differentiating (not boilerplate).
        """
        tickers = f" [{', '.join(evt.tickers[:3])}]" if evt.tickers else ""
        parts = [f"<b>{evt.title}</b>{tickers}"]

        # Only show summary if it adds information beyond the title
        if evt.summary:
            summary_clean = evt.summary.strip()
            if not self._summary_duplicates_title(summary_clean, evt.title):
                parts.append(summary_clean[:300])

        # Compact metadata: only include what differentiates this item
        meta = []
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

    @staticmethod
    def _summary_duplicates_title(summary: str, title: str) -> bool:
        """Return True if the summary is essentially the title repeated."""
        s = summary.lower().strip()
        t = title.lower().strip()
        # Strip source attributions
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
        if not s_tokens:
            return True
        overlap = len(s_tokens & t_tokens) / max(len(s_tokens), len(t_tokens))
        return overlap >= 0.75

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
