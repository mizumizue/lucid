from __future__ import annotations

import os
import time
from typing import Any

INDEXED_SOFT_LIMIT = 2000
_last_search_ms: float | None = None

from lucid_memories.storage import blobs
from lucid_memories.runtime import embedding
from . import memory, retrieval
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.util import new_id, row_dict, truncate, workspace_matches
from . import api_common


def _active_indexed_count(conn, model: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM embeddings e
        JOIN knowledge k ON k.id = e.entity_id
        WHERE e.entity_type = 'knowledge'
          AND e.model = ?
          AND k.memory_status NOT IN ('archived', 'faded')
          AND (k.expires_at IS NULL OR k.expires_at >= ?)
        """,
        (model, now_iso()),
    ).fetchone()
    return int(row["count"] if row else 0)


def _embedding_text(entity_type: str, entity_id: str) -> str | None:
    conn = api_common._conn()
    try:
        if entity_type == "knowledge":
            row = conn.execute(
                "SELECT title, body, blob_sha, tags_json FROM knowledge WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                return None
            body = row["body"] or ""
            if row["blob_sha"]:
                body = blobs.get_blob_text(row["blob_sha"]) or body
            return f"{row['title']}\n{body}\n{row['tags_json'] or ''}".strip()
        if entity_type == "prompt":
            row = conn.execute(
                "SELECT last_prompt FROM turn_state WHERE conversation_id = ?",
                (entity_id,),
            ).fetchone()
            return row["last_prompt"] if row else None
        return None
    finally:
        conn.close()

def index_embedding(
    entity_type: str,
    entity_id: str,
    *,
    text: str | None = None,
) -> dict[str, Any]:
    """Embed one entity and persist its float32 vector without holding a DB lock."""
    if entity_type not in ("knowledge", "prompt"):
        return {"ok": False, "error": "invalid_entity_type", "entity_type": entity_type}
    source = text if text is not None else _embedding_text(entity_type, entity_id)
    if not source or not source.strip():
        return {"ok": False, "error": "empty_text"}
    try:
        vector = embedding.embed(source)
    except embedding.EmbeddingError as exc:
        return {
            "ok": False,
            "error": "embedding_unavailable",
            "message": str(exc),
            "model": embedding.model(),
        }
    now = now_iso()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO embeddings(
                  id, entity_type, entity_id, model, dimensions, vector,
                  text_hash, text_preview, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id, model) DO UPDATE SET
                  dimensions = excluded.dimensions,
                  vector = excluded.vector,
                  text_hash = excluded.text_hash,
                  text_preview = excluded.text_preview,
                  updated_at = excluded.updated_at
                """,
                (
                    new_id(),
                    entity_type,
                    entity_id,
                    vector.model,
                    vector.dimensions,
                    embedding.pack(vector.values),
                    embedding.text_hash(source),
                    truncate(source, 240),
                    now,
                    now,
                ),
            )
        return {
            "ok": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "model": vector.model,
            "dimensions": vector.dimensions,
        }
    finally:
        conn.close()

def semantic_search(
    query: str,
    *,
    workspace: str | None = None,
    kind: str | None = None,
    limit: int = 8,
    min_score: float = 0.20,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    """Brute-force cosine search over vectors stored in SQLite.

    The first implementation intentionally avoids a native SQLite vector
    extension: it keeps the database portable on Windows and lets the model
    provider change without changing the storage contract.
    """
    text = (query or "").strip()
    if not text:
        return {"ok": False, "error": "query required"}
    workspace = workspace or os.getcwd()
    tracking_id = request_id
    if track and tracking_id is None:
        tracking_id = retrieval.start_request(
            "semantic_search",
            query=text,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
            metadata={"min_score": min_score, "kind": kind},
        )
    try:
        needle = embedding.embed(text)
    except embedding.EmbeddingError as exc:
        if tracking_id:
            retrieval.fail_request(tracking_id)
        return {
            "ok": False,
            "error": "embedding_unavailable",
            "message": str(exc),
            "results": [],
            "retrieval_request_id": tracking_id,
        }
    scan_started = time.perf_counter()
    conn = api_common._conn()
    try:
        rows = conn.execute(
            """
            SELECT e.entity_id, e.model, e.dimensions, e.vector,
                   k.*
            FROM embeddings e
            JOIN knowledge k ON k.id = e.entity_id
            WHERE e.entity_type = 'knowledge'
              AND e.model = ?
              AND e.dimensions = ?
              AND k.memory_status NOT IN ('archived', 'faded')
              AND (k.expires_at IS NULL OR k.expires_at >= ?)
            """,
            (needle.model, needle.dimensions, now_iso()),
        ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            if kind and row["kind"] != kind:
                continue
            if row["scope"] == "workspace" and not workspace_matches(
                [row["workspace_root"] or ""], workspace
            ):
                continue
            try:
                score = embedding.cosine(
                    needle.values,
                    embedding.unpack(row["vector"], row["dimensions"]),
                )
            except (TypeError, ValueError, embedding.EmbeddingError):
                continue
            if score < min_score:
                continue
            item = row_dict(row)
            item.pop("vector", None)
            item["score"] = round(score, 6)
            item["memory_score"] = memory.score(row)
            item["semantic"] = True
            if not item.get("body") and item.get("blob_sha"):
                item["body"] = truncate(blobs.get_blob_text(item["blob_sha"]), 400)
            scored.append(item)
        scored.sort(
            key=lambda item: (item["score"] * (0.5 + item["memory_score"]), item["score"]),
            reverse=True,
        )
        for rank, item in enumerate(scored, start=1):
            item["rank"] = rank
            item["selected"] = rank <= max(1, limit)
        if tracking_id:
            retrieval.record_results(
                tracking_id,
                retrieval.normalize_results(scored, source_db="sqlite"),
            )
            retrieval.finish_request(tracking_id)
        memory.touch(conn, [item["id"] for item in scored[: max(1, limit)]])
        global _last_search_ms
        _last_search_ms = round((time.perf_counter() - scan_started) * 1000, 2)
        return {
            "ok": True,
            "query": text,
            "model": needle.model,
            "dimensions": needle.dimensions,
            "knowledge": scored[: max(1, limit)],
            "retrieval_request_id": tracking_id,
        }
    except Exception:
        if tracking_id:
            retrieval.fail_request(tracking_id)
        raise
    finally:
        conn.close()

def embedding_status() -> dict[str, Any]:
    conn = api_common._conn()
    try:
        row = conn.execute(
            """
            SELECT model, dimensions, COUNT(*) AS count, MAX(updated_at) AS updated_at
            FROM embeddings
            GROUP BY model, dimensions
            ORDER BY updated_at DESC
            """
        ).fetchall()
        stored = [row_dict(item) for item in row]
        configured_model = embedding.model()
        indexed_count = _active_indexed_count(conn, configured_model)
        warning = None
        if indexed_count > INDEXED_SOFT_LIMIT:
            warning = (
                f"indexed active knowledge ({indexed_count}) exceeds soft limit "
                f"({INDEXED_SOFT_LIMIT}); consider archive/prune or a future vector index"
            )
        provider_status = embedding.status()
        if provider_status.get("dimensions") is None and stored:
            provider_status["dimensions"] = stored[0]["dimensions"]
        return {
            "ok": True,
            "provider": embedding.provider(),
            "configured_model": configured_model,
            "provider_status": provider_status,
            "stored": stored,
            "indexed_count": indexed_count,
            "last_search_ms": _last_search_ms,
            "soft_limit": INDEXED_SOFT_LIMIT,
            "warning": warning,
        }
    finally:
        conn.close()

def backfill_embeddings(
    *,
    workspace: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    conn = api_common._conn()
    try:
        rows = conn.execute(
            """
            SELECT k.id
            FROM knowledge k
            LEFT JOIN embeddings e
              ON e.entity_type = 'knowledge'
             AND e.entity_id = k.id
             AND e.model = ?
            WHERE e.id IS NULL
              AND (k.expires_at IS NULL OR k.expires_at >= ?)
              AND (? IS NULL OR k.kind = ?)
            ORDER BY k.updated_at DESC
            LIMIT ?
            """,
            (embedding.model(), now_iso(), kind, kind, limit),
        ).fetchall()
        ids = [row["id"] for row in rows]
    finally:
        conn.close()
    indexed = []
    failed = []
    for entity_id in ids:
        result = index_embedding("knowledge", entity_id)
        (indexed if result.get("ok") else failed).append(
            {"id": entity_id, **result}
        )
    return {
        "ok": True,
        "model": embedding.model(),
        "indexed": indexed,
        "failed": failed,
        "count": len(ids),
    }
