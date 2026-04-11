"""Schemas for portfolio holdings and import payloads."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, field_validator


class PortfolioHolding(BaseModel):
    """Single portfolio position used for personalization."""

    profile_name: str = "default_user"
    symbol: str
    weight_pct: float | None = None
    shares: float | None = None
    avg_cost: float | None = None
    account: str | None = None
    bucket: str | None = None
    sector_override: str | None = None
    active: bool = True
    as_of_date: date | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        symbol = (value or "").strip().upper()
        if not symbol:
            raise ValueError("Holding symbol is required")
        return symbol

    @field_validator("bucket", mode="before")
    @classmethod
    def _normalize_bucket(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        return text or None

    @field_validator("sector_override", mode="before")
    @classmethod
    def _normalize_sector_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        return text or None

    @field_validator("weight_pct")
    @classmethod
    def _validate_weight_pct(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("weight_pct cannot be negative")
        return value

    @property
    def effective_weight(self) -> float:
        """Weight used for ranking; falls back to 0 when unknown."""
        return self.weight_pct or 0.0


class PortfolioSnapshot(BaseModel):
    """Imported holdings snapshot for one profile."""

    profile_name: str = "default_user"
    as_of_date: date | None = None
    holdings: list[PortfolioHolding]
