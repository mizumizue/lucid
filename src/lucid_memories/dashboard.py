"""Compatibility alias for lucid_memories.web.dashboard."""
from .web import dashboard as _module
from .web.dashboard import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
