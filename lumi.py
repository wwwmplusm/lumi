#!/usr/bin/env python3
"""Lumi research engine. Usage: python3 lumi.py <command> (from a project folder)."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parent
if (root / "engine").is_dir():
    sys.path.insert(0, str(root))
else:
    # Installed in share/lumi: this script must not shadow the lumi package.
    sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != root]

from lumi.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
