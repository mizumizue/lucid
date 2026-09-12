"""Compatibility alias for lucid_memories.entrypoints.hook."""
from .entrypoints import hook as _module
from .entrypoints.hook import *  # noqa: F401, F403

for _k, _v in _module.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v

if __name__ == "__main__":
    main()
