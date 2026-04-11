"""Email-oriented formatting with inline charts for richer morning delivery."""

from __future__ import annotations

import html
import re
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.briefing.formatter import TelegramFormatter
from app.schemas.briefings import MorningBriefing
from app.schemas.delivery import EmailRenderResult
from app.schemas.events import NormalisedEvent, QuoteData
from app.universe.ticker_metadata import format_company_ticker_list

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class EmailFormatter:
    """Render a richer email body while keeping selection logic shared."""

    def __init__(self, timezone_name: str = "UTC"):
        self.telegram_formatter = TelegramFormatter(timezone_name)
        try:
            self.local_tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.local_tz = ZoneInfo("UTC")

    def format_morning_briefing(self, briefing: MorningBriefing) -> EmailRenderResult:
        subject = self._subject(briefing)
        plain_text = self._plain_text(briefing)
        html_body = self._html_body(briefing)
        return EmailRenderResult(
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
            inline_assets=briefing.chart_assets,
        )

    def _subject(self, briefing: MorningBriefing) -> str:
        date_str = briefing.generated_at.strftime("%a %d %b")
        if briefing.session_mode in {"saturday", "sunday"}:
            return f"Weekend Briefing | {date_str}"
        return f"Morning Briefing | {date_str}"

    def _plain_text(self, briefing: MorningBriefing) -> str:
        messages = self.telegram_formatter.format_morning_briefing(briefing)
        return "\n\n".join(_HTML_TAG_RE.sub("", msg) for msg in messages)

    def _html_body(self, briefing: MorningBriefing) -> str:
        title = self._subject(briefing)
        lead = (
            "Weekend mode is active, so this note emphasizes Friday close context, portfolio relevance, and what to watch into the next reopen."
            if briefing.session_mode in {"saturday", "sunday"}
            else "A concise market-intelligence note built from the highest-signal developments in the latest cycle."
        )

        parts = [
            "<html><body style=\"margin:0;padding:0;background:#eef3f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#102a43;\">",
            "<div style=\"max-width:820px;margin:0 auto;padding:24px;\">",
            "<div style=\"background:#0f172a;color:#f8fafc;border-radius:18px;padding:24px 28px;\">",
            f"<div style=\"font-size:28px;font-weight:700;line-height:1.2;\">{html.escape(title)}</div>",
            f"<div style=\"margin-top:10px;font-size:15px;line-height:1.6;color:#dbeafe;\">{html.escape(lead)}</div>",
            "</div>",
        ]

        if briefing.chart_assets:
            parts.append("<div style=\"margin-top:18px;display:grid;gap:18px;\">")
            for asset in briefing.chart_assets:
                parts.append(
                    "<div style=\"background:#ffffff;border:1px solid #d8e2ed;border-radius:18px;padding:18px;\">"
                    f"<div style=\"font-size:18px;font-weight:700;color:#102a43;margin-bottom:8px;\">{html.escape(asset.title)}</div>"
                    f"<img src=\"cid:{html.escape(asset.content_id)}\" alt=\"{html.escape(asset.title)}\" "
                    "style=\"display:block;width:100%;max-width:760px;border-radius:14px;\">"
                    f"<div style=\"margin-top:10px;font-size:13px;line-height:1.5;color:#486581;\">{html.escape(asset.caption)}</div>"
                    "</div>"
                )
            parts.append("</div>")

        market_block = self._market_setup_block(briefing.market_setup.index_quotes, briefing.market_setup.macro_quotes)
        if market_block:
            parts.append(self._section("Market Setup", market_block))

        portfolio_block = self._event_block(briefing.portfolio_focus, max_items=5)
        if portfolio_block:
            parts.append(self._section("Portfolio Focus", portfolio_block))

        theme_title = "Weekend Developments" if briefing.session_mode in {"saturday", "sunday"} else "Top Themes"
        themes_block = self._event_block(briefing.top_themes, max_items=5, ordered=True)
        if themes_block:
            parts.append(self._section(theme_title, themes_block))

        watchlist_block = self._watchlist_block(briefing.watchlist_quotes, briefing.watchlist_events)
        if watchlist_block:
            parts.append(self._section("Watchlist", watchlist_block))

        footer = (
            f"{briefing.events_fetched} fetched | "
            f"{briefing.events_after_dedup} unique | "
            f"{briefing.events_sent} surfaced"
        )
        parts.append(
            f"<div style=\"margin:22px 0 6px 0;font-size:12px;color:#829ab1;\">{html.escape(footer)}</div>"
        )
        parts.append("</div></body></html>")
        return "".join(parts)

    def _market_setup_block(self, indices: list[QuoteData], macro_quotes: list[QuoteData]) -> str:
        selected_quotes = indices[:4] + macro_quotes[:4]
        rows = []
        for quote in selected_quotes:
            sign = "+" if quote.change_percent >= 0 else ""
            rows.append(
                "<tr>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #e6edf5;\">{html.escape(quote.display_name or quote.symbol)}</td>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #e6edf5;text-align:right;\">{quote.current_price:,.2f}</td>"
                f"<td style=\"padding:8px 10px;border-bottom:1px solid #e6edf5;text-align:right;color:{'#0f9d58' if quote.change_percent >= 0 else '#d93025'};\">{sign}{quote.change_percent:.2f}%</td>"
                "</tr>"
            )
        if not rows:
            return ""
        table = (
            "<table style=\"width:100%;border-collapse:collapse;font-size:14px;\">"
            + "".join(rows)
            + "</table>"
        )
        freshness = self._quotes_freshness_summary(selected_quotes)
        if freshness:
            table += (
                f"<div style=\"margin-top:8px;font-size:12px;color:#829ab1;\">{html.escape(freshness)}</div>"
            )
        return table

    def _event_block(
        self,
        events: list[NormalisedEvent],
        *,
        max_items: int,
        ordered: bool = False,
    ) -> str:
        if not events:
            return ""
        items = []
        tag = "ol" if ordered else "ul"
        for event in events[:max_items]:
            summary = ""
            if event.summary and not self.telegram_formatter._summary_duplicates_title(event.summary, event.title):
                summary = self.telegram_formatter._strip_cluster_suffix(event.summary)
            meta = self._event_meta(event)
            body = (
                f"<li style=\"margin:0 0 12px 0;\">"
                f"<div style=\"font-weight:700;color:#102a43;\">{html.escape(event.title)}</div>"
                + (
                    f"<div style=\"margin-top:4px;color:#486581;line-height:1.55;\">{html.escape(summary[:220])}</div>"
                    if summary
                    else ""
                )
                + (
                    f"<div style=\"margin-top:4px;font-size:12px;color:#829ab1;\">{html.escape(meta)}</div>"
                    if meta
                    else ""
                )
                + "</li>"
            )
            items.append(body)
        return f"<{tag} style=\"padding-left:20px;margin:0;\">" + "".join(items) + f"</{tag}>"

    def _watchlist_block(self, quotes: list[QuoteData], events: list[NormalisedEvent]) -> str:
        parts = []
        if quotes:
            compact = []
            for quote in quotes[:8]:
                sign = "+" if quote.change_percent >= 0 else ""
                compact.append(f"{quote.display_name or quote.symbol} {sign}{quote.change_percent:.2f}%")
            parts.append(
                f"<div style=\"margin-bottom:12px;font-size:14px;color:#334e68;\">{' | '.join(html.escape(x) for x in compact)}</div>"
            )
            freshness = self._quotes_freshness_summary(quotes)
            if freshness:
                parts.append(
                    f"<div style=\"margin:-6px 0 12px 0;font-size:12px;color:#829ab1;\">{html.escape(freshness)}</div>"
                )
        event_block = self._event_block(events, max_items=5)
        if event_block:
            parts.append(event_block)
        return "".join(parts)

    def _event_meta(self, event: NormalisedEvent) -> str:
        meta = []
        company = format_company_ticker_list(event.tickers)
        if company:
            meta.append(company)
        if event.published_at:
            dt = event.published_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            meta.append(dt.astimezone(self.local_tz).strftime("%H:%M %Z"))
        if event.cluster_size > 1:
            meta.append(f"{event.cluster_size} reports")
        return " | ".join(meta)

    @staticmethod
    def _section(title: str, body: str) -> str:
        return (
            "<div style=\"margin-top:18px;background:#ffffff;border:1px solid #d8e2ed;border-radius:18px;padding:20px 22px;\">"
            f"<div style=\"font-size:18px;font-weight:700;color:#102a43;margin-bottom:12px;\">{html.escape(title)}</div>"
            f"{body}"
            "</div>"
        )

    def _quotes_freshness_summary(self, quotes: list[QuoteData]) -> str:
        if not quotes:
            return ""
        latest = max(quotes, key=lambda quote: self._coerce_utc_ts(quote.timestamp))
        latest_local = self._coerce_utc_ts(latest.timestamp).astimezone(self.local_tz).strftime("%H:%M %Z")
        source_counts: dict[str, int] = {}
        for quote in quotes:
            source = (quote.source or "unknown").lower()
            source_counts[source] = source_counts.get(source, 0) + 1
        source_summary = ", ".join(
            f"{source}({count})"
            for source, count in sorted(source_counts.items(), key=lambda item: item[0])
        )
        return f"Quotes as of {latest_local} | sources: {source_summary}"

    @staticmethod
    def _coerce_utc_ts(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
