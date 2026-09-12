#!/usr/bin/env python3
"""Standalone script to run Git privacy check."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
src_dir = ROOT / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from lucid_memories.core.privacy_gate import main

if __name__ == "__main__":
    sys.exit(main())
