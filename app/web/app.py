"""Phase 4.3 FastAPI + HTMX Briefly control center."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.db.session import init_db
from app.logger import get_logger
from app.settings import Settings, get_settings
from app.web.control_plane_service import (
    apply_preference_updates,
    build_profile_state,
    import_holdings_from_upload,
    refresh_risk_for_profile,
    remove_preference,
    reset_preferences,
    save_allocation,
    save_benchmark,
    save_holdings_from_form,
    save_policy,
    save_risk_config,
    search_followables,
)

logger = get_logger("web")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class PreferenceUpdateRequest(BaseModel):
    """Bulk preference payload for API updates."""

    updates: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class AllocationUpdateRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class BenchmarkUpdateRequest(BaseModel):
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

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ui/settings", status_code=307)

    @app.get("/ui/settings", response_class=HTMLResponse, include_in_schema=False)
    def ui_settings(
        request: Request,
        profile: str = Query(default="default_user"),
    ):
        normalized_profile = _normalize_profile(profile)
        state = build_profile_state(_settings(request), normalized_profile)
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "state": state,
                "message": "",
                "message_kind": "info",
            },
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
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Coverage preferences saved to DB overrides.",
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
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Delivery preferences saved to DB overrides.",
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
        return _render_ui_after_update(
            request,
            profile=normalized_profile,
            updates=updates,
            success_message="Morning section visibility saved to DB overrides.",
        )

    @app.post(
        "/ui/profile/{profile}/save/policy",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_policy(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
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
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Policy save failed: {exc}",
                message_kind="error",
                status_code=400,
            )

    @app.post(
        "/ui/profile/{profile}/save/allocation",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_allocation(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
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
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Allocation save failed: {exc}",
                message_kind="error",
                status_code=400,
            )

    @app.post(
        "/ui/profile/{profile}/save/benchmark",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_benchmark(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
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
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Benchmark save failed: {exc}",
                message_kind="error",
                status_code=400,
            )

    @app.post(
        "/ui/profile/{profile}/holdings/save",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_save_holdings(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
        form = await request.form()
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
            )
        except (ValueError, Exception) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Holdings save failed: {exc}",
                message_kind="error",
                status_code=400,
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
    ):
        normalized_profile = _normalize_profile(profile)
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
            )
        except ValueError as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Holdings import failed: {exc}",
                message_kind="error",
                status_code=400,
            )

    @app.post(
        "/ui/profile/{profile}/reset",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_reset_profile_preferences(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
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
            )
        except (ValueError, TypeError) as exc:
            return _render_settings_root(
                request,
                profile=normalized_profile,
                message=f"Risk config save failed: {exc}",
                message_kind="error",
                status_code=400,
            )

    @app.post(
        "/ui/profile/{profile}/refresh/risk",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def ui_refresh_risk(request: Request, profile: str):
        normalized_profile = _normalize_profile(profile)
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

    return app


def _render_ui_after_update(
    request: Request,
    *,
    profile: str,
    updates: dict[str, Any],
    success_message: str,
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
        )
    except ValueError as exc:
        return _render_settings_root(
            request,
            profile=profile,
            message=f"Save failed: {exc}",
            message_kind="error",
            status_code=400,
        )


def _render_settings_root(
    request: Request,
    *,
    profile: str,
    message: str,
    message_kind: str,
    status_code: int = 200,
    state: dict[str, Any] | None = None,
) -> HTMLResponse:
    state = state or build_profile_state(_settings(request), profile)
    return templates.TemplateResponse(
        request,
        "partials/settings_root.html",
        {
            "state": state,
            "message": message,
            "message_kind": message_kind,
        },
        status_code=status_code,
    )


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
    )
    updates: dict[str, Any] = {}
    for section in section_keys:
        key = "sections.global_news" if section == "global_news" else f"sections.morning.{section}"
        updates[key] = f"section_{section}" in form
    return updates


def _policy_updates_from_form(form) -> dict[str, Any]:
    prohibited_assets = [
        item.strip()
        for item in str(form.get("policy_prohibited_assets", "")).split(",")
        if item.strip()
    ]
    return {
        "investor_type": str(form.get("policy_investor_type", "")).strip(),
        "base_currency": str(form.get("policy_base_currency", "")).strip().upper(),
        "investment_horizon_years": str(form.get("policy_investment_horizon_years", "")).strip(),
        "liquidity_need_percent": str(form.get("policy_liquidity_need_percent", "")).strip(),
        "target_return_percent": str(form.get("policy_target_return_percent", "")).strip(),
        "max_volatility_percent": str(form.get("policy_max_volatility_percent", "")).strip(),
        "max_drawdown_percent": str(form.get("policy_max_drawdown_percent", "")).strip(),
        "single_name_limit_percent": str(form.get("policy_single_name_limit_percent", "")).strip(),
        "max_equity_percent": str(form.get("policy_max_equity_percent", "")).strip(),
        "min_liquid_assets_percent": str(form.get("policy_min_liquid_assets_percent", "")).strip(),
        "benchmark_policy": str(form.get("policy_benchmark_policy", "")).strip(),
        "rebalancing_policy": str(form.get("policy_rebalancing_policy", "")).strip(),
        "prohibited_assets": prohibited_assets,
        "governance_review_frequency": str(form.get("policy_governance_review_frequency", "")).strip(),
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
                "role": str(roles[index] if index < len(roles) else "").strip().lower(),
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


def _normalize_profile(profile: str) -> str:
    return (profile or "default_user").strip() or "default_user"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _settings_from_app(app: FastAPI) -> Settings:
    return app.state.settings
