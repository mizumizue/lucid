"""Compatibility alias for lucid_memories.storage.db."""
from .storage import db as _module
from .storage.db import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
