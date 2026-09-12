"""Compatibility alias for lucid_memories.core.graph."""
from .core import graph as _module
from .core.graph import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
