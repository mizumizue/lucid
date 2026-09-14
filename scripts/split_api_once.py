#!/usr/bin/env python3
"""One-shot split of core/api.py per ADR-0010. Run from repo root."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "lucid_memories" / "core"
API = ROOT / "api.py"

FUNCTION_MODULES: dict[str, str] = {
    "_embedding_text": "embedding_ops",
    "index_embedding": "embedding_ops",
    "semantic_search": "embedding_ops",
    "embedding_status": "embedding_ops",
    "backfill_embeddings": "embedding_ops",
    "ensure_session": "session_ops",
    "persist_session_summary": "session_ops",
    "heartbeat": "session_ops",
    "bind_current_session": "session_ops",
    "unbind_current_session": "session_ops",
    "save_prompt": "session_ops",
    "bind_pending_prompt": "session_ops",
    "last_prompt": "session_ops",
    "last_turn": "session_ops",
    "generation_changed": "session_ops",
    "_usage_value": "usage_ops",
    "_usage_int": "usage_ops",
    "_usage_float": "usage_ops",
    "record_usage": "usage_ops",
    "record_mcp_usage": "usage_ops",
    "_conversation_id_from_payload": "usage_ops",
    "_payload_text": "usage_ops",
    "_payload_artifact_text": "usage_ops",
    "_payload_workspace": "usage_ops",
    "_decode_json_value": "usage_ops",
    "_artifact_paths": "usage_ops",
    "_repository_root": "usage_ops",
    "_artifact_snapshot": "usage_ops",
    "record_activity": "usage_ops",
    "load_artifact": "usage_ops",
    "_env_conversation_id": "identity_ops",
    "_bound_current_session": "identity_ops",
    "whoami": "identity_ops",
    "_effective_job_status": "identity_ops",
    "status": "identity_ops",
    "search": "knowledge_ops",
    "_knowledge_text": "knowledge_ops",
    "list_knowledge": "knowledge_ops",
    "remember": "knowledge_ops",
    "_sync_knowledge_fts": "knowledge_ops",
    "_remember_update": "knowledge_ops",
    "archive": "knowledge_ops",
    "_append_job_event": "job_ops",
    "job_start": "job_ops",
    "job_update": "job_ops",
    "job_done": "job_ops",
    "acquire_lease": "job_ops",
    "release_leases_for_session": "job_ops",
    "job_claim": "job_ops",
    "_load_pack_items": "pack_ops",
    "_load_tracking_results": "pack_ops",
    "_finish_load_tracking": "pack_ops",
    "load": "pack_ops",
    "post_notice": "pack_ops",
    "_add_pack_item": "pack_ops",
    "snapshot_compact": "pack_ops",
    "reload": "pack_ops",
    "save_handoff": "pack_ops",
    "load_handoff": "pack_ops",
    "_prompt_query": "retrieval_ops",
    "_search_hits_for_prompt": "retrieval_ops",
    "_flatten_retrieval": "retrieval_ops",
    "collect_retrieval": "retrieval_ops",
    "log_retrieval": "retrieval_ops",
    "retrieval_gaps": "retrieval_ops",
    "_patch_retrieval_eval": "retrieval_ops",
    "improve_retrieval": "retrieval_ops",
    "improve_from_logs": "retrieval_ops",
    "_match_expected": "retrieval_ops",
    "_rate": "retrieval_ops",
    "measure_retrieval": "retrieval_ops",
    "eval_retrieval": "retrieval_ops",
    "_digest_memory_lines": "retrieval_ops",
    "_dashboard_json_list": "dashboard_ops",
    "_dashboard_node_id": "dashboard_ops",
    "_dashboard_temperature": "dashboard_ops",
    "dashboard_graph": "dashboard_ops",
}

CONST_MODULES: dict[str, str] = {
    "JOB_KINDS": "api_common",
    "JOB_STATUSES": "api_common",
    "KNOWLEDGE_KINDS": "api_common",
    "KNOWLEDGE_SCOPES": "api_common",
    "PACK_KINDS": "api_common",
    "NOTICE_KINDS": "api_common",
    "SOURCES": "api_common",
    "PENDING_PROMPT_ID": "session_ops",
    "_PATH_KEYS": "usage_ops",
    "_PATCH_PATH_RE": "usage_ops",
    "_STOP_WORDS": "retrieval_ops",
    "DEFAULT_EVAL_CASES": "retrieval_ops",
}

MODULE_HEADERS: dict[str, str] = {
    "api_common": '''from __future__ import annotations

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

''',
    "embedding_ops": '''from __future__ import annotations

import os
from typing import Any

from lucid_memories.storage import blobs
from lucid_memories.runtime import embedding
from . import memory, retrieval
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.util import new_id, row_dict, truncate, workspace_matches
from .api_common import _conn

''',
    "session_ops": '''from __future__ import annotations

from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import SUMMARY_LIMIT, env
from lucid_memories.runtime.util import dumps, normalize_root, parse_roots
from .session_metadata import compute_session_summary
from .api_common import _conn
from .embedding_ops import index_embedding

PENDING_PROMPT_ID = "_pending"

''',
    "usage_ops": '''from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from lucid_memories.storage import blobs
from . import memory
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import env
from lucid_memories.runtime.util import dumps, estimate_tokens, new_id, normalize_root, row_dict, truncate
from .api_common import _conn
from .identity_ops import whoami

_PATH_KEYS = {
    "path",
    "filepath",
    "filename",
    "outputfile",
    "targetfile",
    "artifactpath",
    "transcriptpath",
    "outputpath",
}
_PATCH_PATH_RE = re.compile(r"\\*\\*\\*\\s+(?:Add|Update|Delete) File:\\s*(.+)")

''',
    "identity_ops": '''from __future__ import annotations

import os
import sqlite3
from typing import Any

from lucid_memories.storage.db import checkpoint_passive, now_iso
from lucid_memories.storage.paths import env
from lucid_memories.runtime.util import (
    is_stale_heartbeat,
    normalize_root,
    parse_roots,
    row_dict,
    workspace_contains,
    workspace_matches,
)
from .api_common import _conn

''',
    "knowledge_ops": '''from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from lucid_memories.storage import blobs
from . import memory, retrieval
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import INLINE_BODY_LIMIT
from lucid_memories.runtime.util import dumps, estimate_tokens, fts_match_arg, looks_like_id, new_id, row_dict, truncate, workspace_matches
from .api_common import KNOWLEDGE_KINDS, KNOWLEDGE_SCOPES, _conn
from .embedding_ops import index_embedding
from .identity_ops import _env_conversation_id, whoami

''',
    "job_ops": '''from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import LEASE_TTL_SECONDS
from lucid_memories.runtime.util import new_id, row_dict, truncate
from .api_common import JOB_KINDS, JOB_STATUSES, _conn
from .identity_ops import _env_conversation_id, whoami
from .session_ops import ensure_session

''',
    "pack_ops": '''from __future__ import annotations

import os
from typing import Any

from lucid_memories.storage import blobs
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import DEFAULT_LOAD_BUDGET
from lucid_memories.runtime.util import dumps, estimate_tokens, looks_like_id, new_id, normalize_root, row_dict, truncate, workspace_matches
from .api_common import NOTICE_KINDS, PACK_KINDS, _conn
from .identity_ops import _env_conversation_id, whoami
from .knowledge_ops import _knowledge_text, list_knowledge, remember, search

''',
    "retrieval_ops": '''from __future__ import annotations

import json
import os
from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.util import dumps, new_id, normalize_root, query_tokens
from . import retrieval
from .api_common import _conn
from .embedding_ops import semantic_search
from .knowledge_ops import search

_STOP_WORDS = {
    "how",
    "should",
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "for",
    "with",
    "this",
    "that",
    "what",
    "when",
    "where",
    "why",
    "is",
    "are",
    "was",
    "be",
    "do",
    "does",
    "can",
    "could",
    "would",
    "i",
    "we",
    "you",
    "it",
    "vs",
    "from",
}

DEFAULT_EVAL_CASES = [
    {
        "prompt": "指示パイプラインで検索と提案書き込みをして",
        "expect": [
            {"db": "map", "kind": "topic", "title": "指示パイプライン"},
            {"db": "sqlite", "title": "指示パイプラインは Auto、確定だけ Ask"},
        ],
    },
    {
        "prompt": "lucid-memories の文脈でパイプラインを引け",
        "expect": [
            {"db": "map", "kind": "context", "title": "lucid-memories"},
        ],
    },
]

''',
    "dashboard_ops": '''from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lucid_memories.storage.db import connect_readonly
from . import memory
from lucid_memories.runtime.util import parse_iso, truncate, workspace_matches, normalize_root
from .api_common import _conn

''',
}

STAY_IN_API = {
    "memory_status",
    "memory_candidates",
    "memory_worker",
    "promote_memory_candidate",
    "persona_candidates",
    "persona_revisions",
    "persona_worker",
    "persona_rollback",
    "persona_reject",
    "persona_workspace",
    "digest_text",
}


def main() -> None:
    source = API.read_text(encoding="utf-8")
    tree = ast.parse(source)
    segments: dict[str, list[str]] = {name: [] for name in MODULE_HEADERS}
    api_remainder: list[str] = []

    for node in tree.body:
        seg = ast.get_source_segment(source, node)
        if seg is None:
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name == "_conn":
                continue
            mod = FUNCTION_MODULES.get(node.name)
            if mod:
                segments[mod].append(seg)
            elif node.name in STAY_IN_API:
                api_remainder.append(seg)
            else:
                raise SystemExit(f"unmapped function: {node.name}")
        elif isinstance(node, ast.Assign):
            continue
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        else:
            raise SystemExit(f"unhandled top-level node: {type(node).__name__}")

    for mod, header in MODULE_HEADERS.items():
        body = "\n\n".join(segments[mod])
        if mod == "api_common":
            path = ROOT / "api_common.py"
            path.write_text(header, encoding="utf-8")
            continue
        path = ROOT / f"{mod}.py"
        path.write_text(header + body + "\n", encoding="utf-8")

    exports = sorted(
        {name for name, mod in FUNCTION_MODULES.items() if not name.startswith("_")}
        | STAY_IN_API
    )
    facade_lines = [
        'from __future__ import annotations',
        "",
        "import json",
        "import os",
        "from typing import Any",
        "",
        "from lucid_memories.storage.db import connect, now_iso, write_tx",
        "from lucid_memories.runtime.util import dumps",
        "from . import memory, persona",
        "from .api_common import (",
        "    JOB_KINDS,",
        "    JOB_STATUSES,",
        "    KNOWLEDGE_KINDS,",
        "    KNOWLEDGE_SCOPES,",
        "    NOTICE_KINDS,",
        "    PACK_KINDS,",
        "    SOURCES,",
        "    _conn,",
        ")",
        "from .dashboard_ops import dashboard_graph",
        "from .embedding_ops import (",
        "    backfill_embeddings,",
        "    embedding_status,",
        "    index_embedding,",
        "    semantic_search,",
        ")",
        "from .identity_ops import status, whoami",
        "from .job_ops import (",
        "    acquire_lease,",
        "    job_claim,",
        "    job_done,",
        "    job_start,",
        "    job_update,",
        "    release_leases_for_session,",
        ")",
        "from .knowledge_ops import archive, list_knowledge, remember, search",
        "from .pack_ops import (",
        "    load,",
        "    load_handoff,",
        "    post_notice,",
        "    reload,",
        "    save_handoff,",
        "    snapshot_compact,",
        ")",
        "from .retrieval_ops import (",
        "    collect_retrieval,",
        "    eval_retrieval,",
        "    improve_from_logs,",
        "    improve_retrieval,",
        "    log_retrieval,",
        "    measure_retrieval,",
        "    retrieval_gaps,",
        "    _digest_memory_lines,",
        ")",
        "from .session_ops import (",
        "    bind_current_session,",
        "    bind_pending_prompt,",
        "    ensure_session,",
        "    generation_changed,",
        "    heartbeat,",
        "    last_prompt,",
        "    last_turn,",
        "    persist_session_summary,",
        "    save_prompt,",
        "    unbind_current_session,",
        ")",
        "from .usage_ops import load_artifact, record_activity, record_mcp_usage, record_usage",
        "",
    ]
    facade_lines.append("\n\n".join(api_remainder))
    facade_lines.append("")
    API.write_text("\n".join(facade_lines), encoding="utf-8")
    print("split complete")


if __name__ == "__main__":
    main()
