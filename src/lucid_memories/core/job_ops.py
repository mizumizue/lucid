from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import LEASE_TTL_SECONDS
from lucid_memories.runtime.util import new_id, row_dict, truncate
from . import api_common
from .api_common import JOB_KINDS, JOB_STATUSES
from .identity_ops import _env_conversation_id, whoami
from .session_ops import ensure_session

def _append_job_event(
    conn: sqlite3.Connection,
    job_id: str,
    from_status: str | None,
    to_status: str | None,
    source: str,
    detail: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO job_events(id, job_id, at, from_status, to_status, source, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id(), job_id, now_iso(), from_status, to_status, source, truncate(detail, 1000)),
    )

def job_start(
    title: str,
    *,
    kind: str = "declared",
    conversation_id: str | None = None,
    workspace: str | None = None,
    summary: str | None = None,
    subagent_id: str | None = None,
    subagent_type: str | None = None,
    tool_call_id: str | None = None,
    parent_job_id: str | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    if kind not in JOB_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    workspace = workspace or os.getcwd()
    conv = conversation_id or _env_conversation_id()
    if not conv:
        ident = whoami(workspace=workspace)
        conv = ident.get("conversation_id")
    if not conv:
        return {"ok": False, "error": "conversation_id required"}
    ensure_session(conv, workspace_roots=[workspace])
    now = now_iso()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            if subagent_id:
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE subagent_id = ?",
                    (subagent_id,),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE jobs SET title = COALESCE(?, title), summary = COALESCE(?, summary),
                          status = 'running', subagent_type = COALESCE(?, subagent_type),
                          tool_call_id = COALESCE(?, tool_call_id), updated_at = ?, rev = rev + 1
                        WHERE id = ?
                        """,
                        (
                            title,
                            truncate(summary),
                            subagent_type,
                            tool_call_id,
                            now,
                            existing["id"],
                        ),
                    )
                    _append_job_event(conn, existing["id"], existing["status"], "running", source, title)
                    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (existing["id"],)).fetchone()
                    return {"ok": True, "job": row_dict(row), "updated": True}
            jid = new_id()
            conn.execute(
                """
                INSERT INTO jobs(
                  id, conversation_id, kind, status, title, summary,
                  subagent_id, subagent_type, tool_call_id, parent_job_id,
                  started_at, updated_at, rev
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    jid,
                    conv,
                    kind,
                    title,
                    truncate(summary),
                    subagent_id,
                    subagent_type,
                    tool_call_id,
                    parent_job_id,
                    now,
                    now,
                ),
            )
            _append_job_event(conn, jid, None, "running", source, title)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        return {"ok": True, "job": row_dict(row), "updated": False}
    finally:
        conn.close()

def job_update(
    job_id: str,
    rev: int,
    *,
    status: str | None = None,
    summary: str | None = None,
    title: str | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    if status is not None and status not in JOB_STATUSES:
        return {"ok": False, "error": "invalid_status", "status": status}
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "job_not_found", "id": job_id}
            if row["rev"] != rev:
                return {"ok": False, "error": "rev_conflict", "current_rev": row["rev"]}
            new_status = status or row["status"]
            ended = now_iso() if new_status in ("done", "error", "aborted") else None
            conn.execute(
                """
                UPDATE jobs SET
                  status = ?, summary = COALESCE(?, summary), title = COALESCE(?, title),
                  updated_at = ?, ended_at = COALESCE(?, ended_at), rev = rev + 1
                WHERE id = ?
                """,
                (new_status, truncate(summary) if summary else None, title, now_iso(), ended, job_id),
            )
            if status and status != row["status"]:
                _append_job_event(conn, job_id, row["status"], status, source, summary)
        out = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return {"ok": True, "job": row_dict(out)}
    finally:
        conn.close()

def job_done(
    job_id: str,
    rev: int,
    *,
    summary: str | None = None,
    status: str = "done",
    source: str = "cli",
) -> dict[str, Any]:
    if status not in ("done", "error", "aborted"):
        return {"ok": False, "error": "invalid_terminal_status", "status": status}
    result = job_update(job_id, rev, status=status, summary=summary, source=source)
    if result.get("ok"):
        from .pack_ops import post_notice

        job = result["job"]
        post_notice(
            kind="job_done",
            body=truncate(summary or job.get("title") or job_id),
            to_conversation_id=None,
            from_conversation_id=job.get("conversation_id"),
            workspace=None,
        )
    return result

def acquire_lease(
    resource_key: str,
    owner_conversation_id: str,
    purpose: str | None = None,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    until = (now + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat()
    now_s = now.replace(microsecond=0).isoformat()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute(
                "SELECT * FROM leases WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            if row is not None:
                held = row["until"] >= now_s and row["owner_conversation_id"] != owner_conversation_id
                if held:
                    return {
                        "ok": False,
                        "error": "lease_held",
                        "owner": row["owner_conversation_id"],
                        "until": row["until"],
                    }
                conn.execute(
                    """
                    UPDATE leases SET owner_conversation_id = ?, until = ?, purpose = ?, updated_at = ?
                    WHERE resource_key = ?
                    """,
                    (owner_conversation_id, until, purpose, now_s, resource_key),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO leases(resource_key, owner_conversation_id, until, purpose, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (resource_key, owner_conversation_id, until, purpose, now_s, now_s),
                )
        return {"ok": True, "resource_key": resource_key, "until": until, "owner": owner_conversation_id}
    finally:
        conn.close()

def release_leases_for_session(conversation_id: str) -> dict[str, Any]:
    conn = api_common._conn()
    try:
        with write_tx(conn):
            cur = conn.execute(
                "DELETE FROM leases WHERE owner_conversation_id = ?",
                (conversation_id,),
            )
        return {"ok": True, "released": cur.rowcount}
    finally:
        conn.close()

def job_claim(
    job_id: str,
    rev: int,
    *,
    conversation_id: str | None = None,
    workspace: str | None = None,
    source: str = "cli",
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    conv = conversation_id or _env_conversation_id()
    if not conv:
        ident = whoami(workspace=workspace)
        conv = ident.get("conversation_id")
    if not conv:
        return {"ok": False, "error": "conversation_id required"}
    lease = acquire_lease(f"job:{job_id}", conv, purpose="job_claim")
    if not lease.get("ok"):
        return lease
    until = lease["until"]
    conn = api_common._conn()
    try:
        with write_tx(conn):
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "job_not_found", "id": job_id}
            if row["rev"] != rev:
                return {"ok": False, "error": "rev_conflict", "current_rev": row["rev"]}
            conn.execute(
                """
                UPDATE jobs SET claim_owner = ?, claim_until = ?, updated_at = ?, rev = rev + 1
                WHERE id = ?
                """,
                (conv, until, now_iso(), job_id),
            )
            _append_job_event(conn, job_id, row["status"], row["status"], source, f"claimed by {conv}")
        out = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return {"ok": True, "job": row_dict(out), "lease": lease}
    finally:
        conn.close()
