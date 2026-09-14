from __future__ import annotations

import json
import os
from typing import Any

from lucid_memories.storage.db import connect, now_iso, write_tx
from lucid_memories.runtime.util import dumps, is_stale_heartbeat
from . import memory, persona
from .api_common import (
    JOB_KINDS,
    JOB_STATUSES,
    KNOWLEDGE_KINDS,
    KNOWLEDGE_SCOPES,
    NOTICE_KINDS,
    PACK_KINDS,
    SOURCES,
    _conn,
)
from .dashboard_ops import dashboard_graph
from .embedding_ops import (
    backfill_embeddings,
    embedding_status,
    index_embedding,
    semantic_search,
)
from .identity_ops import status, whoami
from .job_ops import (
    acquire_lease,
    job_claim,
    job_done,
    job_start,
    job_update,
    release_leases_for_session,
)
from .knowledge_ops import archive, list_knowledge, remember, search
from .pack_ops import (
    load,
    load_handoff,
    post_notice,
    reload,
    save_handoff,
    snapshot_compact,
)
from .retrieval_ops import (
    collect_retrieval,
    eval_retrieval,
    improve_from_logs,
    improve_retrieval,
    log_retrieval,
    measure_retrieval,
    retrieval_gaps,
    _digest_memory_lines,
)
from .session_ops import (
    bind_current_session,
    bind_pending_prompt,
    ensure_session,
    generation_changed,
    heartbeat,
    last_prompt,
    last_turn,
    persist_session_summary,
    save_prompt,
    unbind_current_session,
)
from .usage_ops import load_artifact, record_activity, record_mcp_usage, record_usage

def memory_status() -> dict[str, Any]:
    conn = _conn()
    try:
        return {"ok": True, **memory.status(conn)}
    finally:
        conn.close()

def memory_candidates(
    *,
    workspace: str | None = None,
    status: str = "pending",
    limit: int = 50,
) -> dict[str, Any]:
    conn = _conn()
    try:
        return {
            "ok": True,
            "candidates": memory.list_candidates(
                conn,
                workspace=workspace,
                status=status,
                limit=limit,
            ),
        }
    finally:
        conn.close()

def memory_worker(
    *,
    limit: int = 20,
    sweep_faded: bool = True,
) -> dict[str, Any]:
    conn = _conn()
    try:
        processed = memory.process_tasks(conn, limit=limit)
        decay = memory.sweep(conn) if sweep_faded else None
        return {
            "ok": True,
            "worker": processed,
            "decay": decay,
            "status": memory.status(conn),
        }
    finally:
        conn.close()

def promote_memory_candidate(candidate_id: str) -> dict[str, Any]:
    if not candidate_id:
        return {"ok": False, "error": "candidate_id required"}
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM memory_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "error": "candidate_not_found", "id": candidate_id}
    if row["status"] != "pending":
        return {
            "ok": False,
            "error": "candidate_not_pending",
            "id": candidate_id,
            "status": row["status"],
        }
    workspace = row["workspace_root"] or None
    promoted = remember(
        row["title"],
        row["summary"],
        kind=row["kind"],
        scope="workspace" if workspace else "global",
        workspace=workspace,
        conversation_id=row["conversation_id"],
        tags=json.loads(row["tags_json"] or "[]"),
        confidence=row["confidence"],
        salience=row["salience"],
        source_event_id=row["source_event_id"],
        provenance={"source": "memory_candidate", "candidate_id": candidate_id},
        source="auto",
    )
    if not promoted.get("ok"):
        return promoted
    conn = _conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                UPDATE memory_candidates
                SET status = 'accepted', knowledge_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (promoted["id"], now_iso(), candidate_id),
            )
    finally:
        conn.close()
    return {"ok": True, "candidate_id": candidate_id, "knowledge": promoted}

def persona_candidates(
    *,
    status: str = "pending",
    limit: int = 50,
) -> dict[str, Any]:
    return {
        "ok": True,
        "candidates": persona.list_preference_candidates(status=status, limit=limit),
    }

def persona_revisions(*, limit: int = 50) -> dict[str, Any]:
    return {
        "ok": True,
        "revisions": persona.list_persona_revisions(limit=limit),
    }

def persona_worker(
    *,
    limit: int = 20,
    auto_apply: bool = True,
) -> dict[str, Any]:
    return persona.persona_worker(limit=limit, auto_apply=auto_apply)

def persona_rollback(revision_id: str) -> dict[str, Any]:
    return persona.rollback_persona_revision(revision_id)

def persona_reject(candidate_id: str) -> dict[str, Any]:
    return persona.reject_preference_candidate(candidate_id)

def persona_workspace(
    action: str,
    *,
    workspace: str | None = None,
    type_id: str | None = None,
    rationale: str | None = None,
    confidence: float | None = None,
    proposed_by: str = "agent",
    section_id: str | None = None,
    title: str | None = None,
    content: str | None = None,
    priority: int = 80,
    limit: int = 20,
    created_by: str = "user",
) -> dict[str, Any]:
    from lucid_memories.core import workspace_persona

    if action == "types":
        workspace_persona.ensure_type_templates()
        return {"ok": True, "types": workspace_persona.list_type_ids()}
    if action == "infer":
        if not workspace:
            return {"ok": False, "error": "workspace_required"}
        return workspace_persona.infer_workspace_type(workspace)
    if action == "status":
        if not workspace:
            return {"ok": False, "error": "workspace_required"}
        return workspace_persona.workspace_status(workspace=workspace)
    if action == "propose":
        if not workspace or not type_id or not rationale:
            return {"ok": False, "error": "workspace_type_rationale_required"}
        try:
            return workspace_persona.propose_binding(
                workspace=workspace,
                type_id=type_id,
                rationale=rationale,
                confidence=confidence,
                proposed_by=proposed_by,
            )
        except KeyError as exc:
            return {"ok": False, "error": "invalid_type", "detail": str(exc)}
    if action == "confirm":
        if not workspace:
            return {"ok": False, "error": "workspace_required"}
        return workspace_persona.confirm_binding(
            workspace=workspace,
            type_id=type_id,
            created_by=created_by,
        )
    if action == "reject":
        if not workspace:
            return {"ok": False, "error": "workspace_required"}
        return workspace_persona.reject_binding(workspace=workspace, created_by=created_by)
    if action == "override":
        if not workspace or not type_id:
            return {"ok": False, "error": "workspace_type_required"}
        return workspace_persona.override_binding(
            workspace=workspace,
            type_id=type_id,
            created_by=created_by,
        )
    if action == "overlay_set":
        if not workspace or not section_id or not title or content is None:
            return {"ok": False, "error": "workspace_section_title_content_required"}
        return workspace_persona.set_workspace_overlay_section(
            workspace=workspace,
            section_id=section_id,
            title=title,
            content=content,
            priority=priority,
            created_by=created_by,
        )
    if action == "overlay_remove":
        if not workspace or not section_id:
            return {"ok": False, "error": "workspace_section_required"}
        return workspace_persona.remove_workspace_overlay_section(
            workspace=workspace,
            section_id=section_id,
            created_by=created_by,
        )
    if action == "events":
        if not workspace:
            return {"ok": False, "error": "workspace_required"}
        return {
            "ok": True,
            "events": workspace_persona.list_binding_events(workspace=workspace, limit=limit),
        }
    return {"ok": False, "error": "invalid_action", "action": action}

def digest_text(
    conversation_id: str,
    workspace: str | None = None,
    *,
    generation_id: str | None = None,
) -> str:
    bind_pending_prompt(conversation_id)
    snap = status(workspace=workspace, conversation_id=conversation_id)
    running = sum(1 for j in snap.get("jobs") or [] if j.get("effective_status") == "running")
    notices = len(snap.get("notices_unread") or [])
    reload_flag = "yes" if snap.get("reload_available") else "no"
    lines = [
        f"[lucid-memories] conversation_id={conversation_id}",
        f"running_jobs={running} unread_notices={notices} reload_available={reload_flag}",
        "CLI/MCP: whoami, status, search, recall, remember, link, job, reload, archive.",
    ]
    turn = last_turn(conversation_id)
    if turn and turn.get("last_prompt"):
        lines.extend(
            _digest_memory_lines(
                turn["last_prompt"],
                workspace or turn.get("workspace_root"),
                conversation_id=conversation_id,
                prompt_at=turn.get("last_prompt_at"),
                generation_id=generation_id,
            )
        )
    return "\n".join(lines)
