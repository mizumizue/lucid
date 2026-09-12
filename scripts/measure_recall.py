#!/usr/bin/env python3
"""Measure lucid-memories retrieval hit rates from retrieval_logs (and optional eval)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lucid_memories.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] != "measure":
        argv = ["measure", *argv]
    raise SystemExit(main(argv))
