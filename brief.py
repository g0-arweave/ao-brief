#!/usr/bin/env python3
"""Compatibility launcher for legacy workflow paths.

Delegates execution to daily-github-brief/brief.py.
"""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "daily-github-brief" / "brief.py"
    runpy.run_path(str(target), run_name="__main__")
