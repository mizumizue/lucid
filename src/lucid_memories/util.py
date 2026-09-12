"""Compatibility alias for lucid_memories.runtime.util."""
from .runtime import util as _module
from .runtime.util import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
