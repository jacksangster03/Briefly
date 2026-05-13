"""Deterministic briefing output quality guard.

Performs post-processing on assembled briefing text before dispatch.
Rules are rewrite/suppress-first: the guard does not block sending
unless severity == CRITICAL (currently no critical rules).

Rules applied (in order):
1. Duplicate section headings: remove the second occurrence.
2. VIX unavailable as confirmation: replace "VIX confirms" with "VIX unavailable".
3. Stale Brent as live confirmation: replace with stale caveat.
4. "review diagnostics": replace with context-appropriate wording.
5. Contradictory directional claims for the same metric: suppress second claim.
"""

from __future__ import annotations

import re
from typing import Any

from app.logger import get_logger

logger = get_logger("quality_guard")

# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"<b>([^<]+)</b>", re.IGNORECASE)
_PLAIN_HEADING_RE = re.compile(r"^([A-Z][A-Z /\-&]{3,}[A-Z])$", re.MULTILINE)


def _extract_heading(block: str) -> str:
    """Return the first bold heading from a Telegram-formatted block, normalised."""
    match = _HEADING_RE.search(block)
    if match:
        return match.group(1).strip().upper()
    # Plain-text headings (all-caps lines)
    for line in block.splitlines():
        line = line.strip()
        if _PLAIN_HEADING_RE.match(line):
            return line.upper()
    return ""


# ---------------------------------------------------------------------------
# Direction contradiction detection
# ---------------------------------------------------------------------------

_METRIC_DIRECTION_RE = re.compile(
    r"\b(10[Yy]|10-year|10Y yield|US 10Y|Treasury yield)\b.{0,60}\b(higher|lower|rising|falling)\b",
    re.IGNORECASE,
)


def _extract_direction_claims(text: str) -> list[tuple[str, str, int]]:
    """Return list of (metric_key, direction, position) tuples."""
    claims: list[tuple[str, str, int]] = []
    for m in _METRIC_DIRECTION_RE.finditer(text):
        direction = m.group(2).lower()
        if direction in {"higher", "rising"}:
            direction = "higher"
        elif direction in {"lower", "falling"}:
            direction = "lower"
        else:
            continue
        claims.append(("10Y_yield", direction, m.start()))
    return claims


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class BriefingQualityGuard:
    """Post-process assembled briefing text (list of section strings or full text).

    Usage:
        guard = BriefingQualityGuard(vix_available=False, brent_stale=True)
        cleaned_sections = guard.apply(sections)
    """

    def __init__(
        self,
        *,
        vix_available: bool = True,
        brent_stale: bool = False,
        vix_stale: bool = False,
    ) -> None:
        self.vix_available = vix_available
        self.brent_stale = brent_stale
        self.vix_stale = vix_stale

    def apply(self, sections: list[str]) -> list[str]:
        """Apply all quality rules to a list of section strings.

        Returns the cleaned section list. Never raises.
        """
        try:
            return self._apply_internal(sections)
        except Exception as exc:
            logger.warning("BriefingQualityGuard.apply failed, returning sections unchanged: %s", exc)
            return sections

    def apply_text(self, text: str) -> str:
        """Apply all quality rules to a single text block."""
        try:
            parts = text.split("\n\n")
            cleaned = self._apply_internal(parts)
            return "\n\n".join(cleaned)
        except Exception as exc:
            logger.warning("BriefingQualityGuard.apply_text failed: %s", exc)
            return text

    # -- Internal -------------------------------------------------------------

    # Brent stale caveat note (injected once only)
    _BRENT_STALE_CAVEAT = "Brent stale/provider-held; WTI used for live energy impulse."
    _BRENT_STALE_CAVEAT_RE = re.compile(
        r"Brent stale[^\.]*?(?:WTI[^\.]*?)?(?:energy[^\.]*?)?[\.;]",
        re.IGNORECASE,
    )

    def _apply_internal(self, sections: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen_headings: set[str] = set()
        direction_claims: dict[str, str] = {}  # metric_key -> first seen direction
        brent_caveat_emitted: bool = False

        for block in sections:
            text = str(block or "").strip()
            if not text:
                continue

            # Rule 1: duplicate section headings
            heading = _extract_heading(text)
            if heading:
                if heading in seen_headings:
                    # Remove the heading line from this block
                    text = _HEADING_RE.sub("", text, count=1).strip()
                    if not text:
                        logger.warning(
                            "quality_guard: removed duplicate heading block '%s'", heading
                        )
                        continue
                    logger.warning(
                        "quality_guard: stripped duplicate heading '%s' from block", heading
                    )
                else:
                    seen_headings.add(heading)

            # Rule 2: VIX unavailable
            if not self.vix_available or self.vix_stale:
                original = text
                text = re.sub(r"\bVIX confirms\b", "VIX unavailable", text, flags=re.IGNORECASE)
                text = re.sub(r"\bVIX does not confirm\b", "VIX unavailable", text, flags=re.IGNORECASE)
                if text != original:
                    logger.warning("quality_guard: replaced VIX confirmation language (vix_available=%s)", self.vix_available)

            # Rule 3: stale Brent — replace confirmation language and deduplicate caveat note.
            if self.brent_stale:
                original = text
                text = re.sub(r"\bBrent confirms\b", "Brent stale/provider-held", text, flags=re.IGNORECASE)
                text = re.sub(r"\bBrent does not confirm\b", "Brent stale/provider-held", text, flags=re.IGNORECASE)
                if text != original:
                    logger.warning("quality_guard: replaced Brent live-confirmation language (brent_stale=True)")

                # Deduplicate Brent stale caveat notes that appear across sections.
                if self._BRENT_STALE_CAVEAT_RE.search(text):
                    if brent_caveat_emitted:
                        # Remove the duplicate caveat sentence from this block.
                        text = self._BRENT_STALE_CAVEAT_RE.sub("", text).strip()
                        logger.warning("quality_guard: removed duplicate Brent stale caveat from block")
                    else:
                        brent_caveat_emitted = True

            # Rule 4: "review diagnostics" wording
            original = text
            text = re.sub(
                r"\breview diagnostics\b",
                lambda m: "review risk" if re.search(r"\bportfolio\b", text[:m.start()], re.IGNORECASE) else "monitor confirmation",
                text,
                flags=re.IGNORECASE,
            )
            if text != original:
                logger.warning("quality_guard: replaced 'review diagnostics' wording")

            # Rule 5: contradictory direction claims
            claims = _extract_direction_claims(text)
            for metric_key, direction, pos in claims:
                if metric_key not in direction_claims:
                    direction_claims[metric_key] = direction
                elif direction_claims[metric_key] != direction:
                    # Contradictory: suppress the second directional phrase
                    logger.warning(
                        "quality_guard: contradictory direction claim for %s ('%s' vs '%s'); suppressing second",
                        metric_key, direction_claims[metric_key], direction,
                    )
                    text = _suppress_direction_claim(text, direction, pos)

            if text:
                cleaned.append(text)

        return cleaned


def _suppress_direction_claim(text: str, direction: str, pos: int) -> str:
    """Remove the sentence containing a contradictory directional claim."""
    # Split into sentences and remove the one containing the claim position.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    char_pos = 0
    for sentence in sentences:
        sentence_end = char_pos + len(sentence)
        if char_pos <= pos <= sentence_end and re.search(
            r"\b(10[Yy]|10-year|10Y yield|US 10Y|Treasury yield)\b.{0,60}\b" + re.escape(direction) + r"\b",
            sentence,
            re.IGNORECASE,
        ):
            # Skip this sentence
            char_pos = sentence_end + 1
            continue
        out.append(sentence)
        char_pos = sentence_end + 1
    return " ".join(out)


def apply_quality_guard(
    sections: list[str],
    *,
    vix_available: bool = True,
    brent_stale: bool = False,
    vix_stale: bool = False,
) -> list[str]:
    """Convenience wrapper for pipeline integration."""
    guard = BriefingQualityGuard(
        vix_available=vix_available,
        brent_stale=brent_stale,
        vix_stale=vix_stale,
    )
    return guard.apply(sections)


def flags_from_briefing(briefing: Any) -> dict[str, bool]:
    """Extract quality guard flags from a MorningBriefing object.

    Returns a dict with keys:
        vix_available (bool): True when a VIX quote with a valid price was fetched.
        vix_stale (bool): True when VIX timestamp is older than 6 hours.
        brent_stale (bool): True when Brent freshness state is stale/prior_close.
    """
    basis_lines = [str(line or "") for line in (getattr(briefing, "data_basis_lines", None) or [])]
    freshness = dict(getattr(briefing, "quote_freshness", None) or {})

    # VIX available: prefer market_setup quotes as the ground truth.
    # Fall back to canonical_prices when quotes are absent.
    vix_available = True
    vix_stale = False
    canonical = dict(getattr(briefing, "canonical_prices", None) or {})
    vix_canon = canonical.get("VIX") or {}

    try:
        index_quotes = list(briefing.market_setup.index_quotes or [])
        macro_quotes = list(briefing.market_setup.macro_quotes or [])
        all_quotes = index_quotes + macro_quotes
        vix_quotes = [
            q for q in all_quotes
            if "vix" in (q.symbol or "").lower() or "vix" in (q.display_name or "").lower()
        ]
        if vix_quotes:
            # Found VIX quote object(s); availability requires a non-None, non-zero price.
            vix_available = any(
                q.current_price is not None and float(q.current_price) > 0
                for q in vix_quotes
            )
        elif vix_canon:
            # No quote object but canonical price present
            vix_val = vix_canon.get("value")
            vix_available = vix_val is not None and float(vix_val) > 0
        else:
            # No VIX data at all in this briefing
            vix_available = False
    except Exception:
        # Defensive: fall back to canonical check only
        if vix_canon:
            vix_val = vix_canon.get("value")
            vix_available = vix_val is not None and float(vix_val) > 0
        else:
            vix_available = False

    # VIX stale: check freshness map for the VIX entry.
    vix_meta = (
        freshness.get("VIX")
        or freshness.get("^VIX")
        or {}
    )
    vix_freshness_state = str(vix_meta.get("freshness_state") or "").lower()
    if vix_freshness_state in {"stale", "carried_forward"}:
        vix_stale = True

    # Belt-and-braces: also check data_basis_lines for legacy stale/unavailable markers.
    if any("vix:" in line.lower() and "unavailable" in line.lower() for line in basis_lines):
        vix_available = False

    # Brent stale: check quote_freshness
    brent_stale = False
    brent_meta = freshness.get("BRENT") or freshness.get("BZ=F") or freshness.get("brent") or {}
    freshness_state = str(brent_meta.get("freshness_state") or "").lower()
    if freshness_state in {"stale", "prior_close", "carried_forward"}:
        brent_stale = True
    # Also check basis lines
    if any("brent" in line.lower() and "stale" in line.lower() for line in basis_lines):
        brent_stale = True

    return {
        "vix_available": vix_available,
        "vix_stale": vix_stale,
        "brent_stale": brent_stale,
    }
