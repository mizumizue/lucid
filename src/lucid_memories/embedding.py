"""Compatibility alias for lucid_memories.runtime.embedding."""
from .runtime import embedding as _module
from .runtime.embedding import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
