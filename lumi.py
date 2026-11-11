#!/usr/bin/env python3
"""Lumi v2 library commands. Usage: python3 lumi.py library check."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lumi.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
