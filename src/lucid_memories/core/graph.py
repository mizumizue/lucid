"""Map: Directive / Procedure / Material on Ladybug. Bodies stay in SQLite."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from lucid_memories.storage import blobs
from . import retrieval
from lucid_memories.storage.db import connect as sqlite_connect
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.ladybug_runtime import connect as ladybug_connect
from lucid_memories.storage.paths import (
    DEFAULT_LOAD_BUDGET,
    graph_schema_path,
    legacy_map_path,
    map_path,
)
from lucid_memories.runtime.util import (
    dumps,
    estimate_tokens,
    fts_match_arg,
    looks_like_id,
    new_id,
    normalize_root,
    parse_iso,
    row_dict,
    truncate,
    useful_query_tokens,
    workspace_matches,
)

RECALL_HOT_COUNT = 3
RECALL_HOT_DAYS = 14

NODE_TYPES = ("directive", "procedure", "material", "topic", "context")
REL_TYPES = ("TRIGGERS", "USES", "ALIAS_OF", "KIND_OF", "ABOUT", "IN_CONTEXT", "RELATED")
COUNTED_RELS = ("TRIGGERS", "USES", "ABOUT", "IN_CONTEXT", "RELATED")
SENSES = ("topic", "context", "related")
REL_ENDS: dict[str, tuple[tuple[str, str], ...]] = {
    "TRIGGERS": (("directive", "procedure"),),
    "USES": (("procedure", "material"),),
    "ALIAS_OF": (("directive", "directive"), ("topic", "topic"), ("context", "context")),
    "KIND_OF": (
        ("directive", "directive"),
        ("procedure", "procedure"),
        ("topic", "topic"),
        ("context", "context"),
    ),
    "ABOUT": (("directive", "topic"), ("procedure", "topic"), ("material", "topic")),
    "IN_CONTEXT": (("directive", "context"), ("procedure", "context"), ("material", "context")),
    "RELATED": (
        ("directive", "directive"),
        ("directive", "procedure"),
        ("directive", "material"),
        ("procedure", "procedure"),
        ("procedure", "material"),
        ("material", "material"),
        ("topic", "topic"),
        ("context", "context"),
        ("topic", "context"),
    ),
}
LABEL = {
    "directive": "Directive",
    "procedure": "Procedure",
    "material": "Material",
    "topic": "Topic",
    "context": "Context",
}
SOURCES = ("hook", "cli", "mcp", "agent", "human", "auto")


def _norm(title: str) -> str:
    return " ".join(title.strip().lower().split())


def _ws(workspace: str | None, *, global_scope: bool = False) -> str:
    if global_scope or not workspace:
        return ""
    return normalize_root(workspace)


def _exec(conn: Any, query: str, params: dict[str, Any] | None = None) -> tuple[list[str], list[list]]:
    result = conn.execute(query, params) if params is not None else conn.execute(query)
    if isinstance(result, list):
        if not result:
            return [], []
        result = result[-1]
    closer = getattr(result, "close", None)
    try:
        names = result.get_column_names()
        rows = result.get_all()
        return list(names), list(rows)
    finally:
        if callable(closer):
            closer()


def _table_names(conn: Any) -> set[str]:
    _names, rows = _exec(conn, "CALL show_tables() RETURN *")
    found: set[str] = set()
    for row in rows:
        if len(row) >= 2 and row[1]:
            found.add(str(row[1]))
    return found


def _cypher_statements(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.split(";"):
        stmt = "\n".join(
            line.split("//", 1)[0].split("--", 1)[0] for line in raw.splitlines()
        ).strip()
        if stmt:
            out.append(stmt)
    return out


def _ensure_graph_schema(conn: Any) -> None:
    for stmt in _cypher_statements(graph_schema_path().read_text(encoding="utf-8")):
        try:
            _exec(conn, stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg:
                continue
            raise
    for table, col in (
        ("TRIGGERS", "sense"),
        ("TRIGGERS", "label"),
        ("USES", "sense"),
        ("USES", "label"),
    ):
        try:
            _exec(conn, f"ALTER TABLE {table} ADD {col} STRING")
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "already has" in msg:
                continue
            raise


@contextmanager
def _graph(*, read_only: bool = False) -> Iterator[Any]:
    path = map_path()
    if not path.exists() and legacy_map_path().exists():
        path = legacy_map_path()
    if read_only and not path.exists():
        yield None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    database, conn = ladybug_connect(path, read_only=read_only)
    try:
        if not read_only:
            _ensure_graph_schema(conn)
        yield conn
    finally:
        conn.close()
        database.close()


def _sync_ontology_fts(sql: Any, node_id: str, title: str, normalized: str) -> None:
    sql.execute("DELETE FROM ontology_nodes_fts WHERE node_id = ?", (node_id,))
    sql.execute(
        "INSERT INTO ontology_nodes_fts(title, normalized, node_id) VALUES (?, ?, ?)",
        (title, normalized, node_id),
    )


def _upsert_sqlite_node(
    sql: Any,
    *,
    node_id: str,
    type_: str,
    title: str,
    pointer: str | None,
    knowledge_id: str | None,
    workspace_root: str,
    extra: dict[str, Any],
) -> None:
    now = now_iso()
    normalized = _norm(title)
    row = sql.execute("SELECT id FROM ontology_nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        sql.execute(
            """
            INSERT INTO ontology_nodes(
              id, type, title, normalized, pointer, knowledge_id, workspace_root,
              extra_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                type_,
                title,
                normalized,
                pointer,
                knowledge_id,
                workspace_root or None,
                dumps(extra),
                now,
                now,
            ),
        )
    else:
        sql.execute(
            """
            UPDATE ontology_nodes
            SET title = ?, normalized = ?, pointer = COALESCE(?, pointer),
                knowledge_id = COALESCE(?, knowledge_id), extra_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, normalized, pointer, knowledge_id, dumps(extra), now, node_id),
        )
    _sync_ontology_fts(sql, node_id, title, normalized)


def _find_node(
    sql: Any,
    ref: str,
    type_: str,
    workspace: str | None,
) -> dict[str, Any] | None:
    if looks_like_id(ref):
        row = sql.execute(
            "SELECT * FROM ontology_nodes WHERE id = ? AND type = ?",
            (ref.strip(), type_),
        ).fetchone()
        return row_dict(row) if row else None
    normalized = _norm(ref)
    ws = _ws(workspace)
    rows = sql.execute(
        """
        SELECT * FROM ontology_nodes
        WHERE type = ? AND normalized = ?
        ORDER BY CASE
          WHEN workspace_root IS NULL OR workspace_root = '' THEN 1
          ELSE 0
        END, updated_at DESC
        """,
        (type_, normalized),
    ).fetchall()
    picked = None
    for row in rows:
        root = row["workspace_root"] or ""
        if ws and workspace_matches([root] if root else [""], workspace):
            return row_dict(row)
        if not root:
            picked = row_dict(row)
    if picked:
        return picked
    if rows:
        return row_dict(rows[0])
    match = fts_match_arg(ref)
    if match:
        try:
            fts_rows = sql.execute(
                """
                SELECT n.* FROM ontology_nodes_fts f
                JOIN ontology_nodes n ON n.id = f.node_id
                WHERE f MATCH ? AND n.type = ?
                LIMIT 8
                """,
                (match, type_),
            ).fetchall()
        except Exception:
            fts_rows = []
        if fts_rows:
            local = None
            glob = None
            for frow in fts_rows:
                item = row_dict(frow)
                root = item.get("workspace_root") or ""
                if ws and root and workspace_matches([root], workspace):
                    return item
                if not root and glob is None:
                    glob = item
                if local is None:
                    local = item
            return glob or local
    return None


def _merge_label(
    conn: Any,
    type_: str,
    node_id: str,
    title: str,
    *,
    pointer: str | None = None,
    knowledge_id: str | None = None,
    subtype: str | None = None,
    workspace_root: str = "",
) -> None:
    label = LABEL[type_]
    normalized = _norm(title)
    if type_ == "directive":
        _exec(
            conn,
            """
            MERGE (d:Directive {id: $id})
            ON CREATE SET d.title = $title, d.normalized = $normalized, d.workspace_root = $ws
            ON MATCH SET d.title = $title, d.normalized = $normalized
            """,
            {"id": node_id, "title": title, "normalized": normalized, "ws": workspace_root},
        )
    elif type_ == "procedure":
        _exec(
            conn,
            """
            MERGE (p:Procedure {id: $id})
            ON CREATE SET p.title = $title, p.kind = $kind, p.pointer = $pointer, p.workspace_root = $ws
            ON MATCH SET p.title = $title,
              p.kind = CASE WHEN $kind <> '' THEN $kind ELSE p.kind END,
              p.pointer = CASE WHEN $pointer <> '' THEN $pointer ELSE p.pointer END
            """,
            {
                "id": node_id,
                "title": title,
                "kind": subtype or "",
                "pointer": pointer or "",
                "ws": workspace_root,
            },
        )
    elif type_ == "material":
        _exec(
            conn,
            """
            MERGE (m:Material {id: $id})
            ON CREATE SET m.title = $title, m.kind = $kind, m.pointer_uri = $pointer,
              m.knowledge_id = $kid, m.workspace_root = $ws
            ON MATCH SET m.title = $title,
              m.kind = CASE WHEN $kind <> '' THEN $kind ELSE m.kind END,
              m.pointer_uri = CASE WHEN $pointer <> '' THEN $pointer ELSE m.pointer_uri END,
              m.knowledge_id = CASE WHEN $kid <> '' THEN $kid ELSE m.knowledge_id END
            """,
            {
                "id": node_id,
                "title": title,
                "kind": subtype or "",
                "pointer": pointer or "",
                "kid": knowledge_id or "",
                "ws": workspace_root,
            },
        )
    else:
        _exec(
            conn,
            f"""
            MERGE (n:{label} {{id: $id}})
            ON CREATE SET n.title = $title, n.normalized = $normalized, n.workspace_root = $ws
            ON MATCH SET n.title = $title, n.normalized = $normalized
            """,
            {"id": node_id, "title": title, "normalized": normalized, "ws": workspace_root},
        )


def _resolve_or_create(
    sql: Any,
    conn: Any,
    ref: str,
    type_: str,
    *,
    workspace: str | None,
    pointer: str | None = None,
    knowledge_id: str | None = None,
    subtype: str | None = None,
    create: bool = True,
) -> dict[str, Any] | None:
    found = _find_node(sql, ref, type_, workspace)
    if found:
        if pointer or knowledge_id or subtype:
            extra = json.loads(found.get("extra_json") or "{}")
            if subtype:
                extra["kind"] = subtype
            _upsert_sqlite_node(
                sql,
                node_id=found["id"],
                type_=type_,
                title=found["title"],
                pointer=pointer or found.get("pointer"),
                knowledge_id=knowledge_id or found.get("knowledge_id"),
                workspace_root=found.get("workspace_root") or _ws(workspace),
                extra=extra,
            )
            _merge_label(
                conn,
                type_,
                found["id"],
                found["title"],
                pointer=pointer or found.get("pointer"),
                knowledge_id=knowledge_id or found.get("knowledge_id"),
                subtype=subtype or extra.get("kind"),
                workspace_root=found.get("workspace_root") or _ws(workspace),
            )
            found = _find_node(sql, found["id"], type_, workspace)
        return found
    if not create:
        return None
    title = ref.strip()
    node_id = knowledge_id if (type_ == "material" and knowledge_id) else new_id()
    extra = {"kind": subtype} if subtype else {}
    ws_root = _ws(workspace)
    _upsert_sqlite_node(
        sql,
        node_id=node_id,
        type_=type_,
        title=title,
        pointer=pointer,
        knowledge_id=knowledge_id,
        workspace_root=ws_root,
        extra=extra,
    )
    _merge_label(
        conn,
        type_,
        node_id,
        title,
        pointer=pointer,
        knowledge_id=knowledge_id,
        subtype=subtype,
        workspace_root=ws_root,
    )
    return _find_node(sql, node_id, type_, workspace)


def _infer_type(sql: Any, ref: str, workspace: str | None, preferred: str | None) -> str | None:
    if preferred:
        return preferred
    if looks_like_id(ref):
        row = sql.execute("SELECT type FROM ontology_nodes WHERE id = ?", (ref.strip(),)).fetchone()
        if row:
            return row["type"]
    for type_ in NODE_TYPES:
        if _find_node(sql, ref, type_, workspace):
            return type_
    return None


def _pick_ends(
    sql: Any,
    from_ref: str,
    to_ref: str,
    rel: str,
    workspace: str | None,
    from_type: str | None,
    to_type: str | None,
) -> tuple[str, str] | None:
    allowed = REL_ENDS[rel]
    inferred_src = _infer_type(sql, from_ref, workspace, from_type)
    inferred_dst = _infer_type(sql, to_ref, workspace, to_type)
    if inferred_src and inferred_dst and (inferred_src, inferred_dst) in allowed:
        return inferred_src, inferred_dst
    if inferred_src:
        for pair in allowed:
            if pair[0] == inferred_src and (inferred_dst is None or pair[1] == inferred_dst):
                return pair
    if inferred_dst:
        for pair in allowed:
            if pair[1] == inferred_dst and (inferred_src is None or pair[0] == inferred_src):
                return pair
    if from_type and to_type and (from_type, to_type) in allowed:
        return from_type, to_type
    return allowed[0]


def _sense_for(rel: str, sense: str | None) -> str:
    if rel == "ABOUT":
        return "topic"
    if rel == "IN_CONTEXT":
        return "context"
    value = (sense or "related").strip().lower()
    if value not in SENSES:
        return "related"
    return value


def _forbidden(conn: Any, from_id: str, to_id: str) -> bool:
    _names, rows = _exec(
        conn,
        """
        MATCH (a)-[f:FORBIDS]->(b)
        WHERE a.id = $from_id AND b.id = $to_id
        RETURN f.reason
        LIMIT 1
        """,
        {"from_id": from_id, "to_id": to_id},
    )
    return bool(rows)


def link(
    from_ref: str,
    to_ref: str,
    *,
    rel: str = "TRIGGERS",
    confirm: bool = False,
    role: str | None = None,
    pointer: str | None = None,
    subtype: str | None = None,
    knowledge_id: str | None = None,
    from_type: str | None = None,
    to_type: str | None = None,
    sense: str | None = None,
    label: str | None = None,
    workspace: str | None = None,
    source: str = "cli",
    at: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    rel = (rel or "TRIGGERS").upper()
    if rel not in REL_TYPES:
        return {"ok": False, "error": "invalid_rel", "rel": rel}
    if not from_ref or not to_ref:
        return {"ok": False, "error": "from and to required"}
    if sense and sense.strip().lower() not in SENSES:
        return {"ok": False, "error": "invalid_sense", "sense": sense}
    sql = sqlite_connect()
    try:
        picked = _pick_ends(sql, from_ref, to_ref, rel, workspace, from_type, to_type)
        if picked is None:
            return {"ok": False, "error": "invalid_type"}
        src_type, dst_type = picked
        if src_type not in NODE_TYPES or dst_type not in NODE_TYPES:
            return {"ok": False, "error": "invalid_type"}
        if (src_type, dst_type) not in REL_ENDS[rel]:
            return {"ok": False, "error": "invalid_type"}
        if source not in SOURCES:
            source = "cli"
        now = at or now_iso()
        status = "confirmed" if confirm else "proposed"
        edge_sense = _sense_for(rel, sense)
        edge_label = (label or "").strip()
        src_label = LABEL[src_type]
        dst_label = LABEL[dst_type]
        with _graph(read_only=False) as conn:
            with write_tx(sql):
                src = _resolve_or_create(
                    sql,
                    conn,
                    from_ref,
                    src_type,
                    workspace=workspace,
                    create=True,
                )
                dst = _resolve_or_create(
                    sql,
                    conn,
                    to_ref,
                    dst_type,
                    workspace=workspace,
                    pointer=pointer,
                    knowledge_id=knowledge_id,
                    subtype=subtype,
                    create=True,
                )
            if not src or not dst:
                return {"ok": False, "error": "node_missing"}
            if _forbidden(conn, src["id"], dst["id"]):
                return {"ok": False, "error": "forbidden", "from": src["id"], "to": dst["id"]}
            if rel in COUNTED_RELS:
                extra_on_create = ""
                extra_set = ""
                params: dict[str, Any] = {
                    "from_id": src["id"],
                    "to_id": dst["id"],
                    "now": now,
                    "status": status,
                    "source": source,
                    "role": role or "",
                    "sense": edge_sense,
                    "label": edge_label,
                }
                _names, existed = _exec(
                    conn,
                    f"""
                    MATCH (a:{src_label} {{id: $from_id}})-[t:{rel}]->(b:{dst_label} {{id: $to_id}})
                    RETURN t.count, t.status, t.last_at
                    """,
                    {"from_id": src["id"], "to_id": dst["id"]},
                )
                if source == "auto" and existed:
                    edge = {
                        "count": existed[0][0],
                        "status": existed[0][1],
                        "last_at": existed[0][2],
                        "sense": edge_sense,
                        "label": edge_label,
                    }
                    return {
                        "ok": True,
                        "rel": rel,
                        "from": {"id": src["id"], "type": src_type, "title": src["title"]},
                        "to": {"id": dst["id"], "type": dst_type, "title": dst["title"]},
                        "edge": edge,
                        "source": source,
                    }
                if rel == "USES":
                    extra_on_create = ", t.role = $role"
                    extra_set = ", t.role = CASE WHEN $role <> '' THEN $role ELSE t.role END"
                if rel != "ABOUT" and rel != "IN_CONTEXT":
                    extra_on_create += ", t.sense = $sense"
                    extra_set += ", t.sense = CASE WHEN $sense <> '' THEN $sense ELSE t.sense END"
                extra_on_create += ", t.label = $label"
                extra_set += ", t.label = CASE WHEN $label <> '' THEN $label ELSE t.label END"
                confirm_set = ", t.status = CASE WHEN $status = 'confirmed' THEN 'confirmed' ELSE t.status END"
                _exec(
                    conn,
                    f"""
                    MATCH (a:{src_label} {{id: $from_id}}), (b:{dst_label} {{id: $to_id}})
                    MERGE (a)-[t:{rel}]->(b)
                    ON CREATE SET t.count = 1, t.first_at = $now, t.last_at = $now,
                      t.status = $status, t.source = $source{extra_on_create}
                    ON MATCH SET t.count = t.count + 1, t.last_at = $now{confirm_set}{extra_set}
                    """,
                    params,
                )
            else:
                _exec(
                    conn,
                    f"""
                    MATCH (a:{src_label} {{id: $from_id}}), (b:{dst_label} {{id: $to_id}})
                    MERGE (a)-[:{rel}]->(b)
                    """,
                    {"from_id": src["id"], "to_id": dst["id"]},
                )
            _names, rows = _exec(
                conn,
                f"""
                MATCH (a:{src_label} {{id: $from_id}})-[t:{rel}]->(b:{dst_label} {{id: $to_id}})
                RETURN t.count, t.status, t.last_at
                """,
                {"from_id": src["id"], "to_id": dst["id"]},
            ) if rel in COUNTED_RELS else ([], [])
            edge = {"count": None, "status": status, "last_at": now, "sense": edge_sense, "label": edge_label}
            if rows:
                edge["count"] = rows[0][0]
                edge["status"] = rows[0][1]
                edge["last_at"] = rows[0][2]
            return {
                "ok": True,
                "rel": rel,
                "from": {"id": src["id"], "type": src_type, "title": src["title"]},
                "to": {"id": dst["id"], "type": dst_type, "title": dst["title"]},
                "edge": edge,
                "source": source,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        sql.close()


def forbid(
    from_ref: str,
    to_ref: str,
    *,
    reason: str,
    workspace: str | None = None,
    source: str = "cli",
    from_type: str | None = None,
    to_type: str | None = None,
) -> dict[str, Any]:
    if not from_ref or not to_ref:
        return {"ok": False, "error": "from and to required"}
    if not reason or not reason.strip():
        return {"ok": False, "error": "reason required"}
    sql = sqlite_connect()
    try:
        with _graph(read_only=False) as conn:
            src = None
            dst = None
            types = [from_type] if from_type else list(NODE_TYPES)
            to_types = [to_type] if to_type else list(NODE_TYPES)
            for t in types:
                src = _find_node(sql, from_ref, t, workspace)
                if src:
                    break
            for t in to_types:
                dst = _find_node(sql, to_ref, t, workspace)
                if dst:
                    break
            if not src or not dst:
                return {"ok": False, "error": "node_missing"}
            src_label = LABEL[src["type"]]
            dst_label = LABEL[dst["type"]]
            _exec(
                conn,
                f"""
                MATCH (a:{src_label} {{id: $from_id}}), (b:{dst_label} {{id: $to_id}})
                MERGE (a)-[f:FORBIDS]->(b)
                ON CREATE SET f.reason = $reason, f.created_at = $now, f.created_by = $who
                ON MATCH SET f.reason = $reason
                """,
                {
                    "from_id": src["id"],
                    "to_id": dst["id"],
                    "reason": reason.strip(),
                    "now": now_iso(),
                    "who": source,
                },
            )
            pair = (src["type"], dst["type"])
            deny_rel = None
            if pair == ("directive", "procedure"):
                deny_rel = "TRIGGERS"
            elif pair == ("procedure", "material"):
                deny_rel = "USES"
            if deny_rel:
                _exec(
                    conn,
                    f"""
                    MATCH (a:{src_label} {{id: $from_id}})-[t:{deny_rel}]->(b:{dst_label} {{id: $to_id}})
                    SET t.status = 'denied'
                    """,
                    {"from_id": src["id"], "to_id": dst["id"]},
                )
            return {
                "ok": True,
                "from": {"id": src["id"], "type": src["type"], "title": src["title"]},
                "to": {"id": dst["id"], "type": dst["type"], "title": dst["title"]},
                "reason": reason.strip(),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        sql.close()


def confirm(
    from_ref: str,
    to_ref: str,
    *,
    rel: str = "TRIGGERS",
    workspace: str | None = None,
) -> dict[str, Any]:
    rel = (rel or "TRIGGERS").upper()
    if rel not in COUNTED_RELS:
        return {"ok": False, "error": "invalid_rel", "rel": rel}
    sql = sqlite_connect()
    try:
        with _graph(read_only=False) as conn:
            missing_nodes = True
            for src_type, dst_type in REL_ENDS[rel]:
                src = _find_node(sql, from_ref, src_type, workspace)
                dst = _find_node(sql, to_ref, dst_type, workspace)
                if not src or not dst:
                    continue
                missing_nodes = False
                if _forbidden(conn, src["id"], dst["id"]):
                    return {"ok": False, "error": "forbidden"}
                src_label = LABEL[src_type]
                dst_label = LABEL[dst_type]
                _names, rows = _exec(
                    conn,
                    f"""
                    MATCH (a:{src_label} {{id: $from_id}})-[t:{rel}]->(b:{dst_label} {{id: $to_id}})
                    SET t.status = 'confirmed'
                    RETURN t.status
                    """,
                    {"from_id": src["id"], "to_id": dst["id"]},
                )
                if not rows:
                    continue
                return {
                    "ok": True,
                    "rel": rel,
                    "from": {"id": src["id"], "type": src_type, "title": src["title"]},
                    "to": {"id": dst["id"], "type": dst_type, "title": dst["title"]},
                    "status": "confirmed",
                }
            if missing_nodes:
                return {"ok": False, "error": "node_missing"}
            return {"ok": False, "error": "edge_missing", "rel": rel}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        sql.close()


def _prefer_workspace_ids(
    sql: Any,
    ids: list[str],
    workspace: str | None,
) -> list[str]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = sql.execute(
        f"SELECT id, workspace_root FROM ontology_nodes WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    roots = {str(row["id"]): row["workspace_root"] or "" for row in rows}
    local: list[str] = []
    glob: list[str] = []
    other: list[str] = []
    seen: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            continue
        seen.add(node_id)
        root = roots.get(node_id, "")
        if root and workspace and workspace_matches([root], workspace):
            local.append(node_id)
        elif not root:
            glob.append(node_id)
        else:
            other.append(node_id)
    if local:
        return local[:20]
    if glob:
        return glob[:20]
    return other[:20]


def _node_ids(sql: Any, query: str, type_: str, workspace: str | None) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(node_id: str) -> None:
        if node_id not in seen:
            seen.add(node_id)
            ids.append(node_id)

    exact = _find_node(sql, query, type_, workspace)
    if exact:
        add(exact["id"])
    match = fts_match_arg(query)
    if match:
        try:
            rows = sql.execute(
                """
                SELECT n.id FROM ontology_nodes_fts f
                JOIN ontology_nodes n ON n.id = f.node_id
                WHERE f MATCH ? AND n.type = ?
                LIMIT 20
                """,
                (match, type_),
            ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            add(row["id"])
    like = f"%{query.strip()}%"
    for row in sql.execute(
        """
        SELECT id FROM ontology_nodes
        WHERE type = ? AND (title LIKE ? OR normalized LIKE ?)
        LIMIT 20
        """,
        (type_, like, f"%{_norm(query)}%"),
    ).fetchall():
        add(row["id"])
    seen_tokens: set[str] = set()
    for token in [*query.split(), *useful_query_tokens(query)]:
        if len(token) < 2 or token in seen_tokens:
            continue
        seen_tokens.add(token)
        token_like = f"%{token}%"
        for row in sql.execute(
            """
            SELECT id FROM ontology_nodes
            WHERE type = ? AND (title LIKE ? OR normalized LIKE ?)
            LIMIT 10
            """,
            (type_, token_like, f"%{_norm(token)}%"),
        ).fetchall():
            add(row["id"])
            if len(ids) >= 40:
                break
        if len(ids) >= 40:
            break
    return _prefer_workspace_ids(sql, ids, workspace)


def _canonical_ids(conn: Any, ids: list[str]) -> list[str]:
    if not ids:
        return []
    _names, rows = _exec(
        conn,
        """
        MATCH (x:Directive)
        WHERE x.id IN $ids
        OPTIONAL MATCH (x)-[:ALIAS_OF]->(canon:Directive)
        RETURN DISTINCT coalesce(canon.id, x.id)
        """,
        {"ids": ids},
    )
    out = [str(r[0]) for r in rows if r and r[0]]
    return out or ids


def _visible_edge(
    status: str | None,
    count: Any = None,
    last_at: Any = None,
) -> bool:
    if status == "denied" or not status:
        return False
    if status == "confirmed":
        return True
    if status != "proposed":
        return False
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    if n < RECALL_HOT_COUNT:
        return False
    ts = parse_iso(str(last_at) if last_at is not None else None)
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts <= timedelta(days=RECALL_HOT_DAYS)


def _knowledge_body(sql: Any, knowledge_id: str | None) -> str:
    if not knowledge_id:
        return ""
    row = sql.execute("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,)).fetchone()
    if row is None:
        return ""
    expires = row["expires_at"]
    if expires and expires <= now_iso():
        return ""
    body = row["body"] or ""
    if not body and row["blob_sha"]:
        body = blobs.get_blob_text(row["blob_sha"]) or ""
    return body


def _add_procedure(
    procedures: dict[str, dict[str, Any]],
    *,
    p_id: Any,
    p_title: Any,
    p_kind: Any,
    p_pointer: Any,
    p_ws: Any,
    via: dict[str, Any],
) -> dict[str, Any]:
    proc = procedures.get(str(p_id))
    if proc is None:
        proc = {
            "id": p_id,
            "title": p_title,
            "kind": p_kind,
            "pointer": p_pointer,
            "workspace_root": p_ws,
            "triggers": via,
            "via": [via],
            "materials": [],
        }
        procedures[str(p_id)] = proc
    else:
        proc.setdefault("via", []).append(via)
    return proc


def recall(
    query: str,
    *,
    workspace: str | None = None,
    budget_tokens: int = DEFAULT_LOAD_BUDGET,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    tracking_id = request_id
    owns_request = track and tracking_id is None
    if owns_request:
        tracking_id = retrieval.start_request(
            "map_recall",
            query=query,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
        )
    try:
        result = _recall_impl(query, workspace=workspace, budget_tokens=budget_tokens)
        if tracking_id:
            normalized: list[dict[str, Any]] = []
            for item in result.get("topics") or []:
                normalized.append(
                    {
                        "source_db": "map",
                        "entity_type": "topic",
                        "entity_id": item.get("id"),
                        "title": item.get("title"),
                        "rank": len(normalized) + 1,
                    }
                )
            for item in result.get("contexts") or []:
                normalized.append(
                    {
                        "source_db": "map",
                        "entity_type": "context",
                        "entity_id": item.get("id"),
                        "title": item.get("title"),
                        "rank": len(normalized) + 1,
                    }
                )
            for proc in result.get("procedures") or []:
                normalized.append(
                    {
                        "source_db": "map",
                        "entity_type": "procedure",
                        "entity_id": proc.get("id"),
                        "title": proc.get("title"),
                        "rank": len(normalized) + 1,
                    }
                )
                for mat in proc.get("materials") or []:
                    normalized.append(
                        {
                            "source_db": "sqlite" if mat.get("knowledge_id") else "map",
                            "entity_type": "material",
                            "entity_id": mat.get("knowledge_id") or mat.get("id"),
                            "title": mat.get("title"),
                            "rank": len(normalized) + 1,
                            "metadata": {"role": mat.get("role")},
                        }
                    )
            retrieval.record_results(tracking_id, normalized)
            if owns_request:
                retrieval.finish_request(tracking_id)
            result["retrieval_request_id"] = tracking_id
        return result
    except Exception:
        if owns_request and tracking_id:
            retrieval.fail_request(tracking_id)
        raise


def _recall_impl(
    query: str,
    *,
    workspace: str | None = None,
    budget_tokens: int = DEFAULT_LOAD_BUDGET,
) -> dict[str, Any]:
    if not query or not query.strip():
        return {"ok": False, "error": "query required"}
    workspace = workspace or os.getcwd()
    path = map_path()
    sql = sqlite_connect()
    empty = {
        "ok": True,
        "query": query,
        "directives": [],
        "topics": [],
        "contexts": [],
        "procedures": [],
        "tokens_used": 0,
        "budget": budget_tokens,
    }
    try:
        directive_ids = _node_ids(sql, query, "directive", workspace)
        topic_ids = _node_ids(sql, query, "topic", workspace)
        context_ids = _node_ids(sql, query, "context", workspace)
        if not path.exists() or not (directive_ids or topic_ids or context_ids):
            return empty
        with _graph(read_only=True) as conn:
            if conn is None:
                return empty
            canon = _canonical_ids(conn, directive_ids) if directive_ids else []
            if topic_ids:
                _n, extra_dirs = _exec(
                    conn,
                    """
                    MATCH (d:Directive)-[a:ABOUT]->(t:Topic)
                    WHERE t.id IN $ids AND a.status = 'confirmed'
                    RETURN DISTINCT d.id
                    """,
                    {"ids": topic_ids},
                )
                for row in extra_dirs:
                    if row[0] and str(row[0]) not in canon:
                        canon.append(str(row[0]))
                _n, ctx_dirs = _exec(
                    conn,
                    """
                    MATCH (d:Directive)-[r:RELATED]->(t:Topic)
                    WHERE t.id IN $ids AND r.status = 'confirmed'
                    RETURN DISTINCT d.id
                    """,
                    {"ids": topic_ids},
                )
                for row in ctx_dirs:
                    if row[0] and str(row[0]) not in canon:
                        canon.append(str(row[0]))
            if context_ids:
                _n, extra_dirs = _exec(
                    conn,
                    """
                    MATCH (d:Directive)-[a:IN_CONTEXT]->(c:Context)
                    WHERE c.id IN $ids AND a.status = 'confirmed'
                    RETURN DISTINCT d.id
                    """,
                    {"ids": context_ids},
                )
                for row in extra_dirs:
                    if row[0] and str(row[0]) not in canon:
                        canon.append(str(row[0]))
            procedures: dict[str, dict[str, Any]] = {}
            topics_out: list[dict[str, Any]] = []
            contexts_out: list[dict[str, Any]] = []
            if canon:
                _names, rows = _exec(
                    conn,
                    """
                    MATCH (d:Directive)-[t:TRIGGERS]->(p:Procedure)
                    WHERE d.id IN $ids
                      AND t.status <> 'denied'
                      AND NOT EXISTS { MATCH (d)-[:FORBIDS]->(p) }
                    OPTIONAL MATCH (p)-[u:USES]->(m:Material)
                    WHERE u.status <> 'denied' AND NOT EXISTS { MATCH (p)-[:FORBIDS]->(m) }
                    RETURN d.id, d.title, p.id, p.title, p.kind, p.pointer, p.workspace_root,
                           t.count, t.last_at, t.status, t.sense, t.label,
                           m.id, m.title, m.kind, m.pointer_uri, m.knowledge_id, u.role,
                           u.status, u.count, u.last_at
                    ORDER BY t.last_at DESC, t.count DESC
                    """,
                    {"ids": canon},
                )
                for row in rows:
                    (
                        d_id, d_title, p_id, p_title, p_kind, p_pointer, p_ws,
                        t_count, t_last, t_status, t_sense, t_label,
                        m_id, m_title, m_kind, m_ptr, m_kid, u_role,
                        u_status, u_count, u_last,
                    ) = (row + [None] * 21)[:21]
                    if not _visible_edge(t_status, t_count, t_last):
                        continue
                    via = {
                        "rel": "TRIGGERS",
                        "sense": t_sense or "related",
                        "label": t_label,
                        "count": t_count,
                        "last_at": t_last,
                        "status": t_status,
                        "directive_id": d_id,
                        "directive_title": d_title,
                    }
                    proc = _add_procedure(
                        procedures,
                        p_id=p_id, p_title=p_title, p_kind=p_kind,
                        p_pointer=p_pointer, p_ws=p_ws, via=via,
                    )
                    if m_id and _visible_edge(u_status, u_count, u_last):
                        already = {m["id"] for m in proc["materials"]}
                        if m_id not in already:
                            proc["materials"].append(
                                {
                                    "id": m_id,
                                    "title": m_title,
                                    "kind": m_kind,
                                    "pointer_uri": m_ptr,
                                    "knowledge_id": m_kid or None,
                                    "role": u_role,
                                    "body": None,
                                }
                            )
            if topic_ids:
                _n, about_rows = _exec(
                    conn,
                    """
                    MATCH (p:Procedure)-[a:ABOUT]->(t:Topic)
                    WHERE t.id IN $ids AND a.status <> 'denied'
                    RETURN p.id, p.title, p.kind, p.pointer, p.workspace_root,
                           a.count, a.last_at, a.status, a.label, t.id, t.title
                    """,
                    {"ids": topic_ids},
                )
                for row in about_rows:
                    (
                        p_id, p_title, p_kind, p_pointer, p_ws,
                        a_count, a_last, a_status, a_label, t_id, t_title,
                    ) = (row + [None] * 11)[:11]
                    if not _visible_edge(a_status, a_count, a_last):
                        continue
                    via = {
                        "rel": "ABOUT",
                        "sense": "topic",
                        "label": a_label,
                        "count": a_count,
                        "last_at": a_last,
                        "status": a_status,
                        "topic_id": t_id,
                        "topic_title": t_title,
                    }
                    _add_procedure(
                        procedures,
                        p_id=p_id, p_title=p_title, p_kind=p_kind,
                        p_pointer=p_pointer, p_ws=p_ws, via=via,
                    )
                _n, topic_rows = _exec(
                    conn,
                    "MATCH (t:Topic) WHERE t.id IN $ids RETURN t.id, t.title",
                    {"ids": topic_ids},
                )
                topics_out = [{"id": r[0], "title": r[1], "sense": "topic"} for r in topic_rows]
            if context_ids:
                _n, ctx_rows = _exec(
                    conn,
                    """
                    MATCH (p:Procedure)-[a:IN_CONTEXT]->(c:Context)
                    WHERE c.id IN $ids AND a.status <> 'denied'
                    RETURN p.id, p.title, p.kind, p.pointer, p.workspace_root,
                           a.count, a.last_at, a.status, a.label, c.id, c.title
                    """,
                    {"ids": context_ids},
                )
                for row in ctx_rows:
                    (
                        p_id, p_title, p_kind, p_pointer, p_ws,
                        a_count, a_last, a_status, a_label, c_id, c_title,
                    ) = (row + [None] * 11)[:11]
                    if not _visible_edge(a_status, a_count, a_last):
                        continue
                    via = {
                        "rel": "IN_CONTEXT",
                        "sense": "context",
                        "label": a_label,
                        "count": a_count,
                        "last_at": a_last,
                        "status": a_status,
                        "context_id": c_id,
                        "context_title": c_title,
                    }
                    _add_procedure(
                        procedures,
                        p_id=p_id, p_title=p_title, p_kind=p_kind,
                        p_pointer=p_pointer, p_ws=p_ws, via=via,
                    )
                _n, c_rows = _exec(
                    conn,
                    "MATCH (c:Context) WHERE c.id IN $ids RETURN c.id, c.title",
                    {"ids": context_ids},
                )
                contexts_out = [{"id": r[0], "title": r[1], "sense": "context"} for r in c_rows]
            if canon:
                _n, rel_rows = _exec(
                    conn,
                    """
                    MATCH (d:Directive)-[r:RELATED]->(p:Procedure)
                    WHERE d.id IN $ids AND r.status <> 'denied'
                    RETURN p.id, p.title, p.kind, p.pointer, p.workspace_root,
                           r.count, r.last_at, r.status, r.sense, r.label, d.id, d.title
                    """,
                    {"ids": canon},
                )
                for row in rel_rows:
                    (
                        p_id, p_title, p_kind, p_pointer, p_ws,
                        r_count, r_last, r_status, r_sense, r_label, d_id, d_title,
                    ) = (row + [None] * 12)[:12]
                    if not _visible_edge(r_status, r_count, r_last):
                        continue
                    via = {
                        "rel": "RELATED",
                        "sense": r_sense or "related",
                        "label": r_label,
                        "count": r_count,
                        "last_at": r_last,
                        "status": r_status,
                        "directive_id": d_id,
                        "directive_title": d_title,
                    }
                    _add_procedure(
                        procedures,
                        p_id=p_id, p_title=p_title, p_kind=p_kind,
                        p_pointer=p_pointer, p_ws=p_ws, via=via,
                    )
        ranked = list(procedures.values())
        ws_norm = normalize_root(workspace)

        def _edge_sort(item: dict[str, Any]) -> tuple:
            trig = item.get("triggers") or {}
            confirmed = trig.get("status") == "confirmed"
            last = trig.get("last_at") or ""
            count = int(trig.get("count") or 0)
            return (confirmed, last, count)

        local = [p for p in ranked if p.get("workspace_root") and workspace_matches([p["workspace_root"]], ws_norm)]
        other = [p for p in ranked if p not in local]
        local.sort(key=_edge_sort, reverse=True)
        other.sort(key=_edge_sort, reverse=True)
        ordered = local if local else other
        tokens = 0
        out_procs: list[dict[str, Any]] = []
        for proc in ordered:
            for mat in proc["materials"]:
                body = _knowledge_body(sql, mat.get("knowledge_id"))
                if body:
                    cost = estimate_tokens(body)
                    if tokens + cost > budget_tokens:
                        mat["body"] = None
                        mat["omitted"] = True
                    else:
                        mat["body"] = truncate(body, 1200)
                        tokens += estimate_tokens(mat["body"] or "")
                else:
                    mat["body"] = None
            out_procs.append(proc)
        return {
            "ok": True,
            "query": query,
            "directives": canon,
            "topics": topics_out,
            "contexts": contexts_out,
            "procedures": out_procs,
            "tokens_used": tokens,
            "budget": budget_tokens,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        sql.close()


def map_status() -> dict[str, Any]:
    path = map_path()
    sql = sqlite_connect()
    try:
        sqlite_counts = {
            "nodes": sql.execute("SELECT count(*) FROM ontology_nodes").fetchone()[0],
        }
        materials = [
            row_dict(r)
            for r in sql.execute(
                """
                SELECT id, type, title, pointer, knowledge_id, workspace_root
                FROM ontology_nodes WHERE type = 'material' LIMIT 20
                """
            ).fetchall()
        ]
        if not path.exists():
            return {"ok": True, "graph": False, "sqlite": sqlite_counts, "materials": materials}
        with _graph(read_only=True) as conn:
            if conn is None:
                return {"ok": True, "graph": False, "sqlite": sqlite_counts, "materials": materials}
            def _count(q: str) -> int:
                _n, rows = _exec(conn, q)
                return int(rows[0][0]) if rows else 0

            graph_counts = {
                "directive": _count("MATCH (d:Directive) RETURN count(*)"),
                "procedure": _count("MATCH (p:Procedure) RETURN count(*)"),
                "material": _count("MATCH (m:Material) RETURN count(*)"),
                "topic": _count("MATCH (t:Topic) RETURN count(*)"),
                "context": _count("MATCH (c:Context) RETURN count(*)"),
                "triggers_proposed": _count(
                    "MATCH ()-[t:TRIGGERS]->() WHERE t.status = 'proposed' RETURN count(*)"
                ),
                "triggers_confirmed": _count(
                    "MATCH ()-[t:TRIGGERS]->() WHERE t.status = 'confirmed' RETURN count(*)"
                ),
                "forbids": _count("MATCH ()-[f:FORBIDS]->() RETURN count(*)"),
            }
            _n, mat_rows = _exec(
                conn,
                """
                MATCH (m:Material)
                RETURN m.id, m.title, m.kind, m.pointer_uri, m.knowledge_id
                LIMIT 20
                """,
            )
            graph_materials = [
                {
                    "id": r[0],
                    "title": r[1],
                    "kind": r[2],
                    "pointer_uri": r[3],
                    "knowledge_id": r[4],
                }
                for r in mat_rows
            ]
        return {
            "ok": True,
            "graph": True,
            "sqlite": sqlite_counts,
            "counts": graph_counts,
            "materials": materials,
            "graph_materials": graph_materials,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        sql.close()
