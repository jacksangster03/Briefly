"""Deterministic healthcare classifier over existing normalised news events."""

from __future__ import annotations

import re
from typing import Any

from app.healthcare.schemas import HealthcareEvent
from app.healthcare.taxonomy import EVENT_SEVERITY, SEVERITY_ORDER
from app.processing.article_quality import classify_article_type
from app.schemas.events import NormalisedEvent
from app.universe.ticker_metadata import company_name_for_ticker

_REGULATOR_PATTERNS = {
    "FDA": re.compile(r"\b(fda|food and drug administration|complete response letter|crl)\b", re.I),
    "EMA": re.compile(r"\b(ema|chmp|european medicines agency)\b", re.I),
}

_TRIAL_PHASE_PATTERNS = {
    "Phase 1": re.compile(r"\bphase\s*(i|1)\b", re.I),
    "Phase 2": re.compile(r"\bphase\s*(ii|2)\b", re.I),
    "Phase 3": re.compile(r"\bphase\s*(iii|3|pivotal)\b", re.I),
}

_THEME_ALIASES = {
    "GLP-1": ("glp-1", "glp1", "incretin", "tirzepatide", "semaglutide", "wegovy", "ozempic", "mounjaro", "zepbound"),
    "peptide": ("peptide", "peptides", "amino acid peptide"),
    "obesity": ("obesity", "weight loss", "anti-obesity"),
    "diabetes": ("diabetes", "t2d", "type 2 diabetes", "glycemic"),
    "oncology": ("oncology", "tumor", "tumour", "cancer"),
    "rare disease": ("rare disease", "orphan drug"),
    "immunology": ("immunology", "autoimmune", "inflammation"),
    "API": ("api manufacturing", "api supply", "active pharmaceutical ingredient", "api"),
    "CDMO": ("cdmo", "contract development and manufacturing", "contract manufacturing", "fill-finish", "sterile manufacturing"),
    "manufacturing": ("capacity expansion", "fill-finish", "sterile", "manufacturing"),
    "RNA": ("mrna", "rna"),
    "gene therapy": ("gene therapy",),
    "cell therapy": ("cell therapy",),
    "ADC": ("adc", "antibody drug conjugate"),
    "antibody": ("antibody", "monoclonal antibody"),
    "small molecule": ("small molecule",),
}

_HEALTHCARE_TOKENS = (
    "fda",
    "ema",
    "trial",
    "phase 3",
    "biotech",
    "pharma",
    "glp-1",
    "obesity",
    "diabetes",
    "oncology",
    "cdmo",
    "api",
    "peptide",
    "clinical",
)


def classify_healthcare_event(
    event: NormalisedEvent,
    *,
    healthcare_prefs: dict[str, Any],
) -> HealthcareEvent | None:
    text = f"{event.title} {event.summary}".strip()
    text_lower = text.lower()
    if not text_lower:
        return None

    themes_cfg = [str(item).strip().lower() for item in healthcare_prefs.get("themes", []) if str(item).strip()]
    tickers_cfg = {str(item).strip().upper() for item in healthcare_prefs.get("tickers", []) if str(item).strip()}
    assets_cfg = [str(item).strip().lower() for item in healthcare_prefs.get("assets", []) if str(item).strip()]

    has_healthcare_signal = any(token in text_lower for token in _HEALTHCARE_TOKENS)
    cfg_ticker_hit = any(ticker in tickers_cfg for ticker in event.tickers)
    if not has_healthcare_signal and not cfg_ticker_hit:
        return None

    event_type = _infer_event_type(event, text_lower)
    severity = EVENT_SEVERITY.get(event_type, "low")
    source_quality = _infer_source_quality(event)
    trial_phase = _infer_trial_phase(text)
    regulator = _infer_regulator(text)
    themes = _theme_tags(text_lower)
    if themes_cfg:
        themes = [theme for theme in themes if theme.lower() in set(themes_cfg) or theme.lower() in {"glp-1", "cdmo", "api"}]
    asset_names = [asset for asset in assets_cfg if asset and asset in text_lower]

    tickers = [ticker for ticker in event.tickers if ticker]
    if tickers_cfg:
        tickers = [ticker for ticker in tickers if ticker in tickers_cfg] or tickers
    company_names = [company_name_for_ticker(ticker) for ticker in tickers[:4]]

    article_type = classify_article_type(event.title, event.summary, event.url)
    low_signal_like = article_type in {"seo", "listicle", "opinion", "preview"}
    theme_match = bool(themes)
    asset_match = bool(asset_names)
    severity_rank = SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else 0
    allow_generic = cfg_ticker_hit or theme_match or asset_match or severity_rank >= SEVERITY_ORDER.index("high")
    if low_signal_like and not allow_generic:
        return None
    if source_quality in {"generic_news", "low_signal"} and not allow_generic:
        return None

    return HealthcareEvent(
        title=event.title,
        summary=event.summary,
        source=event.source or "",
        source_url=event.url or "",
        published_at=event.published_at,
        event_type=event_type,
        company_tickers=tickers,
        company_names=company_names,
        asset_names=asset_names,
        therapy_areas=_therapy_areas(themes),
        modality=_modalities(themes),
        trial_phase=trial_phase,
        regulator=regulator,
        geography=_infer_geography(text_lower),
        severity=severity,
        source_quality=source_quality,
        relevance_score=0.0,
    )


def _infer_event_type(event: NormalisedEvent, text_lower: str) -> str:
    evt_type = (event.event_type or "").lower()
    if "complete response letter" in text_lower or " crl" in text_lower:
        return "fda_crl"
    if "fda approval" in text_lower or ("approved" in text_lower and "fda" in text_lower):
        return "fda_approval"
    if "safety signal" in text_lower or ("boxed warning" in text_lower and "fda" in text_lower):
        return "fda_safety"
    if "chmp" in text_lower or ("ema" in text_lower and "opinion" in text_lower):
        return "ema_chmp"
    if "trial halt" in text_lower or "placed on hold" in text_lower:
        return "trial_halt"
    if "phase 3" in text_lower and any(token in text_lower for token in ("data", "topline", "met", "failed", "endpoint", "readout")):
        return "clinical_data"
    if "trial initiation" in text_lower or "initiated phase" in text_lower:
        return "trial_start"
    if "trial completion" in text_lower or "completed enrollment" in text_lower:
        return "trial_completion"
    if evt_type in {"m_and_a", "acquisition", "merger"} or "acquire" in text_lower or "merger" in text_lower:
        return "m_and_a"
    if "license" in text_lower or "licensing" in text_lower or "collaboration" in text_lower:
        return "licensing_deal"
    if "capacity" in text_lower and any(token in text_lower for token in ("cdmo", "api", "manufactur", "fill-finish", "sterile")):
        return "manufacturing_capacity"
    if any(token in text_lower for token in ("shortage", "supply disruption", "allocation", "backorder")):
        return "shortage"
    if "reimbursement" in text_lower or "pricing" in text_lower or "medicare" in text_lower:
        return "pricing_reimbursement"
    if evt_type in {"earnings", "guidance"}:
        return "earnings_guidance"
    if event.source == "sec_edgar":
        return "sec_filing"
    return "general_news"


def _infer_regulator(text: str) -> str:
    for name, rx in _REGULATOR_PATTERNS.items():
        if rx.search(text):
            return name
    return ""


def _infer_trial_phase(text: str) -> str:
    for label, rx in _TRIAL_PHASE_PATTERNS.items():
        if rx.search(text):
            return label
    return ""


def _infer_source_quality(event: NormalisedEvent) -> str:
    source = (event.source or "").lower().strip()
    source_name = str((event.raw_data or {}).get("source_name", "")).lower()
    url = (event.url or "").lower()
    hay = f"{source_name} {url} {source}"
    if "fda.gov" in hay or "ema.europa.eu" in hay:
        return "primary_regulator"
    if source == "sec_edgar":
        return "sec"
    if "clinicaltrials.gov" in hay:
        return "clinical_registry"
    if any(token in hay for token in ("investor relations", "press release", "globenewswire", "businesswire", "prnewswire")):
        return "company_ir"
    if any(token in hay for token in ("reuters", "bloomberg", "wsj", "financial times", "associated press", "apnews")):
        return "tier1_wire"
    if any(token in hay for token in ("fiercebiotech", "endpoints", "statnews", "biopharmadive", "scrip", "evaluate")):
        return "trade_press"
    article_type = classify_article_type(event.title, event.summary, event.url)
    if article_type in {"seo", "listicle", "opinion", "preview"}:
        return "low_signal"
    return "generic_news"


def _theme_tags(text_lower: str) -> list[str]:
    tags: list[str] = []
    for theme, aliases in _THEME_ALIASES.items():
        if any(alias in text_lower for alias in aliases):
            tags.append(theme)
    return tags


def _therapy_areas(theme_tags: list[str]) -> list[str]:
    area_candidates = {"obesity", "diabetes", "oncology", "rare disease", "immunology", "metabolic disease"}
    return [tag for tag in theme_tags if tag.lower() in area_candidates]


def _modalities(theme_tags: list[str]) -> list[str]:
    modality_candidates = {
        "peptide",
        "GLP-1",
        "incretin",
        "small molecule",
        "antibody",
        "ADC",
        "gene therapy",
        "cell therapy",
        "RNA",
        "manufacturing",
        "CDMO",
        "API",
    }
    return [tag for tag in theme_tags if tag in modality_candidates]


def _infer_geography(text_lower: str) -> str:
    if "europe" in text_lower or "eu " in text_lower or " ema" in text_lower:
        return "EU"
    if "us " in text_lower or "fda" in text_lower or "medicare" in text_lower:
        return "US"
    return ""

