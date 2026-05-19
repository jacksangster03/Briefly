# API Setup (Local) — Vertical Intelligence Phase 1

## 1) Quick start
1. Copy an env template:
   - `cp .env.example .env`
   - or use `myenv.example` as a minimal local variant.
2. Fill only the keys you need (leave others blank).
3. Keep vertical shadow output off by default:
   - `VERTICALS_INCLUDE_IN_BRIEFING=false`
4. Run diagnostics safely (no live sends required).

## 2) Free / no-key sources
- **GDELT**: no key required; geopolitics headline/event density input.
- **ClinicalTrials.gov API v2**: no key required; official trials data.
- **arXiv API**: no key required; AI/Tech research trend context.
- **SEC EDGAR**: no API key, but requires a responsible `SEC_USER_AGENT`.
- **GitHub**: public low-rate access can work without token; token is optional.

## 3) Free key recommended
- **openFDA**: free key optional but recommended for better reliability/rate limits.
- **NewsAPI / Finnhub**: existing optional confirmation/news inputs already used in project.

## 4) Source role by vertical
- **Geopolitics**
  - Primary: GDELT
  - Confirmation only: NewsAPI/Finnhub
  - Planned: official sanctions/government feeds
- **Healthcare / Pharma**
  - ClinicalTrials.gov (trials)
  - openFDA (safety/recall/device/drug signals)
  - SEC EDGAR (filings/material updates)
  - EMA sources where configured
- **AI / Tech**
  - SEC EDGAR (filings/material events)
  - arXiv (research trend context)
  - GitHub (open-source momentum context, optional)
  - Hugging Face remains planned/disabled in Phase 1

## 5) Safety and authority
- Deterministic scoring is authoritative.
- LLM usage is shadow-only and not ranking/eligibility authority.
- API/RSS-first policy (no scraping-first implementation).
- No full article text storage in shared vertical source archive.
- Source failures degrade to diagnostics; they do not crash briefing generation.

## 6) Useful commands
```bash
python -m app.cli verticals-status --verbose
python -m app.cli verticals-history
python -m app.cli schedule-status

python -m pytest app/tests/test_vertical_event_schema.py app/tests/test_geopolitics_sources.py app/tests/test_healthcare_sources.py app/tests/test_ai_tech_sources.py app/tests/test_vertical_source_diagnostics.py app/tests/test_vertical_briefing_shadow.py -q
python -m pytest app/tests -q
```

## 7) Troubleshooting
- **SEC disabled**: check `SEC_USER_AGENT` is set with app/contact format.
- **GDELT timeout/error**: source status should show `error` with `last_error`.
- **openFDA rate-limited**: add optional free key and retry later.
- **GitHub rate-limited**: use optional `GITHUB_TOKEN` if needed.
- **No vertical lines in briefing**: expected when `VERTICALS_INCLUDE_IN_BRIEFING=false`.
- **Source status disabled/stub_inactive/error**: expected fail-soft behaviour for optional sources.
