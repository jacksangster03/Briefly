"""Build morning-theme summaries from clustered events.

Summaries should read like a concise institutional note, not a debug dump.
The headline is the core; the summary adds context and cluster breadth.
"""

from __future__ import annotations

from collections.abc import Callable

from app.processing.cleaners import truncate
from app.processing.pipeline import is_actionable_event
from app.schemas.events import NormalisedEvent


def build_top_themes(
    events: list[NormalisedEvent],
    max_themes: int = 5,
    editorial_gate: Callable[[NormalisedEvent], bool] | None = None,
    preferred_symbols: set[str] | None = None,
) -> list[NormalisedEvent]:
    candidates: list[tuple[float, NormalisedEvent]] = []
    preferred_symbols = preferred_symbols or set()
    for evt in events:
        if not is_actionable_event(evt):
            continue
        if (
            evt.personal_relevance_score < 0.55
            and evt.source != "sec_edgar"
            and evt.event_type not in {"macro_release", "fed_decision", "geopolitical", "regulatory"}
        ):
            continue
        if editorial_gate and not editorial_gate(evt):
            continue
        rank = float(evt.final_score or 0.0)
        overlap = set(evt.tickers) & preferred_symbols
        if overlap:
            rank += 0.35
        if evt.cluster_size >= 3:
            rank += 0.08
        if evt.source == "sec_edgar" and not overlap and evt.cluster_size < 2:
            rank -= 0.35
        title_lower = str(evt.title or "").lower()
        if evt.source == "sec_edgar" and not overlap and any(
            token in title_lower for token in ("8-k", "10-q", "10-k", "filing")
        ):
            rank -= 0.2
        candidates.append((rank, evt))

    candidates.sort(key=lambda item: (item[0], item[1].cluster_size, item[1].final_score), reverse=True)

    themes: list[NormalisedEvent] = []
    seen_clusters: set[str] = set()
    for _, evt in candidates:
        if evt.cluster_id and evt.cluster_id in seen_clusters:
            continue
        themed = evt.model_copy(deep=True)
        themed.summary = _build_theme_summary(evt)
        themes.append(themed)
        if themed.cluster_id:
            seen_clusters.add(themed.cluster_id)
        if len(themes) >= max_themes:
            break

    return themes


def _build_theme_summary(evt: NormalisedEvent) -> str:
    """Build a short, natural summary line below a theme headline.

    Style: one sentence of context, plus cluster breadth if >1 source.
    Avoids mechanical patterns like "New catalyst in X. Focus: Y."
    """
    parts: list[str] = []

    # Use the original summary if it adds real info beyond the title
    original = (evt.raw_data.get("summary_original") or evt.summary or "").strip()
    # Strip cluster metadata that prior processing may have appended
    if "[+" in original:
        original = original[: original.rfind("[+")].strip()
    # Only use the original summary if it is meaningfully different from the title
    if original and not _is_near_duplicate(original, evt.title):
        parts.append(truncate(original, 160))

    # Cluster breadth: only attribute sources when at least two distinct
    # outlets have reported. A "3 reports (finnhub)" line implies corroboration
    # that isn't really there.
    if evt.cluster_size > 1:
        distinct_sources = sorted({s for s in evt.raw_data.get("cluster_sources", []) if s})
        if len(distinct_sources) >= 2:
            sources_str = ", ".join(distinct_sources)
            parts.append(f"Corroborated by {evt.cluster_size} reports across {sources_str}.")
        else:
            parts.append(f"{evt.cluster_size} related reports.")

    # Filing confidence note (only for SEC)
    if evt.source == "sec_edgar":
        parts.append("Official filing.")

    # Material update flag
    if evt.update_status == "material_update":
        parts.append("Developing story.")

    if not parts:
        # Fallback: use sector context
        sectors = ", ".join(evt.sectors[:2]) if evt.sectors else ""
        if sectors:
            parts.append(f"Sector: {sectors}.")

    return " ".join(parts)


def _is_near_duplicate(summary: str, title: str) -> bool:
    """Return True if the summary is essentially the same text as the title."""
    s = summary.lower().strip().rstrip(".")
    t = title.lower().strip().rstrip(".")
    # Strip common suffixes like " - Reuters", " | Bloomberg"
    for sep in (" - ", " | ", "  "):
        if sep in s:
            s = s[: s.rfind(sep)].strip()
        if sep in t:
            t = t[: t.rfind(sep)].strip()
    if not s or not t:
        return True
    # Check if one contains the other
    if s in t or t in s:
        return True
    # Token overlap: if 80%+ of words are shared, it's a near-dup
    s_tokens = set(s.split())
    t_tokens = set(t.split())
    if not s_tokens or not t_tokens:
        return True
    overlap = len(s_tokens & t_tokens) / max(len(s_tokens), len(t_tokens))
    return overlap >= 0.80
