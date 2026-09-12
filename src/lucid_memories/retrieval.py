"""Compatibility alias for lucid_memories.core.retrieval."""
from .core import retrieval as _module
from .core.retrieval import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
