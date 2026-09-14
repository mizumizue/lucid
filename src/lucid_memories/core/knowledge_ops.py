from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from lucid_memories.storage import blobs
from . import memory, retrieval
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import INLINE_BODY_LIMIT
from lucid_memories.runtime.util import (
    dumps,
    estimate_tokens,
    fts_match_arg,
    looks_like_id,
    new_id,
    normalize_root,
    row_dict,
    truncate,
    workspace_matches,
)
from . import api_common
from .api_common import KNOWLEDGE_KINDS, KNOWLEDGE_SCOPES
from .embedding_ops import index_embedding
from .identity_ops import _env_conversation_id, whoami

def search(
    query: str,
    workspace: str | None = None,
    limit: int = 20,
    kind: str | None = None,
    *,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    if not query or not query.strip():
        return {"ok": False, "error": "query required"}
    if kind and kind not in KNOWLEDGE_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    tracking_id = request_id
    if track and tracking_id is None:
        tracking_id = retrieval.start_request(
            "search",
            query=query,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
        )
    conn = api_common._conn()
    try:
        hits: list[dict[str, Any]] = []
        match = fts_match_arg(query)
        rows: list[sqlite3.Row] = []
        if match:
            try:
                rows = conn.execute(
                    """
                    SELECT k.* FROM knowledge_fts f
                    JOIN knowledge k ON k.id = f.knowledge_id
                    WHERE f MATCH ?
                      AND k.memory_status NOT IN ('archived', 'faded')
                    LIMIT ?
                    """,
                    (match, limit * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = f"%{query.strip()}%"
            rows = conn.execute(
                """
                SELECT * FROM knowledge
                WHERE memory_status NOT IN ('archived', 'faded')
                  AND (title LIKE ? OR IFNULL(body,'') LIKE ? OR tags_json LIKE ?)
                LIMIT ?
                """,
                (like, like, like, limit * 3),
            ).fetchall()
        now = now_iso()
        for row in rows:
            if row["expires_at"] and row["expires_at"] <= now:
                continue
            if kind and row["kind"] != kind:
                continue
            scope = row["scope"]
            if scope == "workspace" and not workspace_matches(
                [row["workspace_root"] or ""], workspace
            ):
                continue
            item = row_dict(row)
            if not item.get("body") and item.get("blob_sha"):
                item["body"] = truncate(blobs.get_blob_text(item["blob_sha"]), 400)
            item["memory_score"] = memory.score(row)
            hits.append(item)
            if len(hits) >= limit:
                break
        hits.sort(key=lambda item: item.get("memory_score", 0.0), reverse=True)
        for rank, item in enumerate(hits, start=1):
            item["rank"] = rank
        memory.touch(conn, [item["id"] for item in hits])
        packs = conn.execute(
            """
            SELECT * FROM packs
            WHERE title LIKE ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (f"%{query.strip()}%", limit),
        ).fetchall()
        pack_hits = [
            row_dict(p)
            for p in packs
            if not p["workspace_root"]
            or workspace_matches([p["workspace_root"]], workspace)
        ]
        if tracking_id:
            result_items = [
                {**item, "rank": index + 1}
                for index, item in enumerate(hits)
            ]
            result_items.extend(
                {
                    **item,
                    "source_db": "sqlite",
                    "entity_type": "pack",
                    "rank": len(result_items) + index + 1,
                }
                for index, item in enumerate(pack_hits)
            )
            retrieval.record_results(
                tracking_id,
                retrieval.normalize_results(result_items, source_db="sqlite"),
            )
            retrieval.finish_request(tracking_id)
        return {
            "ok": True,
            "knowledge": hits,
            "packs": pack_hits,
            "query": query,
            "kind": kind,
            "retrieval_request_id": tracking_id,
        }
    except Exception:
        if tracking_id:
            retrieval.fail_request(tracking_id)
        raise
    finally:
        conn.close()

def _knowledge_text(row: sqlite3.Row | dict[str, Any]) -> str:
    body = row["body"] if not isinstance(row, dict) else row.get("body")
    sha = row["blob_sha"] if not isinstance(row, dict) else row.get("blob_sha")
    if body:
        return body
    if sha:
        return blobs.get_blob_text(sha) or ""
    return ""

def list_knowledge(
    kind: str | None = None,
    workspace: str | None = None,
    limit: int = 50,
    any_workspace: bool = False,
) -> dict[str, Any]:
    if not any_workspace:
        workspace = workspace or os.getcwd()
    if kind and kind not in KNOWLEDGE_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    conn = api_common._conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM knowledge
            WHERE memory_status NOT IN ('archived', 'faded')
              AND (? IS NULL OR kind = ?)
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (kind, kind, limit * 3),
        ).fetchall()
        now = now_iso()
        items: list[dict[str, Any]] = []
        for row in rows:
            if row["expires_at"] and row["expires_at"] <= now:
                continue
            scope = row["scope"]
            if scope == "session" and not any_workspace:
                continue
            if (
                not any_workspace
                and scope == "workspace"
                and not workspace_matches([row["workspace_root"] or ""], workspace)
            ):
                continue
            item = row_dict(row)
            text = _knowledge_text(row)
            item["body"] = truncate(text, 240)
            items.append(item)
            if len(items) >= limit:
                break
        return {"ok": True, "knowledge": items, "kind": kind, "count": len(items)}
    finally:
        conn.close()

def remember(
    title: str | None = None,
    body: str | None = None,
    *,
    kind: str | None = None,
    scope: str = "workspace",
    workspace: str | None = None,
    conversation_id: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    salience: float | None = None,
    decay_half_life_days: float | None = None,
    expires_at: str | None = None,
    source_event_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    source: str = "cli",
    knowledge_id: str | None = None,
    rev: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if kind is not None and kind not in KNOWLEDGE_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    if scope not in KNOWLEDGE_SCOPES:
        return {"ok": False, "error": "invalid_scope", "scope": scope}
    if knowledge_id:
        return _remember_update(
            knowledge_id,
            rev,
            title=title,
            body=body,
            kind=kind,
            tags=tags,
            confidence=confidence,
            importance=importance,
            salience=salience,
            decay_half_life_days=decay_half_life_days,
            expires_at=expires_at,
            source_event_id=source_event_id,
            provenance=provenance,
            source=source,
        )
    kind = kind or "fact"
    if kind not in KNOWLEDGE_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    if not title:
        return {"ok": False, "error": "title required"}
    workspace = workspace or os.getcwd()
    conv = conversation_id or _env_conversation_id()
    if scope == "session" and not conv:
        ident = whoami(workspace=workspace)
        conv = ident.get("conversation_id")
        if not conv:
            return {"ok": False, "error": "conversation_id required for session scope"}
    kid = new_id()
    now = now_iso()
    created = created_at or now
    body = body or ""
    lifecycle = memory.lifecycle_defaults(
        kind,
        confidence=confidence,
        importance=importance,
        salience=salience,
        decay_half_life_days=decay_half_life_days,
    )
    blob_sha = None
    stored_body = body
    if len(body) > INLINE_BODY_LIMIT:
        stored_body = truncate(body, INLINE_BODY_LIMIT)
    conn = api_common._conn()
    try:
        with write_tx(conn):
            if len(body) > INLINE_BODY_LIMIT:
                blob_sha = blobs.put_blob(conn, body.encode("utf-8"))
            ws_root = normalize_root(workspace) if scope != "global" else None
            conn.execute(
                """
                INSERT INTO knowledge(
                  id, kind, scope, workspace_root, source_conversation_id,
                  title, body, blob_sha, tags_json, confidence, expires_at,
                  importance, salience, decay_half_life_days, access_count,
                  last_accessed_at, source_event_id, provenance_json, memory_status,
                  rev, created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, 'active', 1, ?, ?, ?)
                """,
                (
                    kid,
                    kind,
                    scope,
                    ws_root,
                    conv,
                    title,
                    stored_body,
                    blob_sha,
                    dumps(tags or []),
                    lifecycle["confidence"],
                    expires_at,
                    lifecycle["importance"],
                    lifecycle["salience"],
                    lifecycle["decay_half_life_days"],
                    source_event_id,
                    dumps(provenance or {"source": source}),
                    created,
                    now,
                    conv,
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_fts(title, body, tags, knowledge_id) VALUES (?, ?, ?, ?)",
                (title, body, " ".join(tags or []), kid),
            )
        indexed = index_embedding(
            "knowledge",
            kid,
            text=f"{title}\n{body}\n{' '.join(tags or [])}",
        )
        return {
            "ok": True,
            "id": kid,
            "blob_sha": blob_sha,
            "source": source,
            "embedding": indexed,
        }
    finally:
        conn.close()

def _sync_knowledge_fts(
    conn: sqlite3.Connection,
    knowledge_id: str,
    title: str,
    body: str,
    tags: list[str],
) -> None:
    conn.execute("DELETE FROM knowledge_fts WHERE knowledge_id = ?", (knowledge_id,))
    conn.execute(
        "INSERT INTO knowledge_fts(title, body, tags, knowledge_id) VALUES (?, ?, ?, ?)",
        (title, body, " ".join(tags), knowledge_id),
    )

def _remember_update(
    knowledge_id: str,
    rev: int | None,
    *,
    title: str | None,
    body: str | None,
    kind: str | None,
    tags: list[str] | None,
    confidence: float | None,
    importance: float | None,
    salience: float | None,
    decay_half_life_days: float | None,
    expires_at: str | None,
    source_event_id: str | None,
    provenance: dict[str, Any] | None,
    source: str,
) -> dict[str, Any]:
    if rev is None:
        return {"ok": False, "error": "rev required"}
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute(
                "SELECT * FROM knowledge WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "error": "knowledge_not_found", "id": knowledge_id}
            if row["rev"] != rev:
                return {"ok": False, "error": "rev_conflict", "current_rev": row["rev"]}
            new_kind = kind if kind else row["kind"]
            new_title = title if title else row["title"]
            new_body = body if body is not None else _knowledge_text(row)
            blob_sha = row["blob_sha"]
            stored_body = new_body
            if len(new_body) > INLINE_BODY_LIMIT:
                stored_body = truncate(new_body, INLINE_BODY_LIMIT)
                blob_sha = blobs.put_blob(conn, new_body.encode("utf-8"))
            elif body is not None:
                blob_sha = None
            new_tags = tags if tags is not None else json.loads(row["tags_json"] or "[]")
            conn.execute(
                """
                UPDATE knowledge SET
                  kind = ?, title = ?, body = ?, blob_sha = ?, tags_json = ?,
                  confidence = COALESCE(?, confidence), expires_at = COALESCE(?, expires_at),
                  importance = COALESCE(?, importance),
                  salience = COALESCE(?, salience),
                  decay_half_life_days = COALESCE(?, decay_half_life_days),
                  source_event_id = COALESCE(?, source_event_id),
                  provenance_json = COALESCE(?, provenance_json),
                  memory_status = 'active',
                  rev = rev + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_kind,
                    new_title,
                    stored_body,
                    blob_sha,
                    dumps(new_tags),
                    confidence,
                    expires_at,
                    importance,
                    salience,
                    decay_half_life_days,
                    source_event_id,
                    dumps(provenance) if provenance is not None else None,
                    now_iso(),
                    knowledge_id,
                ),
            )
            _sync_knowledge_fts(conn, knowledge_id, new_title, new_body, new_tags)
        out = conn.execute("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,)).fetchone()
        indexed = index_embedding(
            "knowledge",
            knowledge_id,
            text=f"{new_title}\n{new_body}\n{' '.join(new_tags)}",
        )
        return {
            "ok": True,
            "id": knowledge_id,
            "knowledge": row_dict(out),
            "source": source,
            "embedding": indexed,
        }
    finally:
        conn.close()

def archive(knowledge_id: str) -> dict[str, Any]:
    if not knowledge_id:
        return {"ok": False, "error": "id required"}
    now = now_iso()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "knowledge_not_found", "id": knowledge_id}
            conn.execute(
                """
                UPDATE knowledge
                SET expires_at = ?, memory_status = 'archived',
                    updated_at = ?, rev = rev + 1
                WHERE id = ?
                """,
                (now, now, knowledge_id),
            )
        return {"ok": True, "id": knowledge_id, "archived_at": now}
    finally:
        conn.close()
