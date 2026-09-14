from __future__ import annotations

import sqlite3

from lucid_memories.storage.db import connect

JOB_KINDS = ("subagent", "declared", "shell")
JOB_STATUSES = ("pending", "running", "blocked", "done", "error", "aborted", "stale")
KNOWLEDGE_KINDS = ("fact", "decision", "finding", "pointer", "warning", "handoff", "idea")
KNOWLEDGE_SCOPES = ("global", "workspace", "session")
PACK_KINDS = ("primer", "handoff", "compact_snapshot", "job_board")
NOTICE_KINDS = ("job_done", "please_load", "conflict", "compacted")
SOURCES = ("hook", "cli", "mcp")


def _conn() -> sqlite3.Connection:
    return connect()

