"""Global news hub: multi-source ingestion with canonicalization and dedupe."""

from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Callable
from urllib.parse import urlparse, urlunparse

from app.data_sources.providers.alphavantage_news import AlphaVantageNewsProvider
from app.data_sources.providers.fmp_news import FMPNewsProvider
from app.data_sources.providers.gdelt import GDELTProvider
from app.data_sources.providers.mediastack import MediastackProvider
from app.logger import get_logger
from app.schemas.events import NormalisedEvent
from app.settings import Settings

logger = get_logger("global_news_hub")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def _normalise_title(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (value or "").lower())
    tokens = [token for token in cleaned.split() if token and token not in _STOPWORDS]
    return " ".join(tokens[:16])


class GlobalNewsHubService:
    """Merge market news from core + optional global providers."""

    # Process-level daily call usage: (provider_name, date_iso) -> calls.
    _daily_usage: dict[tuple[str, str], int] = {}

    _SOURCE_PRIORITY = {
        "sec_edgar": 6,
        "finnhub": 5,
        "newsapi": 4,
        "alpha_vantage": 3,
        "gdelt": 2,
        "fmp": 2,
        "mediastack": 1,
    }

    def __init__(
        self,
        settings: Settings,
        *,
        finnhub=None,
        newsapi=None,
        gdelt: GDELTProvider | None = None,
        alpha_vantage: AlphaVantageNewsProvider | None = None,
        fmp: FMPNewsProvider | None = None,
        mediastack: MediastackProvider | None = None,
    ):
        self.settings = settings
        self.finnhub = finnhub
        self.newsapi = newsapi
        self.gdelt = gdelt or (
            GDELTProvider(
                base_url=settings.gdelt_base_url,
                timeout=max(8, min(settings.provider_timeout, 15)),
                max_retries=1,
            )
            if settings.enable_gdelt
            else None
        )
        self.alpha_vantage = alpha_vantage or (
            AlphaVantageNewsProvider(
                api_key=settings.alpha_vantage_api_key,
                base_url=settings.alpha_vantage_base_url,
                timeout=max(8, min(settings.provider_timeout, 15)),
                max_retries=1,
            )
            if settings.enable_alpha_vantage_news and settings.alpha_vantage_configured
            else None
        )
        self.fmp = fmp or (
            FMPNewsProvider(
                api_key=settings.fmp_api_key,
                base_url=settings.fmp_base_url,
                timeout=max(8, min(settings.provider_timeout, 15)),
                max_retries=1,
            )
            if settings.enable_fmp_news and settings.fmp_configured
            else None
        )
        self.mediastack = mediastack or (
            MediastackProvider(
                api_key=settings.mediastack_api_key,
                base_url=settings.mediastack_base_url,
                timeout=max(8, min(settings.provider_timeout, 15)),
                max_retries=1,
            )
            if settings.enable_mediastack_news and settings.mediastack_configured
            else None
        )
        self.last_run_stats: dict[str, object] = {}

    def fetch_market_news(self) -> list[NormalisedEvent]:
        """Fetch and merge market news from all active hub providers."""
        events: list[NormalisedEvent] = []
        contributions: dict[str, int] = {}

        def _pull(name: str, producer: Callable[[], list[NormalisedEvent]]) -> None:
            try:
                rows = producer()
            except Exception:
                logger.warning("Global hub provider failed: %s", name, exc_info=True)
                rows = []
            contributions[name] = len(rows)
            events.extend(rows)

        if self.finnhub and self.finnhub.is_configured():
            _pull("finnhub", self.finnhub.get_market_news)
        if self.newsapi and self.newsapi.is_configured():
            _pull("newsapi", self.newsapi.get_top_headlines)

        if self.gdelt and self.gdelt.is_configured() and self._consume_budget("gdelt", self.settings.gdelt_daily_call_budget):
            _pull(
                "gdelt",
                lambda: self.gdelt.get_market_news(
                    query=self.settings.gdelt_global_query,
                    max_records=self.settings.global_news_max_records,
                ),
            )

        if (
            self.alpha_vantage
            and self.alpha_vantage.is_configured()
            and self._consume_budget("alpha_vantage_news", self.settings.alpha_vantage_news_daily_call_budget)
        ):
            _pull(
                "alpha_vantage",
                lambda: self.alpha_vantage.get_market_news(
                    topics=self.settings.alpha_vantage_topics,
                    limit=self.settings.global_news_max_records,
                ),
            )

        if self.fmp and self.fmp.is_configured() and self._consume_budget("fmp_news", self.settings.fmp_news_daily_call_budget):
            _pull(
                "fmp",
                lambda: self.fmp.get_market_news(limit=self.settings.fmp_news_limit),
            )

        if (
            self.mediastack
            and self.mediastack.is_configured()
            and self._consume_budget("mediastack_news", self.settings.mediastack_news_daily_call_budget)
        ):
            _pull(
                "mediastack",
                lambda: self.mediastack.get_market_news(limit=self.settings.mediastack_news_limit),
            )

        canonicalized = [self._canonicalize_event(event) for event in events]
        deduped, duplicate_count = self._dedupe_story_fingerprints(canonicalized)

        self.last_run_stats = {
            "provider_contributions": contributions,
            "fetched_total": len(canonicalized),
            "deduped_total": len(deduped),
            "duplicates_suppressed": duplicate_count,
        }

        logger.info(
            "Global hub merged %d events -> %d after fingerprint dedupe (suppressed: %d) | contributions=%s",
            len(canonicalized),
            len(deduped),
            duplicate_count,
            contributions,
        )
        return deduped

    def _consume_budget(self, provider_name: str, budget: int) -> bool:
        if budget <= 0:
            return False
        today_key = date.today().isoformat()
        key = (provider_name, today_key)
        current = self._daily_usage.get(key, 0)
        if current >= budget:
            logger.info("%s budget exhausted (%d/%d), skipping provider call", provider_name, current, budget)
            return False
        self._daily_usage[key] = current + 1
        return True

    def _canonicalize_event(self, event: NormalisedEvent) -> NormalisedEvent:
        raw = dict(event.raw_data or {})
        canonical_url = self._canonicalize_url(event.url)
        domain = self._extract_domain(canonical_url)
        source_name = (
            str(raw.get("source_name", "")).strip()
            or str(raw.get("source", "")).strip()
            or domain
            or event.source
        )
        provider_event_id = (
            str(raw.get("provider_event_id", "")).strip()
            or str(raw.get("id", "")).strip()
            or canonical_url
        )
        fingerprint = self._story_fingerprint(event, domain)

        raw["canonical_url"] = canonical_url
        raw["domain"] = domain
        raw["source_name"] = source_name
        raw["provider_event_id"] = provider_event_id
        raw["story_fingerprint"] = fingerprint
        event.raw_data = raw
        if canonical_url:
            event.url = canonical_url
        if not event.content_hash:
            event.compute_hash()
        return event

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url.strip())
        if not parsed.scheme:
            return url.strip()
        canonical = parsed._replace(fragment="", query="")
        return urlunparse(canonical).strip()

    @staticmethod
    def _extract_domain(url: str) -> str:
        if not url:
            return ""
        domain = urlparse(url).netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def _story_fingerprint(self, event: NormalisedEvent, domain: str) -> str:
        title_key = _normalise_title(event.title)
        ticker_key = ",".join(sorted(set(event.tickers)))
        time_bucket = self._time_bucket(event.published_at)
        return f"{title_key}|{domain}|{ticker_key}|{time_bucket}"

    @staticmethod
    def _time_bucket(published_at: datetime | None) -> str:
        if published_at is None:
            return "unknown"
        if published_at.tzinfo is None:
            published = published_at.replace(tzinfo=timezone.utc)
        else:
            published = published_at.astimezone(timezone.utc)
        return published.strftime("%Y%m%d%H")

    def _dedupe_story_fingerprints(
        self,
        events: list[NormalisedEvent],
    ) -> tuple[list[NormalisedEvent], int]:
        best_by_fp: dict[str, NormalisedEvent] = {}
        duplicate_count = 0
        for event in events:
            fingerprint = str(event.raw_data.get("story_fingerprint", "")).strip()
            if not fingerprint:
                fingerprint = self._story_fingerprint(event, self._extract_domain(event.url))

            current = best_by_fp.get(fingerprint)
            if current is None:
                best_by_fp[fingerprint] = event
                continue

            duplicate_count += 1
            if self._quality_tuple(event) > self._quality_tuple(current):
                best_by_fp[fingerprint] = event

        return list(best_by_fp.values()), duplicate_count

    def _quality_tuple(self, event: NormalisedEvent) -> tuple[float, float, int, int]:
        return (
            float(event.factual_confidence_score),
            float(event.cluster_size),
            int(self._SOURCE_PRIORITY.get(event.source, 0)),
            len((event.summary or "").strip()),
        )
