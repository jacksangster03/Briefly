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
    fred_api_key: str = ""
    ecb_base_url: str = "https://data-api.ecb.europa.eu/service"
    eurostat_base_url: str = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
    bls_api_key: str = ""
    bls_base_url: str = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    bea_api_key: str = ""
    bea_base_url: str = "https://apps.bea.gov/api/data"
    sec_user_agent: str = "market-briefing-bot contact@example.com"
    polygon_api_key: str = ""
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
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
    # Optional cost estimation (per 1M tokens). When both are > 0, usage logs
    # include estimated per-run cost for the LLM render call.
    llm_email_input_cost_per_1m_tokens: float = 0.0
    llm_email_output_cost_per_1m_tokens: float = 0.0
    # all | telegram | email
    delivery_channel: str = "all"
    web_host: str = "127.0.0.1"
    web_port: int = 8080

    # -- Paths ----------------------------------------------------------------
    configs_dir: str = str(PROJECT_ROOT / "configs")
    logs_dir: str = str(PROJECT_ROOT / "logs")
    data_dir: str = str(PROJECT_ROOT / "data")

    # -- Provider timeouts (seconds) ------------------------------------------
    provider_timeout: int = 30
    provider_max_retries: int = 2

    # -- Convenience helpers --------------------------------------------------
    @property
    def finnhub_configured(self) -> bool:
        return bool(self.finnhub_api_key)

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
    def newsapi_configured(self) -> bool:
        return bool(self.newsapi_key)

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
