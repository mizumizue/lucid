"""Normalized retrieval request/result tracking."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable

from lucid_memories.storage.db import connect, now_iso, write_tx
from lucid_memories.runtime.util import dumps, new_id, normalize_root, workspace_matches


def resolve_conversation_id(
    conversation_id: str | None = None,
    *,
    workspace: str | None = None,
) -> str | None:
    """Resolve explicit, hook-injected, or currently bound session identity."""
    if conversation_id:
        return str(conversation_id)
    value = os.environ.get("LUCID_MEMORIES_CONVERSATION_ID")
    if value:
        return value
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT conversation_id, workspace_root
            FROM session_bindings
            WHERE binding_key = 'mcp-current'
            ORDER BY bound_at DESC
            """
        ).fetchall()
        for row in rows:
            if not workspace or workspace_matches([row["workspace_root"] or ""], workspace):
                return row["conversation_id"]
        return None
    finally:
        conn.close()


def start_request(
    method: str,
    *,
    query: str | None = None,
    target: str | None = None,
    workspace: str | None = None,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    parent_request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
    at: str | None = None,
) -> str:
    request_id = request_id or new_id()
    timestamp = at or now_iso()
    resolved_conversation_id = resolve_conversation_id(
        conversation_id,
        workspace=workspace,
    )
    conn = connect()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT OR IGNORE INTO retrieval_requests(
                  id, conversation_id, generation_id, parent_request_id, method,
                  query, target, workspace_root, source, status, started_at,
                  metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    request_id,
                    resolved_conversation_id,
                    generation_id,
                    parent_request_id,
                    method,
                    query,
                    target,
                    normalize_root(workspace) if workspace else None,
                    source,
                    timestamp,
                    dumps(metadata or {}),
                ),
            )
        return request_id
    finally:
        conn.close()


def _result_value(item: dict[str, Any], key: str, default: Any = None) -> Any:
    value = item.get(key)
    return default if value is None else value


def normalize_results(
    results: Iterable[dict[str, Any]],
    *,
    source_db: str | None = None,
    entity_type: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ordinal, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        normalized.append(
            {
                "ordinal": int(item.get("ordinal", ordinal)),
                "source_db": str(item.get("source_db") or item.get("db") or source_db or "unknown"),
                "entity_type": str(
                    item.get("entity_type") or item.get("kind") or entity_type or "unknown"
                ),
                "entity_id": item.get("entity_id") or item.get("id"),
                "title": item.get("title"),
                "rank": item.get("rank", ordinal + 1),
                "score": item.get("score"),
                "memory_score": item.get("memory_score"),
                "selected": 1 if item.get("selected", True) else 0,
                "metadata": metadata,
            }
        )
    return normalized


def record_results(
    request_id: str,
    results: Iterable[dict[str, Any]],
    *,
    at: str | None = None,
) -> int:
    normalized = normalize_results(results)
    timestamp = at or now_iso()
    conn = connect()
    try:
        with write_tx(conn):
            for item in normalized:
                ordinal = int(item["ordinal"])
                conn.execute(
                    """
                    INSERT INTO retrieval_results(
                      id, request_id, ordinal, source_db, entity_type, entity_id,
                      title, rank, score, memory_score, selected, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id, ordinal) DO UPDATE SET
                      source_db = excluded.source_db,
                      entity_type = excluded.entity_type,
                      entity_id = excluded.entity_id,
                      title = excluded.title,
                      rank = excluded.rank,
                      score = excluded.score,
                      memory_score = excluded.memory_score,
                      selected = excluded.selected,
                      metadata_json = excluded.metadata_json
                    """,
                    (
                        f"{request_id}:{ordinal}",
                        request_id,
                        ordinal,
                        item["source_db"],
                        item["entity_type"],
                        item["entity_id"],
                        item["title"],
                        item["rank"],
                        item["score"],
                        item["memory_score"],
                        item["selected"],
                        dumps(item["metadata"]),
                        timestamp,
                    ),
                )
            count = conn.execute(
                "SELECT COUNT(*) FROM retrieval_results WHERE request_id = ?",
                (request_id,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE retrieval_requests SET result_count = ? WHERE id = ?",
                (count, request_id),
            )
        return int(count)
    finally:
        conn.close()


def finish_request(
    request_id: str,
    *,
    status: str = "completed",
    at: str | None = None,
) -> dict[str, Any]:
    timestamp = at or now_iso()
    conn = connect()
    try:
        with write_tx(conn):
            conn.execute(
                """
                UPDATE retrieval_requests
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (status, timestamp, request_id),
            )
            row = conn.execute(
                "SELECT result_count, status FROM retrieval_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return {"ok": False, "error": "retrieval_request_not_found", "id": request_id}
        return {
            "ok": True,
            "id": request_id,
            "status": row["status"],
            "result_count": row["result_count"],
        }
    finally:
        conn.close()


def fail_request(request_id: str, *, at: str | None = None) -> dict[str, Any]:
    return finish_request(request_id, status="error", at=at)


def now_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
