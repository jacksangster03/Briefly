"""Centralised application settings loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Data providers -------------------------------------------------------
    finnhub_api_key: str = ""
    newsapi_key: str = ""
    marketaux_api_key: str = ""
    alpha_vantage_api_key: str = ""
    fmp_api_key: str = ""
    mediastack_api_key: str = ""
    fred_api_key: str = ""
    ecb_base_url: str = "https://data-api.ecb.europa.eu/service"
    eurostat_base_url: str = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
    bls_api_key: str = ""
    bls_base_url: str = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    bea_api_key: str = ""
    bea_base_url: str = "https://apps.bea.gov/api/data"
    sec_user_agent: str = "Briefly contact@example.com"
    fda_openfda_base_url: str = "https://api.fda.gov"
    fda_openfda_api_key: str = ""
    clinicaltrials_base_url: str = "https://clinicaltrials.gov/api/v2"
    ema_medicines_base_url: str = "https://www.ema.europa.eu/en/medicines"
    polygon_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    gdelt_base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    alpha_vantage_base_url: str = "https://www.alphavantage.co/query"
    fmp_base_url: str = "https://financialmodelingprep.com/stable/news/general-latest"
    mediastack_base_url: str = "https://api.mediastack.com/v1/news"
    marketaux_base_url: str = "https://api.marketaux.com/v1/news/all"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    x_bearer_token: str = ""

    # -- Delivery: Telegram ---------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # -- Delivery: Email ------------------------------------------------------
    email_host: str = "smtp.gmail.com"
    email_port: int = 587
    email_user: str = ""
    email_password: str = ""
    email_to: str = ""

    # -- Database -------------------------------------------------------------
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'state' / 'market_briefing.db'}"

    # -- General --------------------------------------------------------------
    timezone: str = "Europe/Madrid"
    dry_run: bool = True
    allow_live_trading: bool = False
    log_level: str = "INFO"
    # Manual/test helper: when True, the delivery pipeline prints the
    # rendered message payload to the terminal. Independent of dry_run so
    # live sends can also be inspected locally. Set via CLI --show-output.
    show_output: bool = False
    enable_charts: bool = True
    telegram_send_charts: bool = False
    enable_llm_email_render: bool = False
    llm_render_shadow_mode: bool = True
    llm_email_model: str = "gpt-4o-mini"
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_email_timeout_seconds: int = 25
    llm_email_max_chars: int = 3200
    llm_email_min_source_urls: int = 2
    persist_dry_run_session_snapshots: bool = False
    # Optional cost estimation (per 1M tokens). When both are > 0, usage logs
    # include estimated per-run cost for the LLM render call.
    llm_email_input_cost_per_1m_tokens: float = 0.0
    llm_email_output_cost_per_1m_tokens: float = 0.0
    # Monthly spend cap in USD. 0 = no limit. Requires cost rates to be set;
    # if rates are zero the cap is unenforced even when set.
    llm_monthly_budget_usd: float = 0.0
    # all | telegram | email
    delivery_channel: str = "all"
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    web_public_base_url: str = ""
    breaking_followup_delay_minutes: int = 10
    breaking_storyline_cooldown_minutes: int = 45
    breaking_max_alerts_per_hour: int = 3
    breaking_followup_min_asset_move_pct: float = 0.9
    enable_finbert: bool = False
    enable_garch: bool = False

    # -- Paths ----------------------------------------------------------------
    configs_dir: str = str(PROJECT_ROOT / "configs")
    logs_dir: str = str(PROJECT_ROOT / "logs")
    data_dir: str = str(PROJECT_ROOT / "data")

    # -- Provider timeouts (seconds) ------------------------------------------
    provider_timeout: int = 30
    provider_max_retries: int = 2
    # -- Phase 4.5 global-news expansion -------------------------------------
    # Provider toggles
    enable_gdelt: bool = False
    enable_alpha_vantage_news: bool = False
    enable_fmp_news: bool = False
    enable_mediastack_news: bool = False
    enable_marketaux_news: bool = False
    # Daily call budgets (process-level guard; reset each process day)
    gdelt_daily_call_budget: int = 250
    alpha_vantage_news_daily_call_budget: int = 20
    fmp_news_daily_call_budget: int = 120
    mediastack_news_daily_call_budget: int = 3
    marketaux_news_daily_call_budget: int = 100
    # Request shaping
    global_news_max_records: int = 50
    alpha_vantage_topics: str = "economy_macro,economy_monetary,energy_transportation,financial_markets"
    gdelt_global_query: str = "(inflation OR sanctions OR tariffs OR oil OR shipping OR blockade OR war OR ceasefire OR central bank OR rates OR treasury OR dollar OR fx OR supply chain)"
    fmp_news_limit: int = 50
    mediastack_news_limit: int = 25
    marketaux_news_limit: int = 50

    # -- Phase 6/7 chart modules ---------------------------------------------
    feature_yield_curve_card: bool = True
    feature_vix_risk_card: bool = True
    feature_geo_confirmation_ladder: bool = True
    feature_regional_divergence_score: bool = True
    feature_oil_transmission_card: bool = True
    feature_dynamic_chart_stack: bool = True
    feature_email_density_mode: bool = True
    # -- Phase: News intelligence classifier ---------------------------------
    news_breaking_max_age_hours: int = 6
    enable_llm_news_classifier: bool = False
    llm_news_classifier_shadow_mode: bool = True
    llm_news_classifier_model: str = "gpt-4o-mini"
    llm_news_classifier_max_items_per_run: int = 8
    llm_news_classifier_monthly_budget_usd: float = 0.0
    # -- Phase 1 local ML news classifier foundation -------------------------
    enable_ml_news_classifier: bool = False
    ml_news_classifier_shadow_mode: bool = True
    ml_news_classifier_model_path: str = ""
    ml_news_classifier_max_items: int = 100
    ml_news_classifier_min_score: float = 0.70
    ml_news_classifier_timeout_seconds: float = 2.0
    ml_news_classifier_use_in_live: bool = False

    # -- Convenience helpers --------------------------------------------------
    @property
    def finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)

    @property
    def fred_configured(self) -> bool:
        return bool(self.fred_api_key)

    @property
    def bls_configured(self) -> bool:
        return bool(self.bls_api_key)

    @property
    def bea_configured(self) -> bool:
        return bool(self.bea_api_key)

    @property
    def ecb_configured(self) -> bool:
        return bool(self.ecb_base_url)

    @property
    def eurostat_configured(self) -> bool:
        return bool(self.eurostat_base_url)

    @property
    def newsapi_configured(self) -> bool:
        return bool(self.newsapi_key)

    @property
    def alpha_vantage_configured(self) -> bool:
        return bool(self.alpha_vantage_api_key)

    @property
    def marketaux_configured(self) -> bool:
        return bool(self.marketaux_api_key)

    @property
    def fmp_configured(self) -> bool:
        return bool(self.fmp_api_key)

    @property
    def mediastack_configured(self) -> bool:
        return bool(self.mediastack_api_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_configured(self) -> bool:
        return bool(self.email_user and self.email_password and self.email_to)

    @property
    def normalized_delivery_channel(self) -> str:
        channel = (self.delivery_channel or "all").strip().lower()
        if channel in {"all", "telegram", "email"}:
            return channel
        return "all"


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
