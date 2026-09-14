from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lucid_memories.storage.db import connect_readonly
from . import memory
from lucid_memories.runtime.util import parse_iso, truncate, workspace_matches, normalize_root
from . import api_common

def _dashboard_json_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]

def _dashboard_node_id(
    database: str,
    kind: str,
    item_id: Any,
    title: Any,
) -> str:
    prefix = "memory" if database == "sqlite" else "map"
    value = str(item_id or "").strip()
    if value:
        return f"{prefix}:{value}"
    seed = f"{kind}:{title or ''}".encode("utf-8")
    return f"{prefix}:anonymous:{hashlib.sha1(seed).hexdigest()[:16]}"

def _dashboard_temperature(count: int, last_at: str | None, *, now: datetime) -> float:
    """Return a display-only activity temperature for a graph node."""
    if count <= 0 or not last_at:
        return 0.0
    last = parse_iso(last_at)
    if last is None:
        return 0.0
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - last).total_seconds() / 86400.0)
    activity = min(1.0, count / 10.0)
    recency = 2.0 ** (-age_days / 14.0)
    return round(activity * recency, 6)

def dashboard_graph(
    *,
    database: Path | None = None,
    workspace: str | None = None,
    conversation_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Build a graph of conversations, retrieval requests, and results."""
    try:
        row_limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        row_limit = 200
    target_workspace = normalize_root(workspace) if workspace else None
    conn = connect_readonly(database) if database is not None else api_common._conn()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("r.conversation_id = ?")
            params.append(conversation_id)
        if since:
            clauses.append("r.started_at >= ?")
            params.append(since)
        if until:
            clauses.append("r.started_at <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT r.*,
                   l.id AS log_id,
                   l.prompt AS log_prompt,
                   l.prompt_at AS log_prompt_at,
                   l.links_json AS log_links_json,
                   l.hits_json AS legacy_hits_json
            FROM retrieval_requests r
            LEFT JOIN retrieval_logs l ON l.request_id = r.id
            {where}
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (*params, row_limit),
        ).fetchall()
        if target_workspace:
            rows = [
                row for row in rows
                if workspace_matches([row["workspace_root"] or ""], target_workspace)
            ]

        request_ids = [str(row["id"]) for row in rows]
        result_rows_by_request: dict[str, list[sqlite3.Row]] = {
            request_id: [] for request_id in request_ids
        }
        if request_ids:
            placeholders = ",".join("?" for _ in request_ids)
            result_rows = conn.execute(
                f"""
                SELECT * FROM retrieval_results
                WHERE request_id IN ({placeholders})
                ORDER BY request_id, ordinal
                """,
                tuple(request_ids),
            ).fetchall()
            for result_row in result_rows:
                result_rows_by_request.setdefault(
                    str(result_row["request_id"]), []
                ).append(result_row)

        knowledge_ids = {
            str(result_row["entity_id"])
            for row in rows
            for result_row in result_rows_by_request.get(str(row["id"]), [])
            if result_row["source_db"] == "sqlite" and result_row["entity_id"]
        }
        knowledge_by_id: dict[str, sqlite3.Row] = {}
        if knowledge_ids:
            placeholders = ",".join("?" for _ in knowledge_ids)
            knowledge_rows = conn.execute(
                f"SELECT * FROM knowledge WHERE id IN ({placeholders})",
                tuple(knowledge_ids),
            ).fetchall()
            knowledge_by_id = {str(row["id"]): row for row in knowledge_rows}

        conversation_ids = {
            str(row["conversation_id"])
            for row in rows
            if row["conversation_id"]
        }
        sessions_by_id: dict[str, sqlite3.Row] = {}
        if conversation_ids:
            placeholders = ",".join("?" for _ in conversation_ids)
            session_rows = conn.execute(
                f"SELECT * FROM sessions WHERE conversation_id IN ({placeholders})",
                tuple(conversation_ids),
            ).fetchall()
            sessions_by_id = {str(row["conversation_id"]): row for row in session_rows}

        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        conversation_stats: dict[str, dict[str, Any]] = {}
        retrieval_groups: dict[tuple[str, str], str] = {}
        generated_at = datetime.now(timezone.utc)

        def add_node(node_id: str, **values: Any) -> dict[str, Any]:
            node = nodes.get(node_id)
            if node is None:
                node = {"id": node_id, **values}
                nodes[node_id] = node
            else:
                for key, value in values.items():
                    if value is not None and (node.get(key) in (None, "")):
                        node[key] = value
            return node

        def add_edge(
            source: str,
            target: str,
            relation: str,
            *,
            log_id: str | None = None,
            at: str | None = None,
            **values: Any,
        ) -> None:
            edge_id = f"{relation}:{source}:{target}:{log_id or 'aggregate'}"
            edge = edges.get(edge_id)
            if edge is None:
                edge = {
                    "id": edge_id,
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "count": 1,
                    "first_at": at,
                    "last_at": at,
                    **values,
                }
                edges[edge_id] = edge
            else:
                edge["count"] = int(edge.get("count") or 0) + 1
                if at and (not edge.get("last_at") or at > edge["last_at"]):
                    edge["last_at"] = at
                if at and (not edge.get("first_at") or at < edge["first_at"]):
                    edge["first_at"] = at

        def add_recalled_node(hit: dict[str, Any], at: str | None) -> str | None:
            database = str(hit.get("db") or "map")
            kind = str(hit.get("kind") or "knowledge")
            item_id = hit.get("id")
            title = str(hit.get("title") or item_id or "Untitled")
            node_id = _dashboard_node_id(database, kind, item_id, title)
            is_memory = database == "sqlite"
            knowledge = knowledge_by_id.get(str(item_id)) if item_id else None
            node = add_node(
                node_id,
                type="memory" if is_memory else "map",
                database=database,
                kind=kind,
                item_id=item_id,
                label=title,
                recalled=True,
                retrieval_count=0,
                last_recalled_at=None,
                selected_count=0,
                memory_score=hit.get("memory_score"),
                body_preview=None,
            )
            node["retrieval_count"] = int(node.get("retrieval_count") or 0) + 1
            if hit.get("selected", True):
                node["selected_count"] = int(node.get("selected_count") or 0) + 1
            if at and (
                not node.get("last_recalled_at") or at > node["last_recalled_at"]
            ):
                node["last_recalled_at"] = at
            if knowledge is not None:
                node["memory_score"] = memory.score(knowledge, now=at)
                node["body_preview"] = truncate(knowledge["body"], 240)
                node["memory_status"] = knowledge["memory_status"]
            return node_id

        for row in rows:
            request_id = str(row["id"])
            raw_conversation_id = row["conversation_id"]
            cid = str(raw_conversation_id) if raw_conversation_id else "unknown"
            conversation_node_id = f"conversation:{cid}"
            session = sessions_by_id.get(cid)
            result_rows = result_rows_by_request.get(request_id, [])
            links = _dashboard_json_list(row["log_links_json"])
            stats = conversation_stats.setdefault(
                cid,
                {"count": 0, "last_at": None, "node_id": conversation_node_id},
            )
            stats["count"] += 1
            prompt_at = row["started_at"] or row["log_prompt_at"] or row["completed_at"]
            if prompt_at and (
                not stats["last_at"] or prompt_at > stats["last_at"]
            ):
                stats["last_at"] = prompt_at
            add_node(
                conversation_node_id,
                type="conversation",
                conversation_id=None if cid == "unknown" else cid,
                label=(
                    "Unknown conversation"
                    if cid == "unknown"
                    else (session["title"] if session and session["title"] else cid)
                ),
                status=session["status"] if session else "unknown",
                workspace_root=(
                    row["workspace_root"]
                    or (session["workspace_roots_json"] if session else None)
                ),
                retrieval_count=stats["count"],
                last_recalled_at=stats["last_at"],
            )
            retrieval_prompt_text = (
                row["log_prompt"] or row["query"] or row["target"] or row["method"] or ""
            ).strip()
            group_key = (cid, retrieval_prompt_text) if retrieval_prompt_text else (cid, request_id)

            is_new_retrieval = group_key not in retrieval_groups
            if is_new_retrieval:
                retrieval_groups[group_key] = request_id
                representative_request_id = request_id
            else:
                representative_request_id = retrieval_groups[group_key]

            retrieval_node_id = f"retrieval:{representative_request_id}"
            sqlite_hit_count = sum(
                1 for result_row in result_rows if result_row["source_db"] == "sqlite"
            )
            map_hit_count = sum(
                1 for result_row in result_rows if result_row["source_db"] == "map"
            )

            retrieval_node = nodes.get(retrieval_node_id)
            if retrieval_node is None:
                add_node(
                    retrieval_node_id,
                    type="retrieval",
                    request_id=representative_request_id,
                    request_ids=[request_id],
                    retrieval_count=1,
                    log_id=row["log_id"],
                    label=truncate(
                        row["log_prompt"] or row["query"] or row["target"] or row["method"],
                        100,
                    ),
                    prompt=row["log_prompt"] or row["query"] or row["target"] or "",
                    query=row["query"],
                    target=row["target"],
                    method=row["method"],
                    source=row["source"],
                    generation_id=row["generation_id"],
                    request_status=row["status"],
                    prompt_at=prompt_at,
                    sqlite_hit_count=sqlite_hit_count,
                    map_hit_count=map_hit_count,
                    link_count=len(links),
                )
            else:
                retrieval_node["retrieval_count"] = (
                    int(retrieval_node.get("retrieval_count") or 1) + 1
                )
                if "request_ids" in retrieval_node and isinstance(retrieval_node["request_ids"], list):
                    if request_id not in retrieval_node["request_ids"]:
                        retrieval_node["request_ids"].append(request_id)
                retrieval_node["sqlite_hit_count"] = (
                    int(retrieval_node.get("sqlite_hit_count") or 0) + sqlite_hit_count
                )
                retrieval_node["map_hit_count"] = (
                    int(retrieval_node.get("map_hit_count") or 0) + map_hit_count
                )
                retrieval_node["link_count"] = (
                    int(retrieval_node.get("link_count") or 0) + len(links)
                )
                if prompt_at and (
                    not retrieval_node.get("prompt_at") or prompt_at > retrieval_node["prompt_at"]
                ):
                    retrieval_node["prompt_at"] = prompt_at

            add_edge(
                conversation_node_id,
                retrieval_node_id,
                "contains",
                log_id=representative_request_id,
                at=prompt_at,
            )
            for result_row in result_rows:
                hit = {
                    "db": result_row["source_db"],
                    "kind": result_row["entity_type"],
                    "id": result_row["entity_id"],
                    "title": result_row["title"],
                    "rank": result_row["rank"],
                    "score": result_row["score"],
                    "memory_score": result_row["memory_score"],
                    "selected": bool(result_row["selected"]),
                }
                item_node_id = add_recalled_node(hit, prompt_at)
                if item_node_id:
                    add_edge(
                        retrieval_node_id,
                        item_node_id,
                        "recalled",
                        log_id=representative_request_id,
                        at=prompt_at,
                        database=hit.get("db"),
                        kind=hit.get("kind"),
                        rank=hit.get("rank"),
                        score=hit.get("score"),
                        memory_score=hit.get("memory_score"),
                        selected=hit.get("selected"),
                    )
            for link in links:
                to_node_id = _dashboard_node_id(
                    str(link.get("to_db") or "map"),
                    str(link.get("to_kind") or "material"),
                    link.get("to_id"),
                    link.get("to_title"),
                )
                if to_node_id not in nodes:
                    add_recalled_node(
                        {
                            "db": link.get("to_db") or "map",
                            "kind": link.get("to_kind") or "material",
                            "id": link.get("to_id"),
                            "title": link.get("to_title"),
                        },
                        prompt_at,
                    )
                from_id = link.get("from_id") or link.get("from_title")
                if not from_id:
                    continue
                from_kind = (
                    "procedure"
                    if str(link.get("rel") or "").upper() == "USES"
                    else "directive"
                )
                from_node_id = _dashboard_node_id(
                    "map",
                    from_kind,
                    link.get("from_id"),
                    link.get("from_title"),
                )
                add_node(
                    from_node_id,
                    type="map",
                    database="map",
                    kind=from_kind,
                    item_id=link.get("from_id"),
                    label=link.get("from_title") or link.get("from_id"),
                    recalled=False,
                    retrieval_count=0,
                    last_recalled_at=None,
                    temperature=0.0,
                )
                add_edge(
                    from_node_id,
                    to_node_id,
                    str(link.get("rel") or "RELATED").upper(),
                    log_id=request_id,
                    at=prompt_at,
                    status=link.get("status"),
                )

        for cid, stats in conversation_stats.items():
            node = nodes[stats["node_id"]]
            node["retrieval_count"] = stats["count"]
            node["last_recalled_at"] = stats["last_at"]
            node["temperature"] = _dashboard_temperature(
                stats["count"], stats["last_at"], now=generated_at
            )
        for node in nodes.values():
            if node.get("type") != "conversation" and node.get("recalled", True):
                count = int(node.get("retrieval_count") or 0)
                node["temperature"] = _dashboard_temperature(
                    count, node.get("last_recalled_at"), now=generated_at
                )

        return {
            "ok": True,
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "meta": {
                "workspace": target_workspace,
                "conversation_id": conversation_id,
                "since": since,
                "until": until,
                "limit": row_limit,
                "log_count": len(rows),
                "generated_at": generated_at.replace(microsecond=0).isoformat(),
                "temperature": {
                    "kind": "display_only",
                    "description": (
                        "activity count capped at 10, multiplied by a 14-day "
                        "exponential recency factor"
                    ),
                },
            },
        }
    finally:
        conn.close()
