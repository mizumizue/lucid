from __future__ import annotations

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
from . import api_common

def _env_conversation_id() -> str | None:
    return env("CONVERSATION_ID") or None

def _bound_current_session(
    conn: sqlite3.Connection,
    *,
    workspace: str,
) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT s.*, b.workspace_root AS binding_workspace_root, b.bound_at
        FROM session_bindings b
        JOIN sessions s ON s.conversation_id = b.conversation_id
        WHERE b.binding_key = 'mcp-current'
        """,
    ).fetchone()
    if row is None or row["status"] == "ended":
        return None
    if is_stale_heartbeat(row["bound_at"]) or is_stale_heartbeat(
        row["last_heartbeat_at"]
    ):
        return None
    binding_root = row["binding_workspace_root"]
    if binding_root and not workspace_matches([binding_root], workspace):
        return None
    return row

def whoami(
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    explicit = conversation_id or _env_conversation_id()
    workspace = workspace or os.getcwd()
    conn = api_common._conn()
    try:
        if explicit:
            row = conn.execute(
                "SELECT * FROM sessions WHERE conversation_id = ?",
                (explicit,),
            ).fetchone()
            return {
                "ok": True,
                "conversation_id": explicit,
                "ambiguous": False,
                "session": row_dict(row) if row else None,
                "candidates": [],
            }
        bound = _bound_current_session(conn, workspace=workspace)
        if bound is not None:
            session = {
                key: bound[key]
                for key in bound.keys()
                if key not in {"binding_workspace_root", "bound_at"}
            }
            return {
                "ok": True,
                "conversation_id": session["conversation_id"],
                "ambiguous": False,
                "session": session,
                "candidates": [],
                "workspace": normalize_root(workspace),
                "source": "hook_binding",
            }
        rows = conn.execute(
            "SELECT * FROM sessions WHERE status != 'ended'"
        ).fetchall()
        matched = [
            r
            for r in rows
            if workspace_contains(parse_roots(r["workspace_roots_json"]), workspace)
            and not is_stale_heartbeat(r["last_heartbeat_at"])
        ]
        matched.sort(key=lambda r: r["last_heartbeat_at"] or "", reverse=True)
        candidates = [
            {
                "conversation_id": r["conversation_id"],
                "status": r["status"],
                "last_heartbeat_at": r["last_heartbeat_at"],
                "title": r["title"],
            }
            for r in matched[:10]
        ]
        chosen = matched[0]["conversation_id"] if len(matched) == 1 else None
        return {
            "ok": True,
            "conversation_id": chosen,
            "ambiguous": len(matched) > 1,
            "session": row_dict(matched[0]) if len(matched) == 1 else None,
            "candidates": candidates,
            "workspace": normalize_root(workspace),
        }
    finally:
        conn.close()

def _effective_job_status(job: sqlite3.Row, session: sqlite3.Row | None) -> str:
    status = job["status"]
    if status not in ("running", "pending", "blocked"):
        return status
    hb = session["last_heartbeat_at"] if session is not None else None
    if is_stale_heartbeat(hb):
        return "stale"
    return status

def status(
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    self_info = whoami(workspace=workspace, conversation_id=conversation_id)
    self_id = self_info.get("conversation_id")
    conn = api_common._conn()
    try:
        sessions = conn.execute(
            "SELECT * FROM sessions ORDER BY last_heartbeat_at DESC LIMIT 40"
        ).fetchall()
        session_rows = [
            r
            for r in sessions
            if r["conversation_id"] == self_id
            or workspace_contains(parse_roots(r["workspace_roots_json"]), workspace)
        ][:20]
        session_by_id = {r["conversation_id"]: r for r in session_rows}
        jobs = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('pending','running','blocked')
            ORDER BY updated_at DESC LIMIT 80
            """
        ).fetchall()
        job_out = []
        for job in jobs:
            sess = session_by_id.get(job["conversation_id"])
            if sess is None:
                sess = conn.execute(
                    "SELECT * FROM sessions WHERE conversation_id = ?",
                    (job["conversation_id"],),
                ).fetchone()
                if sess is None or (
                    sess["conversation_id"] != self_id
                    and not workspace_contains(
                        parse_roots(sess["workspace_roots_json"]), workspace
                    )
                ):
                    continue
            item = row_dict(job)
            item["effective_status"] = _effective_job_status(job, sess)
            job_out.append(item)
            if len(job_out) >= 30:
                break
        notices = []
        if self_id:
            n_rows = conn.execute(
                """
                SELECT * FROM notices
                WHERE read_at IS NULL AND (to_conversation_id = ? OR to_conversation_id IS NULL)
                ORDER BY created_at DESC LIMIT 20
                """,
                (self_id,),
            ).fetchall()
            notices = [row_dict(n) for n in n_rows]
        reload_pack = None
        if self_id:
            reload_pack = conn.execute(
                """
                SELECT id, kind, title, token_estimate, created_at
                FROM packs
                WHERE conversation_id = ? AND kind = 'compact_snapshot'
                ORDER BY created_at DESC LIMIT 1
                """,
                (self_id,),
            ).fetchone()
        checkpoint_passive(conn)
        return {
            "ok": True,
            "self": self_info,
            "sessions": [row_dict(s) for s in session_rows],
            "jobs": job_out,
            "notices_unread": notices,
            "reload_available": reload_pack is not None,
            "reload_pack": row_dict(reload_pack) if reload_pack else None,
            "workspace": normalize_root(workspace),
        }
    finally:
        conn.close()
