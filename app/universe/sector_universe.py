"""Sector universe: loads sector definitions, ETFs, and key tickers from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.logger import get_logger
from app.settings import Settings

logger = get_logger("universe")


class SectorDef:
    """Single sector definition with its ETF and representative tickers."""

    def __init__(self, key: str, etf: str, display_name: str, key_names: list[str]):
        self.key = key
        self.etf = etf
        self.display_name = display_name
        self.key_names = key_names


class InstrumentDef:
    """Index or macro instrument definition."""

    def __init__(self, symbol: str, display: str):
        self.symbol = symbol
        self.display = display


class SectorUniverse:
    """Full universe of sectors, indices, and macro instruments."""

    def __init__(
        self,
        sectors: list[SectorDef],
        indices: list[InstrumentDef],
        macro_instruments: list[InstrumentDef],
    ):
        self.sectors = sectors
        self.indices = indices
        self.macro_instruments = macro_instruments

        # Quick lookups
        self._sector_by_key = {s.key: s for s in sectors}
        self._ticker_to_sectors: dict[str, list[str]] = {}
        for s in sectors:
            for ticker in s.key_names:
                self._ticker_to_sectors.setdefault(ticker, []).append(s.key)

    @property
    def all_sector_etfs(self) -> list[str]:
        return [s.etf for s in self.sectors if s.etf]

    @property
    def all_index_symbols(self) -> list[str]:
        return [i.symbol for i in self.indices]

    @property
    def all_macro_symbols(self) -> list[str]:
        return [m.symbol for m in self.macro_instruments]

    @property
    def all_quote_symbols(self) -> list[str]:
        """All symbols that should be quoted in the market setup."""
        return self.all_index_symbols + self.all_macro_symbols

    def sectors_for_ticker(self, ticker: str) -> list[str]:
        """Return sector keys for a given ticker."""
        return self._ticker_to_sectors.get(ticker.upper(), [])

    def get_sector(self, key: str) -> SectorDef | None:
        return self._sector_by_key.get(key)


def load_sector_universe(settings: Settings) -> SectorUniverse:
    """Load sector universe from configs/sectors.yaml."""
    config_path = Path(settings.configs_dir) / "sectors.yaml"

    if not config_path.exists():
        logger.warning("sectors.yaml not found; using empty universe")
        return SectorUniverse([], [], [])

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    sectors = []
    for key, sdata in data.get("sectors", {}).items():
        sectors.append(SectorDef(
            key=key,
            etf=sdata.get("etf", ""),
            display_name=sdata.get("display_name", key),
            key_names=sdata.get("key_names", []),
        ))

    indices = [
        InstrumentDef(symbol=i["symbol"], display=i["display"])
        for i in data.get("indices", [])
    ]

    macro_instruments = [
        InstrumentDef(symbol=m["symbol"], display=m["display"])
        for m in data.get("macro_instruments", [])
    ]

    logger.info(
        "Loaded universe: %d sectors, %d indices, %d macro instruments",
        len(sectors), len(indices), len(macro_instruments),
    )
    return SectorUniverse(sectors, indices, macro_instruments)
