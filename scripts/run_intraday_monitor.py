#!/usr/bin/env python3
"""Run a single intraday update cycle as a standalone script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logger import setup_logging
from app.main import run_intraday_update

if __name__ == "__main__":
    setup_logging()
    run_intraday_update()
