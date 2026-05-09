"""Phase 4.3 FastAPI + HTMX Briefly control center."""

from __future__ import annotations

from datetime import date as _date_cls, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.db.session import init_db
from app.logger import get_logger
from app.settings import Settings, get_settings
from app.briefing.chart_builder import MorningChartBuilder
from app.briefing.macro_policy_service import build_macro_policy_dashboard
from app.briefing.watchlist_chart_service import build_watchlist_chart_spec
from app.data_sources.macro_data import MacroDataService
from app.data_sources.market_data import MarketDataService
from app.risk.tearsheet import build_tearsheet_html
from app.schemas.briefings import MarketSetup, MorningBriefing
from app.allocation.service import (
    build_actual_allocation,
    default_allocation_targets,
    load_allocation_targets,
    merge_targets_with_catalog,
)
from app.web.control_plane_service import (
    _load_profile_defaults,
    apply_preference_updates,
    build_profile_state,
    import_holdings_from_upload,
    refresh_risk_for_profile,
    remove_preference,
    reset_preferences,
    save_allocation,
    save_benchmark,
    save_cma,
    save_cma_corr,
    save_holdings_from_form,
    save_policy,
    save_rebalancing_config_for_profile,
    save_risk_config,
    search_followables,
)
from app.universe.sector_universe import load_sector_universe
from app.onboarding.easy_setup import EasySetupInputs, apply_plan, build_plan, default_inputs

logger = get_logger("web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_INVESTOR_TYPE_VALUES = {"individual", "family_office", "advisor", "institutional", "model_portfolio", "other"}
_BASE_CURRENCY_VALUES = {"EUR", "USD", "GBP", "CHF", "JPY", "OTHER"}
_REBALANCING_POLICY_VALUES = {"threshold", "calendar", "hybrid"}
_GOVERNANCE_FREQUENCY_VALUES = {"monthly", "quarterly", "semi_annual", "annual"}
_ALLOCATION_ROLE_VALUES = {"growth", "income", "diversifier", "hedge", "liquidity", "tactical", "other"}

_ALL_SECTIONS = [
    "section-easy-setup",
    "section-briefing-home",
    "section-overview",
    "section-analyzer",
    "section-policy",
    "section-allocation",
    "section-risk",
    "section-cma",
    "section-scenarios",
    "section-rebalancing",
    "section-attribution",
    "section-simulation",
    "section-benchmark",
    "section-holdings",
    "section-coverage",
    "section-delivery",
    "section-verticals",
    "section-morning",
    "section-briefing-morning-charts",
    "section-audit",
    "section-audit-home",
    # Phase 7
    "section-bonds",
    "section-reports",
    "section-esg",
    "section-fx",
]

_PAGE_CONTEXTS: dict[str, dict[str, Any]] = {
    "briefing_home": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "home",
        "visible_sections": ["section-briefing-home"],
    },
    "briefing_watchlists": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "watchlists",
        "visible_sections": ["section-coverage"],
    },
    "briefing_delivery": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "delivery",
        "visible_sections": ["section-delivery"],
    },
    "briefing_verticals": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "verticals",
        "visible_sections": ["section-verticals"],
    },
    "briefing_morning": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "morning",
        "visible_sections": ["section-morning"],
    },
    "briefing_morning_charts": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "morning_charts",
        "visible_sections": ["section-briefing-morning-charts"],
    },
    "briefing_history": {
        "global_nav": "briefing",
        "workspace": "briefing",
        "workspace_page": "history",
        "visible_sections": [],
    },
    "portfolio_home": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "overview",
        "visible_sections": ["section-overview"],
    },
    "portfolio_easy_setup": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "easy_setup",
        "visible_sections": ["section-easy-setup"],
    },
    "portfolio_builder_holdings": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "builder",
        "builder_tab": "holdings",
        "visible_sections": ["section-holdings"],
    },
    "portfolio_builder_policy": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "builder",
        "builder_tab": "policy",
        "visible_sections": ["section-policy"],
    },
    "portfolio_builder_allocation": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "builder",
        "builder_tab": "allocation",
        "visible_sections": ["section-allocation"],
    },
    "portfolio_builder_benchmark": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "builder",
        "builder_tab": "benchmark",
        "visible_sections": ["section-benchmark"],
    },
    "portfolio_diagnostics": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "diagnostics",
        "visible_sections": ["section-analyzer"],
    },
    "portfolio_risk": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "risk",
        "visible_sections": ["section-risk"],
    },
    "portfolio_cma": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "cma",
        "visible_sections": ["section-cma"],
    },
    "portfolio_scenarios": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "scenarios",
        "visible_sections": ["section-scenarios"],
    },
    "portfolio_implementation": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "implementation",
        "visible_sections": ["section-rebalancing"],
    },
    "portfolio_history": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "history",
        "visible_sections": ["section-audit"],
    },
    "portfolio_attribution": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "attribution",
        "visible_sections": ["section-attribution"],
    },
    "portfolio_simulation": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "simulation",
        "visible_sections": ["section-simulation"],
    },
    "audit_home": {
        "global_nav": "audit",
        "workspace": "audit",
        "workspace_page": "home",
        "visible_sections": ["section-audit-home"],
    },
    "audit_history": {
        "global_nav": "audit",
        "workspace": "audit",
        "workspace_page": "history",
        "visible_sections": ["section-audit"],
    },
    "audit_overrides": {
        "global_nav": "audit",
        "workspace": "audit",
        "workspace_page": "overrides",
        "visible_sections": ["section-audit"],
    },
    "audit_logs": {
        "global_nav": "audit",
        "workspace": "audit",
        "workspace_page": "logs",
        "visible_sections": ["section-audit"],
    },
    # Phase 7
    "portfolio_bonds": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "bonds",
        "visible_sections": ["section-bonds"],
    },
    "portfolio_reports": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "reports",
        "visible_sections": ["section-reports"],
    },
    "portfolio_esg": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "esg",
        "visible_sections": ["section-esg"],
    },
    "portfolio_fx": {
        "global_nav": "portfolio",
        "workspace": "portfolio",
        "workspace_page": "fx",
        "visible_sections": ["section-fx"],
    },
}


class PreferenceUpdateRequest(BaseModel):
    """Bulk preference payload for API updates."""

    updates: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class AllocationUpdateRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class BenchmarkUpdateRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class CMAUpdateRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class CMACorrelationsUpdateRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class RebalancingConfigUpdateRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class SimulationRunRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


def create_web_app(settings: Settings | None = None) -> FastAPI:
    """Create the Briefly control-center app with API + HTMX routes."""
    app = FastAPI(
        title="Briefly control center",
        version="4.3",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.settings = settings or get_settings()
    init_db()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui", status_code=307)

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui_home(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        return templates.TemplateResponse(
            request,
            "ui_home.html",
            {
                "state": state,
            },
        )

    @app.get("/ui/settings", response_class=HTMLResponse, include_in_schema=False)
    def ui_settings(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_home",
        )

    @app.get("/ui/briefing", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_home(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_home",
        )

    @app.get("/ui/briefing/watchlists", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_watchlists(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_watchlists",
        )

    @app.get("/ui/briefing/delivery", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_delivery(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_delivery",
        )

    @app.get("/ui/briefing/morning", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_morning(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_morning",
        )

    @app.get("/ui/briefing/verticals", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_verticals(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_verticals",
        )

    @app.get("/ui/briefing/morning/charts", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_morning_charts(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        preview = _build_morning_chart_preview(
            settings=_settings(request),
            profile=normalized_profile,
        )
        state.setdefault("metadata", {})["morning_chart_preview"] = preview
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="briefing_morning_charts",
            state=state,
            extra_context={"morning_chart_preview": preview},
        )

    @app.get("/ui/briefing/charts/watchlist", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_watchlist_charts(
        request: Request,
        profile: str = Query(default="default_user"),
        period: str = Query(default="1M"),
        mode: str = Query(default="rebased"),
        benchmark: str = Query(default="none"),
        symbols: str = Query(default=""),
        include_events: bool = Query(default=False),
    ):
        normalized_profile = _normalize_profile(profile)
        selected_symbols = [token.strip().upper() for token in symbols.split(",") if token.strip()]
        return templates.TemplateResponse(
            "watchlist_chart_explorer.html",
            {
                "request": request,
                "profile": normalized_profile,
                "default_period": period,
                "default_mode": mode,
                "default_benchmark": benchmark,
                "default_symbols": selected_symbols,
                "default_include_events": bool(include_events),
            },
        )

    @app.get("/ui/briefing/macro", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_macro_dashboard(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        settings = _settings(request)
        user_profile = _load_profile_defaults(settings, normalized_profile)
        payload = build_macro_policy_dashboard(
            profile=user_profile,
            settings=settings,
        )
        return templates.TemplateResponse(
            "macro_dashboard.html",
            {
                "request": request,
                "profile": normalized_profile,
                "payload": payload,
            },
        )

    # ── Phase 9.4: Briefing history UI ──────────────────────────────────────

    _HISTORY_SESSION_SLOTS: list[tuple[str, str, str]] = [
        ("morning",          "Morning Brief",    "07:00 – 10:30"),
        ("europe_midday",    "Europe Midday",    "12:00 – 13:00"),
        ("us_pre_open",      "US Pre-Open",      "14:00 – 15:30"),
        ("us_intraday_risk", "US Intraday Risk", "16:00 – 18:00"),
        ("into_close",       "Into Close",       "20:00 – 22:00"),
        ("closing_wrap",     "Closing Wrap",     "22:00 – 23:59"),
    ]

    def _history_prev_weekday(d: _date_cls) -> _date_cls:
        prev = d - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _history_next_weekday(d: _date_cls) -> _date_cls:
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt

    def _build_history_sessions(profile_name: str, selected_date: _date_cls) -> list[dict]:
        from app.briefing.session_snapshot_service import list_session_snapshots
        snapshots = list_session_snapshots(profile_name, selected_date)
        snap_by_key = {s["session_key"]: s for s in snapshots}
        sessions = []
        for key, label, window in _HISTORY_SESSION_SLOTS:
            snap = snap_by_key.get(key)
            if snap is None:
                status = "missing"
            elif snap["delivery_success"]:
                status = "sent"
            elif snap["delivery_attempted"]:
                status = "failed"
            else:
                status = "skipped"
            sessions.append({
                "key": key,
                "label": label,
                "window": window,
                "snap": snap,
                "status": status,
            })
        return sessions

    @app.get("/ui/briefing/history", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_history(
        request: Request,
        profile: str = Query(default="default_user"),
        date: str = Query(default=""),
    ):
        normalized_profile = _normalize_profile(profile)
        today = _date_cls.today()
        try:
            selected_date = _date_cls.fromisoformat(date) if date else today
        except ValueError:
            selected_date = today
        if selected_date > today:
            selected_date = today

        sessions = _build_history_sessions(normalized_profile, selected_date)
        prev_date = _history_prev_weekday(selected_date)
        next_wd = _history_next_weekday(selected_date)
        next_date = next_wd.isoformat() if next_wd <= today else ""

        return templates.TemplateResponse(
            request,
            "briefing_history.html",
            {
                "profile": normalized_profile,
                "selected_date": selected_date.isoformat(),
                "selected_date_display": selected_date.strftime("%A, %-d %B %Y"),
                "today": today.isoformat(),
                "sessions": sessions,
                "prev_date": prev_date.isoformat(),
                "next_date": next_date,
                "has_any_data": any(s["snap"] is not None for s in sessions),
            },
        )

    @app.get("/ui/briefing/history/session", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_history_session(
        request: Request,
        profile: str = Query(default="default_user"),
        date: str = Query(default=""),
        session: str = Query(default=""),
        format: str = Query(default="telegram"),
    ):
        normalized_profile = _normalize_profile(profile)
        try:
            target_date = _date_cls.fromisoformat(date)
        except ValueError:
            return HTMLResponse("<p style='padding:20px;color:var(--muted)'>Invalid date.</p>")
        from app.briefing.session_snapshot_service import get_session_snapshot
        snap = get_session_snapshot(normalized_profile, target_date, session)
        if snap is None:
            return HTMLResponse(
                "<p style='padding:20px;color:var(--muted)'>No snapshot stored for this session.</p>"
            )
        allowed_formats = {"telegram", "email-text", "email-html", "summary"}
        fmt = format if format in allowed_formats else "telegram"
        return templates.TemplateResponse(
            request,
            "partials/history_detail.html",
            {"snap": snap, "format": fmt, "profile": normalized_profile, "date": date},
        )

    @app.get("/ui/briefing/history/session/raw-html", response_class=HTMLResponse, include_in_schema=False)
    def ui_briefing_history_session_raw_html(
        request: Request,
        profile: str = Query(default="default_user"),
        date: str = Query(default=""),
        session: str = Query(default=""),
    ):
        """Serve raw email HTML for iframe embedding — no external resources called."""
        normalized_profile = _normalize_profile(profile)
        try:
            target_date = _date_cls.fromisoformat(date)
        except ValueError:
            return HTMLResponse("<html><body><p>Invalid date.</p></body></html>")
        from app.briefing.session_snapshot_service import get_session_snapshot
        snap = get_session_snapshot(normalized_profile, target_date, session)
        if not snap or not snap.get("email_html"):
            return HTMLResponse("<html><body><p>No email HTML stored for this session.</p></body></html>")
        return HTMLResponse(content=snap["email_html"])

    # ── /ui/tearsheet ────────────────────────────────────────────────────────

    @app.get("/ui/tearsheet", response_class=HTMLResponse, include_in_schema=False)
    def ui_tearsheet(
        request: Request,
        profile: str = Query(default="default_user"),
        lookback_days: int = Query(default=252, ge=63, le=756),
    ):
        normalized_profile = _normalize_profile(profile)
        html = build_tearsheet_html(normalized_profile, lookback_days=lookback_days)
        if not html:
            return HTMLResponse(
                status_code=404,
                content=(
                    "<html><body><h2>Tearsheet unavailable</h2>"
                    "<p>Configure benchmark + weighted holdings and ensure sufficient price history.</p>"
                    "</body></html>"
                ),
            )
        return HTMLResponse(content=html)

    @app.get("/ui/portfolio", response_class=HTMLResponse, include_in_schema=False)
    def ui_portfolio_home(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="portfolio_home",
        )

    @app.get("/ui/portfolio/easy-setup", response_class=HTMLResponse, include_in_schema=False)
    def ui_portfolio_easy_setup(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        easy_setup = _easy_setup_context(
            step=1,
            state_data=_easy_setup_state_from_inputs(default_inputs()),
            profile_state=state,
            preview=None,
            errors=[],
        )
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="portfolio_easy_setup",
            state=state,
            extra_context={"easy_setup": easy_setup},
        )

    @app.get("/ui/portfolio/{view}", response_class=HTMLResponse, include_in_schema=False)
    def ui_portfolio_view(
        request: Request,
        view: str,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        view_to_page = {
            "easy-setup": "portfolio_easy_setup",
            "builder": "portfolio_builder_holdings",
            "holdings": "portfolio_builder_holdings",
            "policy": "portfolio_builder_policy",
            "allocation": "portfolio_builder_allocation",
            "benchmark": "portfolio_builder_benchmark",
            "diagnostics": "portfolio_diagnostics",
            "risk": "portfolio_risk",
            "cma": "portfolio_cma",
            "scenarios": "portfolio_scenarios",
            "implementation": "portfolio_implementation",
            "rebalancing": "portfolio_implementation",
            "attribution": "portfolio_attribution",
            "simulation": "portfolio_simulation",
            "history": "portfolio_history",
            "overview": "portfolio_home",
        }
        page_key = view_to_page.get((view or "").strip().lower())
        if not page_key:
            return RedirectResponse(url=f"/ui/portfolio?profile={normalized_profile}", status_code=307)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/easy-setup/step1",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_easy_setup_step1(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        state = build_profile_state(_settings(request), normalized_profile)
        state_data = _easy_setup_state_from_form(form, previous={})
        errors: list[str] = []
        has_file = str(form.get("has_holdings_file", "")).strip().lower() == "yes"
        upload = form.get("holdings_file")
        if has_file and isinstance(upload, UploadFile) and upload.filename:
            try:
                content = await upload.read()
                import_holdings_from_upload(
                    profile_name=normalized_profile,
                    filename=upload.filename or "holdings.yaml",
                    content=content,
                )
                state = build_profile_state(_settings(request), normalized_profile)
            except ValueError as exc:
                errors.append(f"Holdings import failed: {exc}")

        easy_setup = _easy_setup_context(
            step=2,
            state_data=state_data,
            profile_state=state,
            preview=None,
            errors=errors,
        )
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message="Step 1 saved. Continue to portfolio shape.",
            message_kind="success" if not errors else "error",
            state=state,
            page_key="portfolio_easy_setup",
            extra_context={"easy_setup": easy_setup},
            status_code=200 if not errors else 400,
        )

    @app.post(
        "/ui/profile/{profile}/easy-setup/step2",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_easy_setup_step2(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        state = build_profile_state(_settings(request), normalized_profile)
        previous = _easy_setup_state_from_json(str(form.get("easy_state", "")))
        state_data = _easy_setup_state_from_form(form, previous=previous)
        parsed_inputs = _easy_setup_inputs_from_state(state_data)
        plan = build_plan(parsed_inputs, holdings=state.get("holdings", []))
        easy_setup = _easy_setup_context(
            step=3,
            state_data=state_data,
            profile_state=state,
            preview=plan.get("summary"),
            errors=[],
        )
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message="Step 2 saved. Review and apply defaults.",
            message_kind="success",
            state=state,
            page_key="portfolio_easy_setup",
            extra_context={"easy_setup": easy_setup},
        )

    @app.post(
        "/ui/profile/{profile}/easy-setup/apply",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_easy_setup_apply(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        previous = _easy_setup_state_from_json(str(form.get("easy_state", "")))
        state_data = _easy_setup_state_from_form(form, previous=previous)
        try:
            parsed_inputs = _easy_setup_inputs_from_state(state_data)
            plan = build_plan(parsed_inputs, holdings=build_profile_state(_settings(request), normalized_profile).get("holdings", []))
            apply_plan(normalized_profile, plan)
            return RedirectResponse(url=f"/ui/portfolio?profile={normalized_profile}", status_code=303)
        except ValueError as exc:
            state = build_profile_state(_settings(request), normalized_profile)
            easy_setup = _easy_setup_context(
                step=3,
                state_data=state_data,
                profile_state=state,
                preview=None,
                errors=[f"Easy setup apply failed: {exc}"],
            )
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Easy setup apply failed: {exc}",
                message_kind="error",
                status_code=400,
                state=state,
                page_key="portfolio_easy_setup",
                extra_context={"easy_setup": easy_setup},
            )

    @app.get("/ui/audit", response_class=HTMLResponse, include_in_schema=False)
    def ui_audit(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key="audit_home",
        )

    @app.get("/ui/audit/{view}", response_class=HTMLResponse, include_in_schema=False)
    def ui_audit_view(
        request: Request,
        view: str,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        view_to_page = {
            "history": "audit_history",
            "overrides": "audit_overrides",
            "logs": "audit_logs",
            "overview": "audit_home",
        }
        page_key = view_to_page.get((view or "").strip().lower())
        if not page_key:
            return RedirectResponse(url=f"/ui/audit?profile={normalized_profile}", status_code=307)
        return _render_settings_page(
            request,
            profile=normalized_profile,
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/save/coverage",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_coverage(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        updates = _coverage_updates_from_form(form)
        page_key = _page_key_from_form(form, default="briefing_watchlists")
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Coverage preferences saved.",
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/save/delivery",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_delivery(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        updates = _delivery_updates_from_form(form)
        page_key = _page_key_from_form(form, default="briefing_delivery")
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Delivery preferences saved.",
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/save/sections",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_sections(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        updates = _section_updates_from_form(form)
        page_key = _page_key_from_form(form, default="briefing_morning")
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Morning section visibility saved.",
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/save/verticals",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_verticals(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        updates = _vertical_updates_from_form(form)
        page_key = _page_key_from_form(form, default="briefing_verticals")
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Vertical intelligence preferences saved.",
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/save/policy",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_policy(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_builder_policy")
        try:
            save_policy(normalized_profile, _policy_updates_from_form(form))
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Investor policy saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Policy save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/save/allocation",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_allocation(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_builder_allocation")
        try:
            save_allocation(normalized_profile, _allocation_updates_from_form(form))
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Strategic allocation targets saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Allocation save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/save/benchmark",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_benchmark(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_builder_benchmark")
        try:
            save_benchmark(normalized_profile, _benchmark_updates_from_form(form))
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Benchmark configuration saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Benchmark save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/holdings/save",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_holdings(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_builder_holdings")
        try:
            result = save_holdings_from_form(normalized_profile, form)
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            message = (
                f"Saved {result['count']} holding(s) for profile "
                f"'{result['profile_name']}' at {saved_at}."
            )
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=message,
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except (ValueError, Exception) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Holdings save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/holdings/import",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_import_holdings(
        request: Request,
        profile: str,
        file: UploadFile = File(...),
        ui_page: str = Form(default="portfolio_builder_holdings"),
    ):
        normalized_profile = _normalize_profile(profile)
        page_key = _normalize_page_key(ui_page, default="portfolio_builder_holdings")
        try:
            content = await file.read()
            result = import_holdings_from_upload(
                profile_name=normalized_profile,
                filename=file.filename or "holdings.yaml",
                content=content,
            )
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            message = (
                f"Imported {result['imported_count']} holdings for profile "
                f"'{result['profile_name']}' at {saved_at}."
            )
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=message,
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Holdings import failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/holdings/load-preset",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_load_holdings_preset(request: Request, profile: str):
        from app.validation.runner import apply_preset

        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        preset_name = str(form.get("preset_name", "")).strip()
        page_key = _page_key_from_form(form, default="portfolio_builder_holdings")
        if not preset_name:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="Select a preset before loading.",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )
        try:
            result = apply_preset(
                profile_name=normalized_profile,
                preset_name=preset_name,
                include_risk_refresh=False,
            )
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            message = (
                f"Loaded preset '{result['preset']}' into profile '{result['profile']}' "
                f"({result['holdings_imported']} holdings) at {saved_at}."
            )
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=message,
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Preset load failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/simulation/run",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_run_simulation(request: Request, profile: str):
        from app.simulation.service import parse_simulation_config, run_simulation

        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_simulation")
        try:
            state = build_profile_state(_settings(request), normalized_profile)
            payload = _simulation_payload_from_form(form)
            config = parse_simulation_config(
                payload=payload,
                fallback_holdings=state.get("holdings", []),
                fallback_benchmark_symbol=str(state.get("benchmark", {}).get("base_symbol") or "ACWI"),
            )
            result = run_simulation(
                profile_name=normalized_profile,
                settings=_settings(request),
                config=config,
                persist=True,
            )
            state = build_profile_state(_settings(request), normalized_profile)
            state.setdefault("metadata", {}).setdefault("simulation", {})["latest_run"] = result
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="Simulation run completed.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except Exception as exc:
            logger.error("simulation run failed: %s", exc)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Simulation run failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/simulation/preset/save",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_simulation_preset(request: Request, profile: str):
        from app.simulation.service import save_preset_from_config

        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_simulation")
        try:
            payload = _simulation_payload_from_form(form)
            preset_name = str(form.get("simulation_preset_name", "")).strip()
            description = str(form.get("simulation_preset_description", "")).strip()
            save_preset_from_config(
                profile_name=normalized_profile,
                preset_name=preset_name,
                description=description,
                config_payload=payload,
            )
            state = build_profile_state(_settings(request), normalized_profile)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Saved simulation preset '{preset_name}'.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except Exception as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Saving simulation preset failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/simulation/preset/load",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_load_simulation_preset(request: Request, profile: str):
        from app.simulation.service import load_preset_config

        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_simulation")
        preset_name = str(form.get("simulation_preset_load_name", "")).strip()
        preset = load_preset_config(normalized_profile, preset_name)
        if not preset:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Preset '{preset_name}' not found.",
                message_kind="error",
                status_code=404,
                page_key=page_key,
            )
        state = build_profile_state(_settings(request), normalized_profile)
        state.setdefault("metadata", {}).setdefault("simulation", {})["defaults"] = preset
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message=f"Loaded simulation preset '{preset_name}'.",
            message_kind="success",
            state=state,
            page_key=page_key,
        )

    @app.post(
        "/ui/profile/{profile}/reset",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_reset_profile_preferences(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="audit_home")
        result = reset_preferences(normalized_profile)
        state = build_profile_state(_settings(request), normalized_profile)
        timezone = state["effective"]["timezone"]
        saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message=f"Reset complete. Removed {result['removed_count']} override(s) at {saved_at}.",
            message_kind="success",
            state=state,
            page_key=page_key,
        )

    @app.get("/api/v1/profile/{profile}/state")
    def api_profile_state(profile: str):
        return build_profile_state(_settings_from_app(app), _normalize_profile(profile))

    @app.put("/api/v1/profile/{profile}/preferences")
    def api_update_preferences(profile: str, payload: PreferenceUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        if not payload.updates:
            raise HTTPException(status_code=400, detail={"error": "No updates provided"})
        try:
            return apply_preference_updates(normalized_profile, payload.updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.delete("/api/v1/profile/{profile}/preferences/{pref_key}")
    def api_delete_preference(profile: str, pref_key: str):
        normalized_profile = _normalize_profile(profile)
        try:
            return remove_preference(normalized_profile, pref_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.post("/api/v1/profile/{profile}/holdings/import")
    async def api_import_holdings(profile: str, file: UploadFile = File(...)):
        normalized_profile = _normalize_profile(profile)
        try:
            content = await file.read()
            return import_holdings_from_upload(
                profile_name=normalized_profile,
                filename=file.filename or "holdings.yaml",
                content=content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.put("/api/v1/profile/{profile}/policy")
    def api_update_policy(profile: str, payload: PolicyUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        try:
            return save_policy(normalized_profile, payload.payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.put("/api/v1/profile/{profile}/allocation")
    def api_update_allocation(profile: str, payload: AllocationUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        try:
            return {"rows": save_allocation(normalized_profile, payload.rows)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.put("/api/v1/profile/{profile}/benchmark")
    def api_update_benchmark(profile: str, payload: BenchmarkUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        try:
            return save_benchmark(normalized_profile, payload.payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.get("/api/v1/followables/search")
    def api_followables_search(
        q: str = Query(default=""),
        kind: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return {
            "results": search_followables(
                settings=_settings_from_app(app),
                q=q,
                kind=kind,
                limit=limit,
            )
        }

    @app.post(
        "/ui/profile/{profile}/save/risk-config",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_risk_config(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_risk")
        try:
            raw_lookback = str(form.get("risk_lookback_days", "252")).strip()
            raw_rfr = str(form.get("risk_free_rate_pct", "4.5")).strip()
            lookback_days = int(raw_lookback) if raw_lookback else 252
            risk_free_rate_pct = float(raw_rfr) if raw_rfr else 4.5
            save_risk_config(normalized_profile, lookback_days, risk_free_rate_pct)
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Risk configuration saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except (ValueError, TypeError) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Risk config save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/refresh/risk",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_refresh_risk(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_risk")
        refresh_risk_for_profile(normalized_profile)
        state = build_profile_state(_settings(request), normalized_profile)
        timezone = state["effective"]["timezone"]
        refreshed_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message=f"Risk metrics refreshed at {refreshed_at}.",
            message_kind="success",
            state=state,
            page_key=page_key,
        )

    @app.get("/api/v1/profile/{profile}/risk")
    def api_get_risk(
        profile: str,
        lookback_days: int = Query(default=252),
    ):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        risk_analytics = state.get("analysis", {}).get("risk_analytics", {})
        return {"profile": normalized_profile, "risk_analytics": risk_analytics}

    @app.post("/api/v1/profile/{profile}/risk/refresh")
    def api_refresh_risk(profile: str):
        normalized_profile = _normalize_profile(profile)
        refresh_risk_for_profile(normalized_profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        risk_analytics = state.get("analysis", {}).get("risk_analytics", {})
        return {"profile": normalized_profile, "refreshed": True, "risk_analytics": risk_analytics}

    @app.post(
        "/ui/profile/{profile}/save/cma",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_cma(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_cma")
        try:
            save_cma(normalized_profile, _cma_entries_from_form(form))
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"CMA assumptions saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except (ValueError, TypeError) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"CMA save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.post(
        "/ui/profile/{profile}/save/cma-correlations",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_cma_correlations(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_cma")
        try:
            save_cma_corr(normalized_profile, _cma_correlations_from_form(form))
            state = build_profile_state(_settings(request), normalized_profile)
            timezone = state["effective"]["timezone"]
            saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"CMA correlations saved. Saved at {saved_at}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except (ValueError, TypeError) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"CMA correlations save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    @app.get("/api/v1/profile/{profile}/cma")
    def api_get_cma(profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        cma_analytics = state.get("analysis", {}).get("cma_analytics", {})
        return {
            "profile": normalized_profile,
            "cma_analytics": cma_analytics,
            "cma_entries": state.get("cma_entries", []),
            "cma_correlations": state.get("cma_correlations", []),
        }

    @app.put("/api/v1/profile/{profile}/cma")
    def api_update_cma(profile: str, payload: CMAUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        try:
            return {"rows": save_cma(normalized_profile, payload.rows)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.put("/api/v1/profile/{profile}/cma/correlations")
    def api_update_cma_correlations(profile: str, payload: CMACorrelationsUpdateRequest):
        normalized_profile = _normalize_profile(profile)
        try:
            return {"rows": save_cma_corr(normalized_profile, payload.rows)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.post("/ui/profile/{profile}/save/rebalancing-config")
    async def ui_save_rebalancing_config(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        try:
            form = await request.form()
            page_key = _page_key_from_form(form, default="portfolio_implementation")
            payload = _rebalancing_config_from_form(form)
            save_rebalancing_config_for_profile(normalized_profile, payload)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="Rebalancing configuration saved.",
                message_kind="success",
                page_key=page_key,
            )
        except Exception as exc:
            logger.error("save rebalancing config error: %s", exc)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Error saving rebalancing config: {exc}",
                message_kind="error",
                page_key="portfolio_implementation",
            )

    @app.post("/ui/profile/{profile}/rebalance/generate")
    async def ui_generate_rebalance(request: Request, profile: str):
        from app.rebalancing.service import compute_rebalance_proposal, load_rebalancing_config
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_implementation")
        try:
            state = build_profile_state(_settings(request), normalized_profile)
            config = state.get("rebalancing_config") or load_rebalancing_config(normalized_profile)
            drift_rows = state.get("analysis", {}).get("allocation_drift", {}).get("rows", [])
            raw_holdings = state.get("holdings") or []
            holdings = [{"symbol": h["symbol"], "weight_pct": h.get("weight_pct")} for h in (raw_holdings if isinstance(raw_holdings, list) else raw_holdings.get("rows", []))]
            compute_rebalance_proposal(normalized_profile, drift_rows, holdings, config, trigger_type="manual", persist=True)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="Rebalance proposal generated.",
                message_kind="success",
                page_key=page_key,
            )
        except Exception as exc:
            logger.error("generate rebalance error: %s", exc)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Error generating proposal: {exc}",
                message_kind="error",
                page_key=page_key,
            )

    @app.get("/api/v1/profile/{profile}/rebalancing")
    def api_get_rebalancing(profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        return state.get("analysis", {}).get("rebalance_proposal", {})

    @app.post("/api/v1/profile/{profile}/rebalancing/generate")
    def api_generate_rebalance(profile: str):
        from app.rebalancing.service import compute_rebalance_proposal, load_rebalancing_config
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        config = state.get("rebalancing_config") or load_rebalancing_config(normalized_profile)
        drift_rows = state.get("analysis", {}).get("allocation_drift", {}).get("rows", [])
        raw_holdings = state.get("holdings") or []
        holdings = [{"symbol": h["symbol"], "weight_pct": h.get("weight_pct")} for h in (raw_holdings if isinstance(raw_holdings, list) else raw_holdings.get("rows", []))]
        proposal = compute_rebalance_proposal(normalized_profile, drift_rows, holdings, config, trigger_type="manual", persist=True)
        return proposal

    @app.get("/api/v1/profile/{profile}/rebalancing/history")
    def api_rebalancing_history(profile: str, limit: int = Query(default=20, le=100)):
        from app.rebalancing.service import load_proposal_history
        normalized_profile = _normalize_profile(profile)
        return {"history": load_proposal_history(normalized_profile, limit=limit)}

    @app.post(
        "/ui/profile/{profile}/attribution/generate",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_generate_attribution(request: Request, profile: str):
        from app.attribution.service import compute_attribution
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_attribution")
        try:
            state = build_profile_state(_settings(request), normalized_profile)
            cma_analytics = state.get("analysis", {}).get("cma_analytics", {})
            actual_allocation = state.get("allocation", {}).get("actual", [])
            raw_targets = load_allocation_targets(normalized_profile)
            allocation_targets = merge_targets_with_catalog(raw_targets) if raw_targets else default_allocation_targets()
            policy = state.get("policy")
            compute_attribution(
                profile_name=normalized_profile,
                actual_allocation=actual_allocation,
                target_allocation=allocation_targets,
                cma_analytics=cma_analytics,
                policy=policy,
                persist=True,
            )
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="Attribution analysis generated and saved.",
                message_kind="success",
                page_key=page_key,
            )
        except Exception as exc:
            logger.error("generate attribution error: %s", exc)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Error generating attribution: {exc}",
                message_kind="error",
                page_key=page_key,
            )

    @app.get("/api/v1/profile/{profile}/attribution")
    def api_get_attribution(profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        return state.get("analysis", {}).get("attribution", {})

    @app.post("/api/v1/profile/{profile}/attribution/generate")
    def api_generate_attribution(profile: str):
        from app.attribution.service import compute_attribution
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        cma_analytics = state.get("analysis", {}).get("cma_analytics", {})
        actual_allocation = state.get("allocation", {}).get("actual", [])
        raw_targets = load_allocation_targets(normalized_profile)
        allocation_targets = merge_targets_with_catalog(raw_targets) if raw_targets else default_allocation_targets()
        policy = state.get("policy")
        result = compute_attribution(
            profile_name=normalized_profile,
            actual_allocation=actual_allocation,
            target_allocation=allocation_targets,
            cma_analytics=cma_analytics,
            policy=policy,
            persist=True,
        )
        return result

    @app.get("/api/v1/profile/{profile}/attribution/history")
    def api_attribution_history(profile: str, limit: int = Query(default=20, le=100)):
        from app.attribution.service import load_attribution_history
        normalized_profile = _normalize_profile(profile)
        return {"history": load_attribution_history(normalized_profile, limit=limit)}

    # ── Phase 7A: Fixed Income Analytics ─────────────────────────────────────

    @app.get("/ui/portfolio/{profile}/bonds", response_class=HTMLResponse, include_in_schema=False)
    def ui_bonds(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message="",
            message_kind="",
            state=state,
            page_key="portfolio_bonds",
        )

    @app.get("/api/v1/profile/{profile}/bonds")
    def api_get_bonds(profile: str):
        from app.bonds.service import compute_bond_analytics, load_bond_overrides
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        holdings = state.get("holdings", [])
        from app.schemas.portfolio import PortfolioHolding as _PH
        holding_objs = [
            _PH(symbol=h["symbol"], weight_pct=h.get("weight_pct"), bucket=h.get("bucket"))
            for h in holdings
            if isinstance(h, dict)
        ] if holdings and isinstance(holdings[0], dict) else state.get("profile_holdings", [])
        analytics = compute_bond_analytics(normalized_profile, holdings=state.get("_raw_holdings", holding_objs))
        overrides = load_bond_overrides(normalized_profile)
        return {"profile": normalized_profile, "bonds_analytics": analytics, "overrides": overrides}

    @app.post("/api/v1/profile/{profile}/bonds/refresh")
    def api_refresh_bonds(profile: str):
        from app.bonds.service import compute_bond_analytics
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        analytics = state.get("analysis", {}).get("bonds_analytics", {})
        return {"profile": normalized_profile, "refreshed": True, "bonds_analytics": analytics}

    @app.put("/api/v1/profile/{profile}/bonds/overrides/{symbol}")
    def api_save_bond_override(profile: str, symbol: str, payload: dict):
        from app.bonds.service import save_bond_override
        normalized_profile = _normalize_profile(profile)
        result = save_bond_override(normalized_profile, symbol, payload)
        return {"saved": True, "override": result}

    @app.delete("/api/v1/profile/{profile}/bonds/overrides/{symbol}")
    def api_delete_bond_override(profile: str, symbol: str):
        from app.bonds.service import delete_bond_override
        normalized_profile = _normalize_profile(profile)
        delete_bond_override(normalized_profile, symbol)
        return {"deleted": True, "symbol": symbol.upper()}

    @app.get("/api/v1/profile/{profile}/bonds/history")
    def api_bonds_history(profile: str, limit: int = Query(default=10, le=50)):
        from app.bonds.service import load_bond_snapshot_history
        normalized_profile = _normalize_profile(profile)
        return {"history": load_bond_snapshot_history(normalized_profile, limit=limit)}

    @app.post(
        "/ui/profile/{profile}/save/bond-override",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_bond_override(request: Request, profile: str):
        from app.bonds.service import save_bond_override, delete_bond_override
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_bonds")
        try:
            symbol = str(form.get("bond_symbol", "")).strip().upper()
            if not symbol:
                raise ValueError("Symbol is required")
            action = str(form.get("action", "save")).strip()
            if action == "delete":
                delete_bond_override(normalized_profile, symbol)
                msg = f"Override for {symbol} removed."
            else:
                payload = {
                    "modified_duration_yrs": float(form.get("modified_duration_yrs")) if form.get("modified_duration_yrs") else None,
                    "ytm_override_pct": float(form.get("ytm_override_pct")) if form.get("ytm_override_pct") else None,
                    "coupon_pct": float(form.get("coupon_pct")) if form.get("coupon_pct") else None,
                    "credit_quality": str(form.get("credit_quality", "")).strip() or None,
                }
                save_bond_override(normalized_profile, symbol, payload)
                msg = f"Override for {symbol} saved."
            state = build_profile_state(_settings(request), normalized_profile)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=msg,
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except (ValueError, TypeError) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Bond override save failed: {exc}",
                message_kind="error",
                status_code=400,
                page_key=page_key,
            )

    # ── Phase 7B: PDF Reports ─────────────────────────────────────────────────

    @app.get("/ui/portfolio/{profile}/reports", response_class=HTMLResponse, include_in_schema=False)
    def ui_reports(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        from app.reports.service import list_reports
        state["_reports_list"] = list_reports(normalized_profile, limit=10)
        return _render_settings_root(
            request,
            profile=normalized_profile,
            message="",
            message_kind="",
            state=state,
            page_key="portfolio_reports",
        )

    @app.post("/api/v1/profile/{profile}/reports/generate")
    def api_generate_report(profile: str, payload: dict = None):
        from app.reports.service import generate_report
        normalized_profile = _normalize_profile(profile)
        payload = payload or {}
        title = str(payload.get("title") or "Portfolio Report")
        sections = payload.get("sections") or None
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        result = generate_report(
            profile_name=normalized_profile,
            state=state,
            sections=sections,
            title=title,
        )
        return result

    @app.get("/api/v1/profile/{profile}/reports")
    def api_list_reports(profile: str, limit: int = Query(default=10, le=50)):
        from app.reports.service import list_reports
        normalized_profile = _normalize_profile(profile)
        return {"reports": list_reports(normalized_profile, limit=limit)}

    @app.get("/api/v1/profile/{profile}/reports/{report_id}/download")
    def api_download_report(profile: str, report_id: int):
        from app.reports.service import get_report_filepath
        from fastapi.responses import FileResponse
        normalized_profile = _normalize_profile(profile)
        filepath = get_report_filepath(report_id)
        if not filepath:
            raise HTTPException(status_code=404, detail="Report not found or file missing")
        return FileResponse(
            path=str(filepath),
            media_type="application/pdf",
            filename=filepath.name,
        )

    @app.delete("/api/v1/profile/{profile}/reports/{report_id}")
    def api_delete_report(profile: str, report_id: int):
        from app.reports.service import delete_report
        normalized_profile = _normalize_profile(profile)
        deleted = delete_report(report_id, normalized_profile)
        return {"deleted": deleted, "report_id": report_id}

    @app.post(
        "/ui/profile/{profile}/generate/report",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_generate_report(request: Request, profile: str):
        from app.reports.service import generate_report, list_reports
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_reports")
        try:
            title = str(form.get("report_title") or "Portfolio Report").strip()
            raw_sections = form.getlist("report_sections")
            sections = list(raw_sections) if raw_sections else None
            state = build_profile_state(_settings(request), normalized_profile)
            result = generate_report(
                profile_name=normalized_profile,
                state=state,
                sections=sections,
                title=title,
            )
            if result.get("available"):
                msg = f"Report '{title}' generated ({result.get('file_size_bytes', 0) // 1024} KB). Download via the API."
                kind = "success"
            else:
                msg = result.get("error", "Report generation failed.")
                kind = "error"
            state["_reports_list"] = list_reports(normalized_profile, limit=10)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=msg,
                message_kind=kind,
                state=state,
                page_key=page_key,
            )
        except Exception as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Report generation failed: {exc}",
                message_kind="error",
                status_code=500,
                page_key=page_key,
            )

    # ── Phase 7C: ESG / SRI ───────────────────────────────────────────────────

    @app.get(
        "/ui/portfolio/{profile}/esg",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_portfolio_esg(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        return _render_settings_root(
            request,
            profile=normalized_profile,
            state=state,
            page_key="portfolio_esg",
        )

    @app.get("/api/v1/profile/{profile}/esg")
    def api_get_esg(profile: str):
        from app.esg.service import compute_portfolio_esg, load_esg_config
        from app.portfolio.service import load_active_holdings
        normalized_profile = _normalize_profile(profile)
        holdings = load_active_holdings(normalized_profile)
        result = compute_portfolio_esg(normalized_profile, holdings, persist=False)
        return {
            "profile": normalized_profile,
            "esg": result,
            "esg_config": load_esg_config(normalized_profile),
        }

    @app.post("/api/v1/profile/{profile}/esg/refresh")
    def api_refresh_esg(profile: str):
        from app.esg.service import compute_portfolio_esg
        from app.portfolio.service import load_active_holdings
        normalized_profile = _normalize_profile(profile)
        holdings = load_active_holdings(normalized_profile)
        result = compute_portfolio_esg(
            normalized_profile, holdings, persist=True, force_refresh=True
        )
        return {"profile": normalized_profile, "esg": result}

    @app.put("/api/v1/profile/{profile}/esg/config")
    async def api_save_esg_config(request: Request, profile: str):
        from app.esg.service import save_esg_config
        normalized_profile = _normalize_profile(profile)
        payload = await request.json()
        result = save_esg_config(normalized_profile, payload)
        return result

    @app.post(
        "/ui/profile/{profile}/save/esg-config",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_esg_config(request: Request, profile: str):
        from app.esg.service import save_esg_config
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_esg")
        try:
            active_screens = list(form.getlist("active_screens"))
            save_esg_config(normalized_profile, {"active_screens": active_screens})
            state = build_profile_state(_settings(request), normalized_profile)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message="ESG screening preferences saved.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except Exception as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Failed to save ESG config: {exc}",
                message_kind="error",
                status_code=500,
                page_key=page_key,
            )

    # ── Phase 7D: Multi-Currency / FX ────────────────────────────────────────

    @app.get(
        "/ui/portfolio/{profile}/fx",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_portfolio_fx(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        return _render_settings_root(
            request,
            profile=normalized_profile,
            state=state,
            page_key="portfolio_fx",
        )

    @app.get("/api/v1/profile/{profile}/fx")
    def api_get_fx(profile: str):
        from app.fx.service import compute_fx_exposure, load_fx_config
        from app.portfolio.service import load_active_holdings
        normalized_profile = _normalize_profile(profile)
        holdings = load_active_holdings(normalized_profile)
        result = compute_fx_exposure(normalized_profile, holdings)
        return {
            "profile": normalized_profile,
            "fx": result,
            "fx_config": load_fx_config(normalized_profile),
        }

    @app.post("/api/v1/profile/{profile}/fx/refresh")
    def api_refresh_fx(profile: str):
        from app.fx.service import compute_fx_exposure
        from app.portfolio.service import load_active_holdings
        normalized_profile = _normalize_profile(profile)
        holdings = load_active_holdings(normalized_profile)
        result = compute_fx_exposure(normalized_profile, holdings, force_refresh=True)
        return {"profile": normalized_profile, "fx": result}

    @app.put("/api/v1/profile/{profile}/fx/config")
    async def api_save_fx_config(request: Request, profile: str):
        from app.fx.service import save_fx_config
        normalized_profile = _normalize_profile(profile)
        payload = await request.json()
        return save_fx_config(normalized_profile, payload)

    @app.get("/api/v1/profile/{profile}/fx/rates")
    def api_get_fx_rates(profile: str):
        from app.db.models import FXRate
        normalized_profile = _normalize_profile(profile)
        with __import__("app.db.session", fromlist=["get_session"]).get_session() as session:
            rows = (
                session.query(FXRate)
                .order_by(FXRate.as_of_date.desc())
                .limit(50)
                .all()
            )
        return {
            "rates": [
                {
                    "from": r.from_currency,
                    "to": r.to_currency,
                    "rate": r.rate,
                    "date": str(r.as_of_date),
                }
                for r in rows
            ]
        }

    @app.post(
        "/ui/profile/{profile}/save/fx-config",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_fx_config(request: Request, profile: str):
        from app.fx.service import save_fx_config
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
        page_key = _page_key_from_form(form, default="portfolio_fx")
        try:
            home_currency = str(form.get("home_currency") or "USD").strip().upper()
            hedge_policy = str(form.get("hedge_policy") or "unhedged").strip()
            save_fx_config(normalized_profile, {
                "home_currency": home_currency,
                "hedge_policy": hedge_policy,
            })
            state = build_profile_state(_settings(request), normalized_profile)
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"FX config saved. Home currency: {home_currency}.",
                message_kind="success",
                state=state,
                page_key=page_key,
            )
        except Exception as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Failed to save FX config: {exc}",
                message_kind="error",
                status_code=500,
                page_key=page_key,
            )

    @app.get("/api/v1/profile/{profile}/briefing/morning/charts")
    def api_morning_charts(profile: str):
        normalized_profile = _normalize_profile(profile)
        return _build_morning_chart_preview(
            settings=_settings_from_app(app),
            profile=normalized_profile,
        )

    @app.get("/api/v1/profile/{profile}/briefing/charts/watchlist")
    def api_watchlist_charts(
        profile: str,
        period: str = Query(default="1M"),
        mode: str = Query(default="rebased"),
        benchmark: str = Query(default="none"),
        symbols: str = Query(default=""),
        include_events: bool = Query(default=False),
    ):
        normalized_profile = _normalize_profile(profile)
        settings = _settings_from_app(app)
        user_profile = _load_profile_defaults(settings, normalized_profile)
        market_svc = MarketDataService(settings)
        symbol_list = [token.strip().upper() for token in symbols.split(",") if token.strip()]
        return build_watchlist_chart_spec(
            profile=user_profile,
            period=period,
            mode=mode,
            benchmark=benchmark,
            symbols=symbol_list or None,
            include_events=include_events,
            market_data_service=market_svc,
        )

    @app.get("/api/v1/profile/{profile}/briefing/macro")
    def api_macro_dashboard(profile: str):
        normalized_profile = _normalize_profile(profile)
        settings = _settings_from_app(app)
        user_profile = _load_profile_defaults(settings, normalized_profile)
        return build_macro_policy_dashboard(
            profile=user_profile,
            settings=settings,
        )

    @app.post("/api/v1/profile/{profile}/simulation/run")
    def api_run_simulation(profile: str, payload: SimulationRunRequest):
        from app.simulation.service import parse_simulation_config, run_simulation

        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings_from_app(app), normalized_profile)
        config = parse_simulation_config(
            payload=payload.payload,
            fallback_holdings=state.get("holdings", []),
            fallback_benchmark_symbol=str(state.get("benchmark", {}).get("base_symbol") or "ACWI"),
        )
        return run_simulation(
            profile_name=normalized_profile,
            settings=_settings_from_app(app),
            config=config,
            persist=True,
        )

    @app.get("/api/v1/profile/{profile}/simulation/runs")
    def api_simulation_runs(profile: str, limit: int = Query(default=20, ge=1, le=200)):
        from app.simulation.repository import list_simulation_runs

        normalized_profile = _normalize_profile(profile)
        return {"runs": list_simulation_runs(normalized_profile, limit=limit)}

    @app.get("/api/v1/profile/{profile}/simulation/runs/{run_id}")
    def api_simulation_run(profile: str, run_id: int):
        from app.simulation.service import fetch_run

        normalized_profile = _normalize_profile(profile)
        run = fetch_run(normalized_profile, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail={"error": "Simulation run not found"})
        return run

    @app.get("/api/v1/profile/{profile}/simulation/presets")
    def api_simulation_presets(profile: str):
        from app.simulation.repository import list_simulation_presets

        normalized_profile = _normalize_profile(profile)
        return {"presets": list_simulation_presets(normalized_profile)}

    @app.post("/api/v1/profile/{profile}/simulation/presets")
    def api_save_simulation_preset(profile: str, payload: SimulationRunRequest):
        from app.simulation.service import save_preset_from_config

        normalized_profile = _normalize_profile(profile)
        body = payload.payload or {}
        name = str(body.get("preset_name") or "").strip()
        description = str(body.get("description") or "").strip()
        config = body.get("config")
        if not name or not isinstance(config, dict):
            raise HTTPException(status_code=400, detail={"error": "preset_name and config are required"})
        return save_preset_from_config(
            profile_name=normalized_profile,
            preset_name=name,
            description=description,
            config_payload=config,
        )

    # -------------------------------------------------------------------------
    # Feedback endpoints
    # -------------------------------------------------------------------------

    @app.post("/api/v1/feedback")
    def api_submit_feedback(request: Request, body: dict = None):
        """Record user feedback on a chart or signal.

        Body: {profile, chart_key, label, source?, notes?, regime_tags?}
        label must be one of: useful | not_relevant | wrong_data
        """
        from datetime import date as _date
        from app.db.models import UserFeedback
        from app.db.session import get_session

        if body is None:
            body = {}
        profile = str(body.get("profile") or "default_user").strip()
        chart_key = str(body.get("chart_key") or "").strip()
        label = str(body.get("label") or "").strip().lower()
        source = str(body.get("source") or "web").strip()
        notes = str(body.get("notes") or "").strip() or None
        regime_tags = list(body.get("regime_tags") or [])

        valid_labels = {"useful", "not_relevant", "wrong_data"}
        if not chart_key:
            raise HTTPException(status_code=400, detail={"error": "chart_key is required"})
        if label not in valid_labels:
            raise HTTPException(status_code=400, detail={"error": f"label must be one of {sorted(valid_labels)}"})

        with get_session() as session:
            fb = UserFeedback(
                profile_name=profile,
                chart_key=chart_key,
                label=label,
                source=source,
                briefing_date=_date.today(),
                notes=notes,
                regime_tags=regime_tags,
            )
            session.add(fb)
            session.commit()

        return {"status": "recorded", "chart_key": chart_key, "label": label}

    @app.get("/api/v1/feedback/weights")
    def api_feedback_weights(profile: str = "default_user", days: int = 30):
        """Return per-chart feedback weight multipliers for the last N days.

        Weight: >1.0 means chart is well-liked; <1.0 means downvote pressure.
        """
        return get_chart_feedback_weights(profile, days=days)

    @app.post("/api/v1/feedback/telegram")
    async def api_telegram_feedback_callback(request: Request):
        """Webhook endpoint for Telegram inline keyboard callback_query feedback."""
        from datetime import date as _date
        from app.db.models import UserFeedback
        from app.db.session import get_session

        data = await request.json()
        callback = data.get("callback_query") or {}
        callback_id = str(callback.get("id") or "")
        callback_data = str(callback.get("data") or "")

        # Format: "fb:<label>:<chart_key>"
        parts = callback_data.split(":")
        if len(parts) < 3 or parts[0] != "fb":
            return {"ok": True}

        label_raw = parts[1]
        chart_key = ":".join(parts[2:])
        label_map = {"useful": "useful", "not_relevant": "not_relevant", "wrong_data": "wrong_data"}
        label = label_map.get(label_raw)
        if not label:
            return {"ok": True}

        with get_session() as session:
            session.add(UserFeedback(
                profile_name="default_user",
                chart_key=chart_key,
                label=label,
                source="telegram",
                briefing_date=_date.today(),
            ))
            session.commit()

        # Acknowledge to dismiss Telegram spinner
        settings_obj = _settings(request)
        if settings_obj.telegram_bot_token and callback_id:
            from app.messaging.telegram import TelegramMessenger
            TelegramMessenger(settings_obj).answer_callback_query(callback_id, "Feedback recorded")

        return {"ok": True}

    return app


def get_chart_feedback_weights(profile: str, *, days: int = 30) -> dict[str, float]:
    """Compute per-chart priority multipliers from recent feedback.

    Returns {chart_key: multiplier} where:
      - 1.0  = neutral (no feedback or balanced)
      - >1.0 = net positive feedback (boost priority)
      - <1.0 = net negative feedback (suppress priority)
    Range: [0.50, 1.50]
    """
    from datetime import date, timedelta
    from sqlalchemy import func
    from app.db.models import UserFeedback
    from app.db.session import get_session

    cutoff = date.today() - timedelta(days=days)
    weights: dict[str, float] = {}

    try:
        with get_session() as session:
            rows = (
                session.query(
                    UserFeedback.chart_key,
                    UserFeedback.label,
                    func.count(UserFeedback.id).label("cnt"),
                )
                .filter(
                    UserFeedback.profile_name == profile,
                    UserFeedback.briefing_date >= cutoff,
                )
                .group_by(UserFeedback.chart_key, UserFeedback.label)
                .all()
            )

        # Aggregate per chart
        chart_counts: dict[str, dict[str, int]] = {}
        for chart_key, label, cnt in rows:
            chart_counts.setdefault(chart_key, {})
            chart_counts[chart_key][label] = int(cnt)

        for chart_key, counts in chart_counts.items():
            useful = counts.get("useful", 0)
            negative = counts.get("not_relevant", 0) + counts.get("wrong_data", 0)
            total = useful + negative
            if total == 0:
                weights[chart_key] = 1.0
                continue
            net_score = (useful - negative) / total  # range [-1, +1]
            # Map to [0.50, 1.50]
            weights[chart_key] = round(1.0 + net_score * 0.50, 4)
    except Exception:
        pass

    return weights


def _render_ui_after_update(
    request: Request,
    *,
    profile: str,
    updates: dict[str, Any],
    success_message: str,
    page_key: str,
) -> HTMLResponse:
    try:
        apply_preference_updates(profile, updates)
        state = build_profile_state(_settings(request), profile)
        timezone = state["effective"]["timezone"]
        saved_at = datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")
        message = f"{success_message} Saved at {saved_at}."
        return _render_settings_root(
            request,
            profile=profile,
            message=message,
            message_kind="success",
            state=state,
            page_key=page_key,
        )
    except ValueError as exc:
        return _render_settings_root(
            request,
            profile=profile,
            message=f"Save failed: {exc}",
            message_kind="error",
            status_code=400,
            page_key=page_key,
        )


def _render_settings_page(
    request: Request,
    *,
    profile: str,
    page_key: str,
    state: dict[str, Any] | None = None,
    extra_context: dict[str, Any] | None = None,
) -> HTMLResponse:
    context = _resolve_page_context(page_key)
    state = state or build_profile_state(_settings(request), profile)
    initial_section = context["visible_sections"][0] if context["visible_sections"] else ""
    payload = {
        "state": state,
        "message": "",
        "message_kind": "info",
        "page_key": page_key,
        "global_nav": context["global_nav"],
        "workspace": context["workspace"],
        "workspace_page": context["workspace_page"],
        "builder_tab": context.get("builder_tab", ""),
        "visible_sections": context["visible_sections"],
        "initial_section": initial_section,
    }
    if extra_context:
        payload.update(extra_context)
    return templates.TemplateResponse(
        request,
        "settings.html",
        payload,
    )


def _render_settings_root(
    request: Request,
    *,
    profile: str,
    message: str,
    message_kind: str,
    status_code: int = 200,
    state: dict[str, Any] | None = None,
    page_key: str = "briefing_home",
    extra_context: dict[str, Any] | None = None,
) -> HTMLResponse:
    context = _resolve_page_context(page_key)
    state = state or build_profile_state(_settings(request), profile)
    payload = {
        "state": state,
        "message": message,
        "message_kind": message_kind,
        "page_key": page_key,
        "workspace": context["workspace"],
        "workspace_page": context["workspace_page"],
        "builder_tab": context.get("builder_tab", ""),
        "visible_sections": context["visible_sections"],
    }
    if extra_context:
        payload.update(extra_context)
    return templates.TemplateResponse(
        request,
        "partials/settings_root.html",
        payload,
        status_code=status_code,
    )


def _resolve_page_context(page_key: str) -> dict[str, Any]:
    key = (page_key or "").strip().lower()
    context = _PAGE_CONTEXTS.get(key) or _PAGE_CONTEXTS["briefing_home"]
    return {
        "global_nav": context["global_nav"],
        "workspace": context["workspace"],
        "workspace_page": context["workspace_page"],
        "builder_tab": context.get("builder_tab", ""),
        "visible_sections": list(context["visible_sections"]),
    }


def _normalize_page_key(value: str | None, *, default: str) -> str:
    raw = (value or "").strip().lower()
    if raw in _PAGE_CONTEXTS:
        return raw
    return default


def _page_key_from_form(form: Any, *, default: str) -> str:
    return _normalize_page_key(str(form.get("ui_page", "")).strip(), default=default)


def _easy_setup_state_from_inputs(inputs: EasySetupInputs) -> dict[str, Any]:
    return {
        "investor_type": inputs.investor_type,
        "horizon_bucket": inputs.horizon_bucket,
        "risk_comfort": inputs.risk_comfort,
        "base_currency": inputs.base_currency,
        "home_region": inputs.home_region,
        "has_holdings_file": inputs.has_holdings_file,
        "infer_from_holdings": inputs.infer_from_holdings,
        "starting_mix": inputs.starting_mix,
        "loss_averse": inputs.loss_averse,
        "concentration_tolerant": inputs.concentration_tolerant,
        "auto_rebalancing": inputs.auto_rebalancing,
    }


def _easy_setup_state_from_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return _easy_setup_state_from_inputs(default_inputs())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _easy_setup_state_from_inputs(default_inputs())
    if not isinstance(parsed, dict):
        return _easy_setup_state_from_inputs(default_inputs())
    defaults = _easy_setup_state_from_inputs(default_inputs())
    defaults.update(parsed)
    return defaults


def _bool_from_form(form: Any, key: str, fallback: bool = False) -> bool:
    value = str(form.get(key, "")).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return fallback


def _easy_setup_state_from_form(form: Any, *, previous: dict[str, Any]) -> dict[str, Any]:
    state = dict(_easy_setup_state_from_inputs(default_inputs()))
    state.update(previous or {})

    mappings = {
        "investor_type": "investor_type",
        "horizon_bucket": "horizon_bucket",
        "risk_comfort": "risk_comfort",
        "base_currency": "base_currency",
        "home_region": "home_region",
        "starting_mix": "starting_mix",
    }
    for field, key in mappings.items():
        raw = str(form.get(field, "")).strip()
        if raw:
            state[key] = raw

    for field, key in (
        ("has_holdings_file", "has_holdings_file"),
        ("infer_from_holdings", "infer_from_holdings"),
        ("loss_averse", "loss_averse"),
        ("concentration_tolerant", "concentration_tolerant"),
        ("auto_rebalancing", "auto_rebalancing"),
    ):
        if field in form:
            state[key] = _bool_from_form(form, field, fallback=bool(state.get(key)))
    return state


def _easy_setup_inputs_from_state(state: dict[str, Any]) -> EasySetupInputs:
    return EasySetupInputs(
        investor_type=str(state.get("investor_type") or "long_term_individual"),
        horizon_bucket=str(state.get("horizon_bucket") or "7_15"),
        risk_comfort=str(state.get("risk_comfort") or "medium"),
        base_currency=str(state.get("base_currency") or "EUR").upper(),
        home_region=str(state.get("home_region") or "global"),
        has_holdings_file=bool(state.get("has_holdings_file")),
        infer_from_holdings=bool(state.get("infer_from_holdings")),
        starting_mix=str(state.get("starting_mix") or "balanced"),
        loss_averse=bool(state.get("loss_averse")),
        concentration_tolerant=bool(state.get("concentration_tolerant")),
        auto_rebalancing=bool(state.get("auto_rebalancing", True)),
    )


def _easy_setup_context(
    *,
    step: int,
    state_data: dict[str, Any],
    profile_state: dict[str, Any],
    preview: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "step": max(1, min(3, int(step))),
        "state": state_data,
        "state_json": json.dumps(state_data),
        "holdings_count": len(profile_state.get("holdings", [])),
        "preview": preview or {},
        "errors": errors,
    }


def _coverage_updates_from_form(form) -> dict[str, Any]:
    def _unique_upper(items: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in items:
            symbol = str(raw).strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            ordered.append(symbol)
        return ordered

    updates: dict[str, Any] = {
        "watchlist.primary": _unique_upper(form.getlist("watchlist_primary")),
        "watchlist.secondary": _unique_upper(form.getlist("watchlist_secondary")),
        "watchlist.monitor": _unique_upper(form.getlist("watchlist_monitor")),
        "coverage.home_region": str(form.get("coverage_home_region", "")).strip().lower(),
    }

    sector_weights: dict[str, float] = {}
    region_weights: dict[str, float] = {}
    for key, value in form.multi_items():
        text_key = str(key)
        raw = str(value).strip()
        if text_key.startswith("sector_weight__"):
            sector_key = text_key.replace("sector_weight__", "", 1)
            if raw:
                sector_weights[sector_key] = float(raw)
            continue
        if text_key.startswith("region_weight__"):
            region_key = text_key.replace("region_weight__", "", 1)
            if raw:
                region_weights[region_key] = float(raw)
            continue
    updates["sector.weights"] = sector_weights
    updates["coverage.weights"] = region_weights
    return updates


def _delivery_updates_from_form(form) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "delivery.morning_channels": [str(item).lower() for item in form.getlist("delivery_morning_channels") if str(item).strip()],
        "delivery.intraday_channels": [str(item).lower() for item in form.getlist("delivery_intraday_channels") if str(item).strip()],
        "delivery.breaking_channels": [str(item).lower() for item in form.getlist("delivery_breaking_channels") if str(item).strip()],
        "delivery.morning_brief_time": str(form.get("delivery_morning_brief_time", "")).strip(),
        "delivery.quiet_hours_start": str(form.get("delivery_quiet_hours_start", "")).strip(),
        "delivery.quiet_hours_end": str(form.get("delivery_quiet_hours_end", "")).strip(),
        "delivery.hourly_updates": "delivery_hourly_updates" in form,
        "delivery.breaking_alerts": "delivery_breaking_alerts" in form,
        "delivery.intraday_global_risk_enabled": "delivery_intraday_global_risk_enabled" in form,
        "delivery.llm_email_morning": "delivery_llm_email_morning" in form,
        "delivery.llm_shadow_mode": "delivery_llm_shadow_mode" in form,
    }
    return updates


def _section_updates_from_form(form) -> dict[str, Any]:
    section_keys = (
        "market_setup",
        "macro_context",
        "global_news",
        "top_themes",
        "portfolio_focus",
        "sector_scan",
        "watchlist",
        "watchlist_snapshot",
    )
    updates: dict[str, Any] = {}
    for section in section_keys:
        key = "sections.global_news" if section == "global_news" else f"sections.morning.{section}"
        updates[key] = f"section_{section}" in form
    return updates


def _vertical_updates_from_form(form) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "verticals.healthcare.mode": str(form.get("verticals_healthcare_mode", "")).strip().lower(),
        "verticals.healthcare.priority": str(form.get("verticals_healthcare_priority", "")).strip().lower(),
        "verticals.healthcare.max_items.morning": int(str(form.get("verticals_healthcare_max_items_morning", "4")).strip() or "4"),
        "verticals.healthcare.max_items.intraday": int(str(form.get("verticals_healthcare_max_items_intraday", "3")).strip() or "3"),
        "verticals.healthcare.min_severity": str(form.get("verticals_healthcare_min_severity", "")).strip().lower(),
        "verticals.healthcare.portfolio_weight_threshold": float(
            str(form.get("verticals_healthcare_portfolio_weight_threshold", "0")).strip() or "0"
        ),
        "verticals.healthcare.watchlist_count_threshold": int(
            str(form.get("verticals_healthcare_watchlist_count_threshold", "1")).strip() or "1"
        ),
    }
    return updates


def _simulation_payload_from_form(form) -> dict[str, Any]:
    methods = [str(item).strip().lower() for item in form.getlist("simulation_methods") if str(item).strip()]
    holdings_symbols = list(form.getlist("simulation_holding_symbol"))
    holdings_weights = list(form.getlist("simulation_holding_weight"))
    holdings: list[dict[str, Any]] = []
    for idx, symbol in enumerate(holdings_symbols):
        sym = str(symbol).strip().upper()
        if not sym:
            continue
        raw_weight = holdings_weights[idx] if idx < len(holdings_weights) else ""
        try:
            weight = float(str(raw_weight).strip())
        except (TypeError, ValueError):
            weight = 0.0
        holdings.append({"symbol": sym, "weight_pct": weight})

    return {
        "name": str(form.get("simulation_name", "")).strip(),
        "mode": str(form.get("simulation_mode", "portfolio")).strip().lower(),
        "methods": methods,
        "frequency": str(form.get("simulation_frequency", "monthly")).strip().lower(),
        "horizon_preset": str(form.get("simulation_horizon_preset", "")).strip().lower(),
        "horizon_periods": str(form.get("simulation_horizon_periods", "")).strip(),
        "simulation_count_preset": str(form.get("simulation_count_preset", "")).strip().lower(),
        "simulation_count": str(form.get("simulation_count", "")).strip(),
        "assumption_source": str(form.get("simulation_assumption_source", "historical")).strip().lower(),
        "benchmark_symbol": str(form.get("simulation_benchmark_symbol", "")).strip().upper(),
        "start_value": str(form.get("simulation_start_value", "")).strip(),
        "macro_overrides": {
            "growth_shock": str(form.get("simulation_growth_shock", "")).strip(),
            "inflation_shock": str(form.get("simulation_inflation_shock", "")).strip(),
            "rates_shock": str(form.get("simulation_rates_shock", "")).strip(),
            "volatility_regime": str(form.get("simulation_volatility_regime", "")).strip(),
            "correlation_stress": str(form.get("simulation_correlation_stress", "")).strip(),
        },
        "holdings": holdings,
    }


def _build_morning_chart_preview(*, settings: Settings, profile: str) -> dict[str, Any]:
    """Build deterministic morning-chart bundle for web preview/API."""
    normalized_profile = _normalize_profile(profile)
    user_profile = _load_profile_defaults(settings, normalized_profile)
    universe = load_sector_universe(settings)
    market_svc = MarketDataService(settings)
    macro_svc = MacroDataService(settings)

    setup = MarketSetup()
    if universe.all_index_symbols:
        index_quotes = market_svc.get_quotes(universe.all_index_symbols)
        index_name_map = {item.symbol: item.display for item in universe.indices}
        for quote in index_quotes:
            quote.display_name = index_name_map.get(quote.symbol, quote.display_name or quote.symbol)
        setup.index_quotes = index_quotes
    if universe.all_macro_symbols:
        macro_quotes = market_svc.get_quotes(universe.all_macro_symbols)
        macro_name_map = {item.symbol: item.display for item in universe.macro_instruments}
        for quote in macro_quotes:
            quote.display_name = macro_name_map.get(quote.symbol, quote.display_name or quote.symbol)
        setup.macro_quotes = macro_quotes

    briefing = MorningBriefing(
        market_setup=setup,
        macro_context=macro_svc.get_morning_macro(),
        watchlist_quotes=market_svc.get_quotes(user_profile.watchlist_primary[:8]) if user_profile.watchlist_primary else [],
        portfolio_quotes=market_svc.get_quotes(user_profile.portfolio_symbols[:8]) if user_profile.portfolio_symbols else [],
    )

    assets = MorningChartBuilder(profile=user_profile, market_data=market_svc).build(briefing)
    return {
        "profile": normalized_profile,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "bundle": briefing.morning_chart_bundle or {},
        "selection": briefing.morning_chart_selection or [],
        "assets": [
            {"key": asset.key, "title": asset.title, "caption": asset.caption}
            for asset in assets
        ],
    }


def _policy_updates_from_form(form) -> dict[str, Any]:
    prohibited_assets = [
        item.strip()
        for item in str(form.get("policy_prohibited_assets", "")).split(",")
        if item.strip()
    ]
    investor_type = str(form.get("policy_investor_type", "")).strip().lower()
    if investor_type and investor_type not in _INVESTOR_TYPE_VALUES:
        investor_type = "other"

    base_currency = str(form.get("policy_base_currency", "")).strip().upper()
    if base_currency and base_currency not in _BASE_CURRENCY_VALUES:
        base_currency = "OTHER"

    rebalancing_policy = str(form.get("policy_rebalancing_policy", "")).strip().lower()
    if rebalancing_policy and rebalancing_policy not in _REBALANCING_POLICY_VALUES:
        rebalancing_policy = "threshold"

    governance_frequency = str(form.get("policy_governance_review_frequency", "")).strip().lower()
    if governance_frequency and governance_frequency not in _GOVERNANCE_FREQUENCY_VALUES:
        governance_frequency = "quarterly"

    return {
        "investor_type": investor_type,
        "base_currency": base_currency,
        "investment_horizon_years": str(form.get("policy_investment_horizon_years", "")).strip(),
        "liquidity_need_percent": str(form.get("policy_liquidity_need_percent", "")).strip(),
        "target_return_percent": str(form.get("policy_target_return_percent", "")).strip(),
        "max_volatility_percent": str(form.get("policy_max_volatility_percent", "")).strip(),
        "max_drawdown_percent": str(form.get("policy_max_drawdown_percent", "")).strip(),
        "single_name_limit_percent": str(form.get("policy_single_name_limit_percent", "")).strip(),
        "max_equity_percent": str(form.get("policy_max_equity_percent", "")).strip(),
        "min_liquid_assets_percent": str(form.get("policy_min_liquid_assets_percent", "")).strip(),
        "benchmark_policy": str(form.get("policy_benchmark_policy", "")).strip(),
        "rebalancing_policy": rebalancing_policy,
        "prohibited_assets": prohibited_assets,
        "governance_review_frequency": governance_frequency,
        "notes": str(form.get("policy_notes", "")).strip(),
    }


def _allocation_updates_from_form(form) -> list[dict[str, Any]]:
    asset_classes = list(form.getlist("allocation_asset_class"))
    roles = list(form.getlist("allocation_role"))
    targets = list(form.getlist("allocation_target"))
    minimums = list(form.getlist("allocation_min"))
    maximums = list(form.getlist("allocation_max"))
    rows: list[dict[str, Any]] = []
    for index, asset_class in enumerate(asset_classes):
        asset_text = str(asset_class).strip().lower()
        if not asset_text:
            continue
        rows.append(
            {
                "asset_class": asset_text,
                "role": (
                    str(roles[index] if index < len(roles) else "").strip().lower()
                    if str(roles[index] if index < len(roles) else "").strip().lower() in _ALLOCATION_ROLE_VALUES
                    else "other"
                ),
                "target_weight_pct": str(targets[index] if index < len(targets) else "").strip(),
                "min_weight_pct": str(minimums[index] if index < len(minimums) else "").strip(),
                "max_weight_pct": str(maximums[index] if index < len(maximums) else "").strip(),
            }
        )
    return rows


def _benchmark_updates_from_form(form) -> dict[str, Any]:
    return {
        "benchmark_type": str(form.get("benchmark_type", "market_index")).strip(),
        "name": str(form.get("benchmark_name", "")).strip(),
        "base_symbol": str(form.get("benchmark_base_symbol", "")).strip().upper(),
        "components": str(form.get("benchmark_components", "")).strip(),
        "notes": str(form.get("benchmark_notes", "")).strip(),
    }


def _cma_entries_from_form(form) -> list[dict[str, Any]]:
    asset_classes = list(form.getlist("cma_asset_class"))
    returns = list(form.getlist("cma_expected_return"))
    vols = list(form.getlist("cma_expected_vol"))
    notes_list = list(form.getlist("cma_notes"))
    rows: list[dict[str, Any]] = []
    for index, asset_class in enumerate(asset_classes):
        ac = str(asset_class).strip().lower()
        if not ac:
            continue
        rows.append({
            "asset_class": ac,
            "expected_return_pct": str(returns[index] if index < len(returns) else "0").strip(),
            "expected_volatility_pct": str(vols[index] if index < len(vols) else "0").strip(),
            "notes": str(notes_list[index] if index < len(notes_list) else "").strip(),
        })
    return rows


def _cma_correlations_from_form(form) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in form.multi_items():
        text_key = str(key)
        if text_key.startswith("corr__"):
            parts = text_key[len("corr__"):].split("__")
            if len(parts) == 2:
                ac_a, ac_b = parts[0], parts[1]
                try:
                    corr = float(str(value).strip())
                except (ValueError, TypeError):
                    corr = 0.0
                rows.append({"asset_class_a": ac_a, "asset_class_b": ac_b, "correlation": corr})
    return rows


def _rebalancing_config_from_form(form) -> dict[str, Any]:
    def _f(key: str, default: str = "") -> str:
        return str(form.get(key, default)).strip()

    portfolio_value_raw = _f("rebalance_portfolio_value")
    portfolio_value = float(portfolio_value_raw) if portfolio_value_raw else None

    return {
        "method": _f("rebalance_method", "drift_threshold"),
        "drift_threshold_pct": float(_f("rebalance_drift_threshold_pct", "5.0") or "5.0"),
        "frequency": _f("rebalance_frequency", "quarterly"),
        "portfolio_value": portfolio_value,
        "transaction_cost_bps": float(_f("rebalance_transaction_cost_bps", "10.0") or "10.0"),
        "min_trade_pct": float(_f("rebalance_min_trade_pct", "0.5") or "0.5"),
        "tax_aware": "rebalance_tax_aware" in form,
        "notes": _f("rebalance_notes"),
    }


def _normalize_profile(profile: str) -> str:
    return (profile or "default_user").strip() or "default_user"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _settings_from_app(app: FastAPI) -> Settings:
    return app.state.settings
