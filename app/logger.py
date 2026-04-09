"""Structured logging setup for market-briefing-bot."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.settings import get_settings

_CONFIGURED = False


def setup_logging() -> logging.Logger:
    """Configure and return the root application logger.

    Safe to call multiple times; only configures once.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return logging.getLogger("mbb")

    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logger = logging.getLogger("mbb")
    logger.setLevel(log_level)
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler
    log_dir = Path(settings.logs_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "mbb.log", encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _CONFIGURED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the mbb namespace."""
    setup_logging()
    return logging.getLogger(f"mbb.{name}")
