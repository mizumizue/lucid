"""Compatibility alias for lucid_memories.entrypoints.mcp_server."""
from .entrypoints import mcp_server as _module
from .entrypoints.mcp_server import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
