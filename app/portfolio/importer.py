"""Portfolio holdings import helpers (YAML + CSV)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import yaml

from app.schemas.portfolio import PortfolioHolding, PortfolioSnapshot


def load_holdings_file(
    path: Path,
    default_profile: str = "default_user",
    default_as_of_date: date | None = None,
) -> PortfolioSnapshot:
    """Load holdings from YAML or CSV and return a typed snapshot."""
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return _load_yaml(path, default_profile=default_profile, default_as_of_date=default_as_of_date)
    if suffix == ".csv":
        return _load_csv(path, default_profile=default_profile, default_as_of_date=default_as_of_date)
    raise ValueError(f"Unsupported holdings file type: {path.suffix}")


def _load_yaml(
    path: Path,
    default_profile: str,
    default_as_of_date: date | None,
) -> PortfolioSnapshot:
    with open(path) as handle:
        data = yaml.safe_load(handle) or {}

    profile_name = str(data.get("profile") or data.get("profile_name") or default_profile).strip() or default_profile
    as_of_value = data.get("as_of_date")
    as_of_date = _parse_date(as_of_value) if as_of_value else default_as_of_date

    holdings_raw = data.get("holdings") or []
    if not isinstance(holdings_raw, list):
        raise ValueError("`holdings` must be a list in YAML holdings files")

    holdings: list[PortfolioHolding] = []
    for idx, row in enumerate(holdings_raw, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Invalid holdings row at index {idx}: expected object")
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            raise ValueError(f"Missing symbol in holdings row {idx}")
        holdings.append(
            PortfolioHolding(
                profile_name=profile_name,
                symbol=symbol,
                weight_pct=_parse_optional_float(row.get("weight_pct")),
                shares=_parse_optional_float(row.get("shares")),
                avg_cost=_parse_optional_float(row.get("avg_cost")),
                account=_clean_optional_text(row.get("account")),
                bucket=_clean_optional_text(row.get("bucket")),
                sector_override=_clean_optional_text(row.get("sector_override")),
                active=bool(row.get("active", True)),
                as_of_date=_parse_date(row.get("as_of_date")) if row.get("as_of_date") else as_of_date,
            )
        )
    return PortfolioSnapshot(profile_name=profile_name, as_of_date=as_of_date, holdings=holdings)


def _load_csv(
    path: Path,
    default_profile: str,
    default_as_of_date: date | None,
) -> PortfolioSnapshot:
    holdings: list[PortfolioHolding] = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row_num, row in enumerate(reader, start=2):
            symbol = _clean_optional_text(row.get("symbol"))
            non_symbol_fields = [
                _clean_optional_text(row.get("weight_pct")),
                _clean_optional_text(row.get("shares")),
                _clean_optional_text(row.get("avg_cost")),
                _clean_optional_text(row.get("account")),
                _clean_optional_text(row.get("bucket")),
                _clean_optional_text(row.get("sector_override")),
            ]
            if not symbol and not any(non_symbol_fields):
                continue
            if not symbol:
                raise ValueError(f"Missing symbol in holdings CSV row {row_num}")

            holdings.append(
                PortfolioHolding(
                    profile_name=default_profile,
                    symbol=symbol,
                    weight_pct=_parse_optional_float(row.get("weight_pct")),
                    shares=_parse_optional_float(row.get("shares")),
                    avg_cost=_parse_optional_float(row.get("avg_cost")),
                    account=_clean_optional_text(row.get("account")),
                    bucket=_clean_optional_text(row.get("bucket")),
                    sector_override=_clean_optional_text(row.get("sector_override")),
                    active=True,
                    as_of_date=default_as_of_date,
                )
            )

    return PortfolioSnapshot(
        profile_name=default_profile,
        as_of_date=default_as_of_date,
        holdings=holdings,
    )


def _parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("Empty date string")
    return date.fromisoformat(text)


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
