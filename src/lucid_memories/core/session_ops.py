from __future__ import annotations

from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import SUMMARY_LIMIT, env
from lucid_memories.runtime.util import dumps, normalize_root, parse_roots, row_dict
from .session_metadata import compute_session_summary
from . import api_common
from .embedding_ops import index_embedding

PENDING_PROMPT_ID = "_pending"

def ensure_session(
    conversation_id: str,
    *,
    parent_conversation_id: str | None = None,
    workspace_roots: list[str] | None = None,
    composer_mode: str | None = None,
    is_background: bool | None = None,
    model: str | None = None,
    transcript_path: str | None = None,
    title: str | None = None,
    status: str | None = None,
    generation_id: str | None = None,
    ended_reason: str | None = None,
) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id required"}
    roots = [normalize_root(r) for r in (workspace_roots or []) if r]
    now = now_iso()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute(
                "SELECT * FROM sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO sessions(
                      conversation_id, parent_conversation_id, workspace_roots_json,
                      composer_mode, is_background, model, transcript_path, title,
                      status, last_generation_id, last_heartbeat_at, ended_reason,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        parent_conversation_id,
                        dumps(roots),
                        composer_mode,
                        1 if is_background else 0,
                        model,
                        transcript_path,
                        title,
                        status or "active",
                        generation_id,
                        now,
                        ended_reason,
                        now,
                        now,
                    ),
                )
            else:
                new_roots = roots or parse_roots(row["workspace_roots_json"])
                conn.execute(
                    """
                    UPDATE sessions SET
                      parent_conversation_id = COALESCE(?, parent_conversation_id),
                      workspace_roots_json = ?,
                      composer_mode = COALESCE(?, composer_mode),
                      is_background = COALESCE(?, is_background),
                      model = COALESCE(?, model),
                      transcript_path = COALESCE(?, transcript_path),
                      title = COALESCE(?, title),
                      status = COALESCE(?, status),
                      last_generation_id = COALESCE(?, last_generation_id),
                      last_heartbeat_at = ?,
                      ended_reason = COALESCE(?, ended_reason),
                      updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (
                        parent_conversation_id,
                        dumps(new_roots),
                        composer_mode,
                        None if is_background is None else (1 if is_background else 0),
                        model,
                        transcript_path,
                        title,
                        status,
                        generation_id,
                        now,
                        ended_reason,
                        now,
                        conversation_id,
                    ),
                )
        row = conn.execute(
            "SELECT * FROM sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return {"ok": True, "session": row_dict(row)}
    finally:
        conn.close()

def persist_session_summary(conversation_id: str) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "saved": False, "error": "conversation_id required"}
    conn = api_common._conn()
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "summary" not in columns:
            return {"ok": False, "saved": False, "error": "summary column unavailable"}
        last_prompt = None
        turn_state = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='turn_state'"
        ).fetchone()
        if turn_state:
            row = conn.execute(
                "SELECT last_prompt FROM turn_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row:
                last_prompt = row["last_prompt"]
        has_events = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_events'"
        ).fetchone() is not None
        has_jobs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone() is not None
        summary = compute_session_summary(
            conn,
            conversation_id,
            has_events=has_events,
            has_jobs=has_jobs,
            last_prompt=last_prompt,
            limit=SUMMARY_LIMIT,
        )
        if not summary:
            return {"ok": True, "saved": False, "summary": None}
        now = now_iso()
        with write_tx(conn):
            conn.execute(
                """
                UPDATE sessions
                SET summary = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (summary, now, conversation_id),
            )
        return {"ok": True, "saved": True, "summary": summary}
    finally:
        conn.close()

def heartbeat(
    conversation_id: str,
    *,
    generation_id: str | None = None,
    status: str | None = "active",
    workspace_roots: list[str] | None = None,
    model: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    return ensure_session(
        conversation_id,
        generation_id=generation_id,
        status=status,
        workspace_roots=workspace_roots,
        model=model,
        transcript_path=transcript_path,
    )

def bind_current_session(
    conversation_id: str,
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Bridge hook identity to the long-lived MCP process."""
    if not conversation_id:
        return {"ok": False, "error": "conversation_id required"}
    root = normalize_root(workspace) if workspace else None
    now = now_iso()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            session = conn.execute(
                "SELECT conversation_id FROM sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if session is None:
                return {"ok": False, "error": "session not found"}
            conn.execute(
                """
                INSERT INTO session_bindings(
                  binding_key, conversation_id, workspace_root, bound_at
                ) VALUES ('mcp-current', ?, ?, ?)
                ON CONFLICT(binding_key) DO UPDATE SET
                  conversation_id = excluded.conversation_id,
                  workspace_root = excluded.workspace_root,
                  bound_at = excluded.bound_at
                """,
                (conversation_id, root, now),
            )
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "workspace": root,
            "bound_at": now,
        }
    finally:
        conn.close()

def unbind_current_session(conversation_id: str) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id required"}
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                DELETE FROM session_bindings
                WHERE binding_key = 'mcp-current' AND conversation_id = ?
                """,
                (conversation_id,),
            )
        return {"ok": True, "conversation_id": conversation_id}
    finally:
        conn.close()

def save_prompt(
    prompt: str,
    *,
    conversation_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt required"}
    cid = conversation_id or PENDING_PROMPT_ID
    now = now_iso()
    ws = normalize_root(workspace) if workspace else None
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO turn_state(conversation_id, last_prompt, last_prompt_at, workspace_root)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                  last_prompt = excluded.last_prompt,
                  last_prompt_at = excluded.last_prompt_at,
                  workspace_root = COALESCE(excluded.workspace_root, turn_state.workspace_root)
                """,
                (cid, text, now, ws),
            )
        result = {"ok": True, "conversation_id": cid}
        if (env("EMBED_PROMPTS", "") or "").lower() in {"1", "true", "yes"}:
            result["embedding"] = index_embedding("prompt", cid, text=text)
        return result
    finally:
        conn.close()

def bind_pending_prompt(conversation_id: str) -> None:
    if not conversation_id or conversation_id == PENDING_PROMPT_ID:
        return
    conn = api_common._conn()
    try:
        with write_tx(conn):
            current = conn.execute(
                "SELECT last_prompt FROM turn_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if current and current["last_prompt"]:
                conn.execute(
                    "DELETE FROM turn_state WHERE conversation_id = ?",
                    (PENDING_PROMPT_ID,),
                )
                return
            pending = conn.execute(
                "SELECT last_prompt, last_prompt_at, workspace_root FROM turn_state WHERE conversation_id = ?",
                (PENDING_PROMPT_ID,),
            ).fetchone()
            if not pending or not pending["last_prompt"]:
                return
            conn.execute(
                """
                INSERT INTO turn_state(conversation_id, last_prompt, last_prompt_at, workspace_root)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                  last_prompt = excluded.last_prompt,
                  last_prompt_at = excluded.last_prompt_at,
                  workspace_root = COALESCE(excluded.workspace_root, turn_state.workspace_root)
                """,
                (
                    conversation_id,
                    pending["last_prompt"],
                    pending["last_prompt_at"] or now_iso(),
                    pending["workspace_root"],
                ),
            )
            conn.execute(
                "DELETE FROM turn_state WHERE conversation_id = ?",
                (PENDING_PROMPT_ID,),
            )
    finally:
        conn.close()

def last_prompt(conversation_id: str | None) -> str | None:
    row = last_turn(conversation_id)
    return row.get("last_prompt") if row else None

def last_turn(conversation_id: str | None) -> dict[str, Any] | None:
    conn = api_common._conn()
    try:
        if conversation_id:
            row = conn.execute(
                "SELECT * FROM turn_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row and row["last_prompt"]:
                return row_dict(row)
        row = conn.execute(
            "SELECT * FROM turn_state WHERE conversation_id = ?",
            (PENDING_PROMPT_ID,),
        ).fetchone()
        return row_dict(row) if row else None
    finally:
        conn.close()

def generation_changed(conversation_id: str, generation_id: str | None) -> bool:
    if not generation_id:
        return False
    conn = api_common._conn()
    try:
        row = conn.execute(
            "SELECT last_generation_id FROM sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return True
        return row["last_generation_id"] != generation_id
    finally:
        conn.close()
