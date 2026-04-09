#!/usr/bin/env python3
"""Run the morning briefing pipeline as a standalone script."""

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.logger import setup_logging
from app.main import run_morning_briefing

if __name__ == "__main__":
    setup_logging()
    run_morning_briefing()
