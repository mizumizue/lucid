"""Compatibility alias for lucid_memories.storage.blobs."""
from .storage import blobs as _module
from .storage.blobs import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
