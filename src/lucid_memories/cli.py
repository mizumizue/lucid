"""Compatibility alias for lucid_memories.entrypoints.cli."""
from .entrypoints import cli as _module
from .entrypoints.cli import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
