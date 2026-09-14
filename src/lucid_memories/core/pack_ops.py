from __future__ import annotations

import os
from typing import Any

from lucid_memories.storage import blobs
from . import memory, retrieval
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import DEFAULT_LOAD_BUDGET
from lucid_memories.runtime.util import dumps, estimate_tokens, looks_like_id, new_id, normalize_root, row_dict, truncate, workspace_matches
from . import api_common
from .api_common import NOTICE_KINDS, PACK_KINDS
from .identity_ops import _env_conversation_id, whoami
from .knowledge_ops import _knowledge_text, list_knowledge, remember, search

def _load_pack_items(
    conn: sqlite3.Connection,
    pack_id: str,
    budget: int,
) -> dict[str, Any]:
    pack = conn.execute("SELECT * FROM packs WHERE id = ?", (pack_id,)).fetchone()
    if pack is None:
        return {"ok": False, "error": "pack_not_found", "pack_id": pack_id}
    items = conn.execute(
        """
        SELECT * FROM pack_items
        WHERE pack_id = ?
        ORDER BY load_priority ASC, ordinal ASC
        """,
        (pack_id,),
    ).fetchall()
    loaded: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    used = 0
    for item in items:
        rec = row_dict(item)
        text = ""
        if item["knowledge_id"]:
            k = conn.execute(
                "SELECT * FROM knowledge WHERE id = ?",
                (item["knowledge_id"],),
            ).fetchone()
            if k is not None:
                rec["knowledge"] = row_dict(k)
                text = _knowledge_text(k)
        if not text and item["blob_sha"]:
            text = blobs.get_blob_text(item["blob_sha"]) or ""
        rec["pointer_uri"] = item["pointer_uri"]
        cost = item["token_estimate"] or estimate_tokens(text)
        rec["token_estimate"] = cost
        if text and used + cost <= budget:
            rec["body"] = text
            rec["omitted"] = False
            loaded.append(rec)
            used += cost
        else:
            rec.pop("body", None)
            rec["omitted"] = True
            omitted.append(
                {
                    "id": rec["id"],
                    "knowledge_id": rec.get("knowledge_id"),
                    "pointer_uri": rec.get("pointer_uri"),
                    "token_estimate": cost,
                    "load_priority": rec["load_priority"],
                }
            )
    return {
        "ok": True,
        "pack": row_dict(pack),
        "items": loaded,
        "omitted": omitted,
        "tokens_used": used,
        "budget": budget,
    }

def _load_tracking_results(
    result: dict[str, Any],
    *,
    target: str,
) -> list[dict[str, Any]]:
    tracked: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        knowledge_id = item.get("knowledge_id")
        entity_id = knowledge_id or item.get("id")
        tracked.append(
            {
                "source_db": "sqlite",
                "entity_type": "knowledge" if knowledge_id or not item.get("pointer_uri") else "pack_item",
                "entity_id": entity_id,
                "title": item.get("title") or target,
                "rank": len(tracked) + 1,
                "selected": True,
                "metadata": {
                    "target": target,
                    "pack_id": result.get("pack", {}).get("id") if result.get("pack") else None,
                    "omitted": False,
                },
            }
        )
    for item in result.get("omitted") or []:
        tracked.append(
            {
                "source_db": "sqlite",
                "entity_type": "knowledge" if item.get("knowledge_id") else "pack_item",
                "entity_id": item.get("knowledge_id") or item.get("id"),
                "title": item.get("title") or target,
                "rank": len(tracked) + 1,
                "selected": False,
                "metadata": {"target": target, "omitted": True},
            }
        )
    return tracked

def _finish_load_tracking(
    request_id: str | None,
    result: dict[str, Any],
    *,
    target: str,
) -> dict[str, Any]:
    if request_id:
        retrieval.record_results(
            request_id,
            _load_tracking_results(result, target=target),
        )
        retrieval.finish_request(request_id)
        result["retrieval_request_id"] = request_id
    return result

def load(
    target: str,
    budget_tokens: int = DEFAULT_LOAD_BUDGET,
    workspace: str | None = None,
    conversation_id: str | None = None,
    *,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    if not target or not target.strip():
        return {"ok": False, "error": "target required"}
    target = target.strip()
    tracking_id = request_id
    if track and tracking_id is None:
        tracking_id = retrieval.start_request(
            "load",
            target=target,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
            metadata={"budget_tokens": budget_tokens},
        )
    conn = api_common._conn()
    try:
        if looks_like_id(target):
            pack = conn.execute("SELECT id FROM packs WHERE id = ?", (target,)).fetchone()
            if pack:
                return _finish_load_tracking(
                    tracking_id,
                    _load_pack_items(conn, target, budget_tokens),
                    target=target,
                )
            k = conn.execute("SELECT * FROM knowledge WHERE id = ?", (target,)).fetchone()
            if k:
                text = _knowledge_text(k)
                item = row_dict(k)
                memory.touch(conn, [target])
                item["body"] = text if estimate_tokens(text) <= budget_tokens else None
                item["omitted"] = item["body"] is None
                return _finish_load_tracking(
                    tracking_id,
                    {
                    "ok": True,
                    "knowledge": [item],
                    "items": [item],
                    "omitted": [],
                    "tokens_used": estimate_tokens(text) if item["body"] else 0,
                    "budget": budget_tokens,
                    },
                    target=target,
                )
        found = search(target, workspace=workspace, limit=12, track=False)
        if not found.get("ok"):
            if tracking_id:
                retrieval.fail_request(tracking_id)
            return found
        loaded: list[dict[str, Any]] = []
        omitted: list[dict[str, Any]] = []
        used = 0
        for pack in found.get("packs") or []:
            result = _load_pack_items(conn, pack["id"], max(0, budget_tokens - used))
            if result.get("ok"):
                loaded.extend(result.get("items") or [])
                omitted.extend(result.get("omitted") or [])
                used += result.get("tokens_used") or 0
                if used >= budget_tokens:
                    break
        if used < budget_tokens:
            for k in found.get("knowledge") or []:
                text = k.get("body") or ""
                if k.get("blob_sha") and not text:
                    text = blobs.get_blob_text(k["blob_sha"]) or ""
                cost = estimate_tokens(text)
                if text and used + cost <= budget_tokens:
                    item = dict(k)
                    item["body"] = text
                    item["omitted"] = False
                    loaded.append(item)
                    used += cost
                else:
                    omitted.append({"id": k.get("id"), "title": k.get("title"), "token_estimate": cost})
        return _finish_load_tracking(
            tracking_id,
            {
                "ok": True,
                "query": target,
                "items": loaded,
                "omitted": omitted,
                "tokens_used": used,
                "budget": budget_tokens,
            },
            target=target,
        )
    except Exception:
        if tracking_id:
            retrieval.fail_request(tracking_id)
        raise
    finally:
        conn.close()

def post_notice(
    kind: str,
    body: str | None,
    *,
    to_conversation_id: str | None = None,
    from_conversation_id: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    if kind not in NOTICE_KINDS:
        return {"ok": False, "error": "invalid_kind", "kind": kind}
    nid = new_id()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO notices(
                  id, to_conversation_id, from_conversation_id, workspace_root,
                  kind, body, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nid,
                    to_conversation_id,
                    from_conversation_id,
                    normalize_root(workspace) if workspace else None,
                    kind,
                    truncate(body, 1000),
                    now_iso(),
                ),
            )
        return {"ok": True, "id": nid}
    finally:
        conn.close()

def _add_pack_item(
    conn: sqlite3.Connection,
    pack_id: str,
    ordinal: int,
    *,
    knowledge_id: str | None = None,
    blob_sha: str | None = None,
    pointer_uri: str | None = None,
    load_priority: int = 100,
    token_estimate: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO pack_items(
          id, pack_id, ordinal, knowledge_id, blob_sha, pointer_uri,
          load_priority, token_estimate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            pack_id,
            ordinal,
            knowledge_id,
            blob_sha,
            pointer_uri,
            load_priority,
            token_estimate,
        ),
    )

def snapshot_compact(
    conversation_id: str,
    *,
    workspace: str | None = None,
    generation_id: str | None = None,
    trigger: str | None = None,
    context_usage_percent: float | None = None,
    context_tokens: int | None = None,
    context_window_size: int | None = None,
    message_count: int | None = None,
    messages_to_compact: int | None = None,
    is_first_compaction: bool | None = None,
) -> dict[str, Any]:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id required"}
    workspace = workspace or os.getcwd()
    ws = normalize_root(workspace)
    now = now_iso()
    pack_id = new_id()
    conn = api_common._conn()
    try:
        with write_tx(conn):
            jobs = conn.execute(
                """
                SELECT * FROM jobs
                WHERE conversation_id = ? AND status IN ('pending','running','blocked')
                ORDER BY updated_at DESC
                """,
                (conversation_id,),
            ).fetchall()
            knowledge_rows = conn.execute(
                """
                SELECT * FROM knowledge
                WHERE (expires_at IS NULL OR expires_at > ?)
                  AND (
                    source_conversation_id = ?
                    OR (scope = 'workspace' AND workspace_root = ?)
                    OR scope = 'global'
                  )
                ORDER BY updated_at DESC
                LIMIT 80
                """,
                (now, conversation_id, ws),
            ).fetchall()
            conn.execute(
                """
                INSERT INTO packs(id, kind, conversation_id, workspace_root, title, token_estimate, created_at)
                VALUES (?, 'compact_snapshot', ?, ?, ?, 0, ?)
                """,
                (pack_id, conversation_id, ws, f"compact snapshot {now}", now),
            )
            ordinal = 0
            total_tokens = 0
            job_lines = []
            for job in jobs:
                line = f"{job['status']} {job['kind']} {job['title'] or ''} {truncate(job['summary'] or '', 200)}"
                job_lines.append(line.strip())
                cost = estimate_tokens(line)
                _add_pack_item(
                    conn,
                    pack_id,
                    ordinal,
                    pointer_uri=f"job:{job['id']}",
                    load_priority=0,
                    token_estimate=cost,
                )
                ordinal += 1
                total_tokens += cost
            if job_lines:
                sha = blobs.put_blob(conn, "\n".join(job_lines).encode("utf-8"))
                _add_pack_item(
                    conn,
                    pack_id,
                    ordinal,
                    blob_sha=sha,
                    load_priority=0,
                    token_estimate=estimate_tokens("\n".join(job_lines)),
                )
                ordinal += 1
                total_tokens += estimate_tokens("\n".join(job_lines))
            for k in knowledge_rows:
                text = _knowledge_text(k)
                priority = 10 if k["source_conversation_id"] == conversation_id else 20
                cost = estimate_tokens(k["title"] + "\n" + text)
                _add_pack_item(
                    conn,
                    pack_id,
                    ordinal,
                    knowledge_id=k["id"],
                    load_priority=priority,
                    token_estimate=cost,
                )
                ordinal += 1
                total_tokens += cost
            conn.execute(
                "UPDATE packs SET token_estimate = ? WHERE id = ?",
                (total_tokens, pack_id),
            )
            event_id = new_id()
            conn.execute(
                """
                INSERT INTO compact_events(
                  id, conversation_id, generation_id, trigger, context_usage_percent,
                  context_tokens, context_window_size, message_count, messages_to_compact,
                  is_first_compaction, pack_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    generation_id,
                    trigger,
                    context_usage_percent,
                    context_tokens,
                    context_window_size,
                    message_count,
                    messages_to_compact,
                    None if is_first_compaction is None else (1 if is_first_compaction else 0),
                    pack_id,
                    now,
                ),
            )
            post_body = f"compacted; reload pack {pack_id}"
        post_notice(
            kind="compacted",
            body=post_body,
            to_conversation_id=conversation_id,
            from_conversation_id=conversation_id,
            workspace=workspace,
        )
        return {"ok": True, "pack_id": pack_id, "token_estimate": total_tokens, "event_id": event_id}
    finally:
        conn.close()

def reload(
    conversation_id: str | None = None,
    budget_tokens: int = DEFAULT_LOAD_BUDGET,
    workspace: str | None = None,
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    conv = conversation_id or _env_conversation_id()
    if not conv:
        ident = whoami(workspace=workspace)
        conv = ident.get("conversation_id")
    if not conv:
        return {"ok": False, "error": "conversation_id required"}
    conn = api_common._conn()
    try:
        pack = conn.execute(
            """
            SELECT id FROM packs
            WHERE conversation_id = ? AND kind = 'compact_snapshot'
            ORDER BY created_at DESC LIMIT 1
            """,
            (conv,),
        ).fetchone()
        if pack is None:
            return {"ok": True, "pack": None, "items": [], "omitted": [], "message": "no compact snapshot"}
        return _load_pack_items(conn, pack["id"], budget_tokens)
    finally:
        conn.close()

def save_handoff(
    title: str,
    body: str,
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
    suggested_skills: list[str] | None = None,
    focus: str | None = None,
    scope: str = "workspace",
    source: str = "cli",
) -> dict[str, Any]:
    if not title:
        return {"ok": False, "error": "title required"}
    workspace = workspace or os.getcwd()
    conv = conversation_id or _env_conversation_id()
    skills = [s.strip() for s in (suggested_skills or []) if s and s.strip()]
    text = body or ""
    if focus and "## 焦点" not in text:
        text = f"## 焦点\n\n{focus}\n\n" + text
    if skills and "## suggested skills" not in text.lower():
        text = text.rstrip() + "\n\n## suggested skills\n\n" + "\n".join(f"- {s}" for s in skills) + "\n"
    tags = ["relay", "handoff"] + skills
    stored = remember(
        title,
        text,
        kind="handoff",
        scope=scope,
        workspace=workspace,
        conversation_id=conv,
        tags=tags,
        source=source,
    )
    if not stored.get("ok"):
        return stored
    kid = stored["id"]
    pack_id = new_id()
    now = now_iso()
    ws = None if scope == "global" else normalize_root(workspace)
    cost = estimate_tokens(text)
    conn = api_common._conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO packs(id, kind, conversation_id, workspace_root, title, token_estimate, created_at)
                VALUES (?, 'handoff', ?, ?, ?, ?, ?)
                """,
                (pack_id, conv, ws, title, cost, now),
            )
            _add_pack_item(
                conn,
                pack_id,
                0,
                knowledge_id=kid,
                load_priority=0,
                token_estimate=cost,
            )
        post_notice(
            kind="please_load",
            body=f"relay pack {pack_id}: {title}",
            to_conversation_id=None,
            from_conversation_id=conv,
            workspace=workspace if scope != "global" else None,
        )
        return {
            "ok": True,
            "pack_id": pack_id,
            "knowledge_id": kid,
            "title": title,
            "token_estimate": cost,
        }
    finally:
        conn.close()

def load_handoff(
    *,
    pack_id: str | None = None,
    target: str | None = None,
    workspace: str | None = None,
    budget_tokens: int = DEFAULT_LOAD_BUDGET,
) -> dict[str, Any]:
    workspace = workspace or os.getcwd()
    chosen = pack_id or (target if target and looks_like_id(target) else None)
    conn = api_common._conn()
    try:
        if chosen:
            pack = conn.execute("SELECT id FROM packs WHERE id = ?", (chosen,)).fetchone()
            if pack:
                result = _load_pack_items(conn, chosen, budget_tokens)
                result["relay"] = True
                return result
            return load(chosen, budget_tokens=budget_tokens, workspace=workspace)
        if target:
            found = search(target, workspace=workspace, kind="handoff", limit=8)
            if found.get("ok") and found.get("knowledge"):
                return load(found["knowledge"][0]["id"], budget_tokens=budget_tokens, workspace=workspace)
        rows = conn.execute(
            """
            SELECT * FROM packs
            WHERE kind = 'handoff'
            ORDER BY created_at DESC
            LIMIT 40
            """
        ).fetchall()
        matched = [
            r
            for r in rows
            if not r["workspace_root"]
            or workspace_matches([r["workspace_root"]], workspace)
        ]
        if not matched:
            listed = list_knowledge(kind="handoff", workspace=workspace, limit=5)
            items = listed.get("knowledge") or []
            if not items:
                return {"ok": True, "pack": None, "items": [], "message": "no handoff"}
            return load(items[0]["id"], budget_tokens=budget_tokens, workspace=workspace)
        result = _load_pack_items(conn, matched[0]["id"], budget_tokens)
        result["relay"] = True
        result["candidates"] = [
            {"id": r["id"], "title": r["title"], "created_at": r["created_at"]}
            for r in matched[:8]
        ]
        return result
    finally:
        conn.close()
