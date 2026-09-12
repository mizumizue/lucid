"""Compatibility alias for lucid_memories.core.persona."""
from .core import persona as _module
from .core.persona import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
