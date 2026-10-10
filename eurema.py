#!/usr/bin/env python3
"""Eurema research engine. Usage: python3 ../00_eurema/eurema.py <command> (from a project folder)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
