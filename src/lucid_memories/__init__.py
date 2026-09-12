"""lucid-memories: shared SQLite store for Cursor agents."""
from __future__ import annotations

from .core.api import (
    archive,
    backfill_embeddings,
    dashboard_graph,
    embedding_status,
    eval_retrieval,
    improve_from_logs,
    job_claim,
    job_done,
    job_start,
    job_update,
    list_knowledge,
    load,
    load_handoff,
    measure_retrieval,
    reload,
    remember,
    save_handoff,
    search,
    semantic_search,
    snapshot_compact,
    status,
    whoami,
)
from .core.graph import confirm, forbid, link, map_status, recall

# Subpackages and modules exposed at package level
from .core import api, gate, graph, memory, persona, retrieval
from .storage import blobs, db, paths
from .web import dashboard
from .runtime import embedding, ladybug_runtime, util
from .entrypoints import cli, hook, mcp_server

__all__ = [
    "whoami",
    "status",
    "search",
    "list_knowledge",
    "load",
    "remember",
    "archive",
    "semantic_search",
    "embedding_status",
    "backfill_embeddings",
    "dashboard_graph",
    "measure_retrieval",
    "eval_retrieval",
    "improve_from_logs",
    "reload",
    "save_handoff",
    "load_handoff",
    "job_start",
    "job_update",
    "job_done",
    "job_claim",
    "snapshot_compact",
    "recall",
    "link",
    "forbid",
    "confirm",
    "map_status",
    # Modules
    "api",
    "gate",
    "graph",
    "memory",
    "persona",
    "retrieval",
    "blobs",
    "db",
    "paths",
    "dashboard",
    "embedding",
    "ladybug_runtime",
    "util",
    "cli",
    "hook",
    "mcp_server",
]
