from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import sqlite3
import sys

from lucid_memories.core.session_metadata import enrich_session_detail, enrich_sessions

from .db import DatabaseInfo, one, rows, table_columns, table_exists

EXPECTED_HOOKS = (
    "beforeSubmitPrompt",
    "postToolUse",
    "postToolUseFailure",
    "afterShellExecution",
    "afterMCPExecution",
    "afterFileEdit",
    "afterAgentResponse",
    "afterAgentThought",
    "stop",
    "sessionEnd",
)

ARTIFACT_MESSAGES = {
    "blob": "lucid-memories が本文を保管しています。",
    "external": "Workspace / Repository が正本です。lucid-memories には索引と preview だけがあります。",
    "preview": "本文は preview のみ残っています。",
    "unavailable": "本文は参照できません。",
}

BODY_PREVIEW_LIMIT = 400
DETAIL_TEXT_LIMIT = 4000
from .models import CollectionQuery, Page


TABLES = (
    "sessions",
    "jobs",
    "job_events",
    "knowledge",
    "packs",
    "pack_items",
    "compact_events",
    "blobs",
    "retrieval_logs",
    "turn_state",
    "embeddings",
    "usage_events",
    "conversation_events",
    "artifacts",
    "memory_tasks",
    "memory_candidates",
    "ontology_nodes",
    "persona_candidates",
    "persona_revisions",
    "notices",
    "leases",
)


class DashboardRepository:
    def __init__(self, connection: sqlite3.Connection, info: DatabaseInfo):
        self.connection = connection
        self.info = info

    def overview(self) -> dict:
        counts = {
            "sessions": self.count("sessions"),
            "live_sessions": self.count("sessions", "status IN ('active', 'idle')"),
            "jobs": self.count("jobs"),
            "open_jobs": self.count("jobs", "status IN ('pending', 'running', 'blocked')"),
            "knowledge": self.count("knowledge"),
            "packs": self.count("packs"),
            "compactions": self.count("compact_events"),
            "retrievals": self.count("retrieval_logs"),
        }
        models = (
            rows(
                self.connection,
                """
                SELECT COALESCE(NULLIF(model, ''), '未記録') AS model, COUNT(*) AS sessions
                FROM sessions
                GROUP BY COALESCE(NULLIF(model, ''), '未記録')
                ORDER BY sessions DESC, model
                """,
            )
            if self.has("sessions")
            else []
        )
        recent_sessions = self.session_rows(
            CollectionQuery(page=Page(1, 6)),
            recent=True,
        )["data"]
        usage = self.usage_summary()
        mcp = self.mcp_summary()
        index = {
            "events": self.count("conversation_events"),
            "artifacts": self.count("artifacts"),
            "available": self.has("conversation_events") or self.has("artifacts"),
        }
        return {
            "database": self.info.path.name,
            "schema_version": self.info.schema_version,
            "database_updated_at": self.info.modified_at,
            "counts": counts,
            "models": models,
            "recent_sessions": recent_sessions,
            "usage": usage,
            "mcp": mcp,
            "index": index,
            "memory": self.memory_status(),
            "embeddings": self.embedding_status(),
            "map": self.map_status(),
            "storage": self.storage_summary(),
            "retrieval": self.retrieval_summary(),
            "hooks": self.hook_coverage(),
            "persona": self.persona_status(),
            "context": self.context_summary(),
            "capabilities": self.capabilities(),
        }

    def usage_summary(self) -> dict:
        if not self.has("usage_events"):
            return {
                "status": "unavailable",
                "available": False,
                "events": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0,
                "message": "usage_events が利用できないため、Usage を集計できません。",
            }
        columns = table_columns(self.connection, "usage_events")
        expressions = {
            key: optional_column("u", column, columns, "0")
            for key, column in (
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
                ("cache_read_tokens", "cache_read_tokens"),
                ("cache_write_tokens", "cache_write_tokens"),
                ("cost_usd", "cost_usd"),
            )
        }
        source_filter = usage_source_sql("u", columns, mcp=False)
        result = one(
            self.connection,
            f"""
            SELECT COUNT(*) AS events,
                   COALESCE(SUM({expressions["input_tokens"]}), 0) AS input_tokens,
                   COALESCE(SUM({expressions["output_tokens"]}), 0) AS output_tokens,
                   COALESCE(SUM({expressions["cache_read_tokens"]}), 0) AS cache_read_tokens,
                   COALESCE(SUM({expressions["cache_write_tokens"]}), 0) AS cache_write_tokens,
                   COALESCE(SUM({expressions["cost_usd"]}), 0) AS cost_usd
            FROM usage_events u
            WHERE {source_filter}
            """,
        )
        result["status"] = "available" if result["events"] else "no_events"
        result["available"] = bool(result["events"])
        result["message"] = (
            "Usage イベントを収集しています。"
            if result["events"]
            else "usage_events は存在しますが、まだイベントがありません。"
        )
        return result

    def mcp_summary(self) -> dict:
        unavailable = {
            "status": "unavailable",
            "available": False,
            "events": 0,
            "tokens": 0,
            "by_tool": [],
            "stats": {"sample_size": 0},
            "message": "usage_events が利用できないため、MCP 利用量を集計できません。",
        }
        if not self.has("usage_events"):
            return unavailable
        columns = table_columns(self.connection, "usage_events")
        if "event_type" not in columns:
            return {
                **unavailable,
                "message": "MCP 利用量の種別列が無いため、集計できません。",
            }
        token_expr = mcp_token_sql("u", columns)
        result = one(
            self.connection,
            f"""
            SELECT COUNT(*) AS events,
                   COALESCE(SUM({token_expr}), 0) AS tokens
            FROM usage_events u
            WHERE {usage_source_sql("u", columns, mcp=True)}
            """,
        )
        tool_expr = mcp_tool_sql("u", columns)
        by_tool = rows(
            self.connection,
            f"""
            SELECT {tool_expr} AS tool, COUNT(*) AS events,
                   COALESCE(SUM({token_expr}), 0) AS tokens
            FROM usage_events u
            WHERE {usage_source_sql("u", columns, mcp=True)}
            GROUP BY {tool_expr}
            ORDER BY tokens DESC, tool
            """,
        )
        samples = rows(
            self.connection,
            f"""
            SELECT {tool_expr} AS tool, {token_expr} AS tokens
            FROM usage_events u
            WHERE {usage_source_sql("u", columns, mcp=True)}
            """,
        )
        token_values = [int(item["tokens"] or 0) for item in samples]
        by_tool_tokens: dict[str, list[int]] = {}
        for item in samples:
            tool = str(item["tool"] or "unknown")
            by_tool_tokens.setdefault(tool, []).append(int(item["tokens"] or 0))
        for item in by_tool:
            tool = str(item["tool"] or "unknown")
            item["stats"] = token_distribution(by_tool_tokens.get(tool, []))
        events = int(result["events"] or 0)
        stats = token_distribution(token_values)
        result["status"] = "available" if events else "no_events"
        result["available"] = bool(events)
        result["by_tool"] = by_tool
        result["stats"] = stats
        result["message"] = mcp_summary_message(events, stats)
        return result

    def memory_status(self) -> dict:
        knowledge = self.grouped_status("knowledge", "memory_status")
        tasks = self.grouped_status("memory_tasks", "status")
        candidates = self.grouped_status("memory_candidates", "status")
        return {
            "available": any(
                item["available"] for item in (knowledge, tasks, candidates)
            ),
            "knowledge": knowledge,
            "tasks": tasks,
            "candidates": candidates,
        }

    def grouped_status(self, table: str, column: str) -> dict:
        if not self.has(table) or column not in table_columns(self.connection, table):
            return {"available": False, "total": 0, "counts": {}}
        grouped = rows(
            self.connection,
            f"""
            SELECT COALESCE(NULLIF({column}, ''), 'unknown') AS status, COUNT(*) AS count
            FROM {table}
            GROUP BY COALESCE(NULLIF({column}, ''), 'unknown')
            ORDER BY count DESC, status
            """,
        )
        return {
            "available": True,
            "total": sum(int(item["count"]) for item in grouped),
            "counts": {str(item["status"]): int(item["count"]) for item in grouped},
        }

    def embedding_status(self) -> dict:
        if not self.has("embeddings"):
            return {"available": False, "total": 0, "models": []}
        columns = table_columns(self.connection, "embeddings")
        if not {"model", "dimensions"}.issubset(columns):
            return {"available": False, "total": 0, "models": []}
        updated = "MAX(updated_at) AS latest_at" if "updated_at" in columns else "NULL AS latest_at"
        vector_bytes = "SUM(length(vector)) AS vector_bytes" if "vector" in columns else "0 AS vector_bytes"
        models = rows(
            self.connection,
            f"""
            SELECT model, dimensions, COUNT(*) AS embeddings,
                   {vector_bytes}, {updated}
            FROM embeddings
            GROUP BY model, dimensions
            ORDER BY embeddings DESC, model
            """,
        )
        total = sum(int(item["embeddings"]) for item in models)
        return {
            "available": True,
            "total": total,
            "vector_bytes": sum(int(item["vector_bytes"] or 0) for item in models),
            "models": models,
        }

    def map_status(self) -> dict:
        if not self.has("ontology_nodes"):
            return {
                "available": False,
                "nodes": 0,
                "by_type": {},
                "graph_available": False,
                "relation_counts": {},
            }
        grouped = rows(
            self.connection,
            """
            SELECT type, COUNT(*) AS count
            FROM ontology_nodes
            GROUP BY type
            ORDER BY count DESC, type
            """,
        )
        map_path = self.info.path.parent / "map.lbdb"
        graph = self.graph_status(map_path)
        return {
            "available": True,
            "nodes": sum(int(item["count"]) for item in grouped),
            "by_type": {str(item["type"]): int(item["count"]) for item in grouped},
            "graph_available": graph["available"],
            "relation_counts": graph["relations"],
            "message": graph["message"],
        }

    def graph_status(self, map_path) -> dict:
        if not map_path.is_file():
            return {
                "available": False,
                "relations": {},
                "message": "Map ファイルがないため、SQLite の ontology_nodes のみ表示しています。",
            }
        package_root = map_path.parent
        package_path = package_root / "lucid_memories"
        inserted = False
        if package_path.is_dir() and str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
            inserted = True
        database = connection = None
        try:
            from lucid_memories.runtime.ladybug_runtime import connect as connect_map

            database, connection = connect_map(map_path, read_only=True)
            table_result = connection.execute("CALL show_tables() RETURN *")
            table_names = {str(row[1]) for row in table_result.get_all() if len(row) > 1}
            relations: dict[str, int] = {}
            for relation in (
                "TRIGGERS",
                "USES",
                "ABOUT",
                "IN_CONTEXT",
                "RELATED",
                "FORBIDS",
            ):
                if relation not in table_names:
                    continue
                result = connection.execute(f"MATCH ()-[r:{relation}]->() RETURN count(*)")
                values = result.get_all()
                relations[relation] = int(values[0][0]) if values else 0
                if relation != "FORBIDS":
                    for status in ("proposed", "confirmed", "denied"):
                        result = connection.execute(
                            f"MATCH ()-[r:{relation}]->() "
                            f"WHERE r.status = '{status}' RETURN count(*)"
                        )
                        values = result.get_all()
                        relations[f"{relation.lower()}_{status}"] = int(values[0][0]) if values else 0
            return {
                "available": True,
                "relations": relations,
                "message": "SQLite の ontology_nodes と Map の関係数を表示しています。",
            }
        except Exception:
            return {
                "available": False,
                "relations": {},
                "message": (
                    "Map ファイルは存在しますが、読み取り用 graph runtime を利用できません。"
                ),
            }
        finally:
            if connection is not None:
                connection.close()
            if database is not None:
                database.close()
            if inserted:
                sys.path.remove(str(package_root))

    def storage_summary(self) -> dict:
        blobs = {"available": False, "count": 0, "bytes": 0}
        if self.has("blobs"):
            columns = table_columns(self.connection, "blobs")
            bytes_expression = "COALESCE(SUM(bytes), 0)" if "bytes" in columns else "0"
            blob_count = one(
                self.connection,
                f"SELECT COUNT(*) AS count, {bytes_expression} AS bytes FROM blobs",
            )
            blobs = {
                "available": True,
                "count": int(blob_count["count"]),
                "bytes": int(blob_count["bytes"] or 0),
            }
        scopes: list[dict] = []
        if self.has("artifacts"):
            columns = table_columns(self.connection, "artifacts")
            scope = "COALESCE(NULLIF(storage_scope, ''), 'unknown')" if "storage_scope" in columns else "'unknown'"
            size = "COALESCE(SUM(size_bytes), 0)" if "size_bytes" in columns else "0"
            scopes = rows(
                self.connection,
                f"""
                SELECT {scope} AS scope, COUNT(*) AS artifacts, {size} AS bytes
                FROM artifacts
                GROUP BY {scope}
                ORDER BY artifacts DESC, scope
                """,
            )
        return {"blobs": blobs, "artifact_scopes": scopes}

    def retrieval_summary(self) -> dict:
        if not self.has("retrieval_logs"):
            return {"available": False, "events": 0, "with_hits": 0, "hit_rate": None}
        columns = table_columns(self.connection, "retrieval_logs")
        sqlite_hits = "COALESCE(sqlite_hit_count, 0)" if "sqlite_hit_count" in columns else "0"
        map_hits = "COALESCE(map_hit_count, 0)" if "map_hit_count" in columns else "0"
        result = one(
            self.connection,
            f"""
            SELECT COUNT(*) AS events,
                   SUM(CASE WHEN {sqlite_hits} + {map_hits} > 0 THEN 1 ELSE 0 END) AS with_hits
            FROM retrieval_logs
            """,
        )
        events = int(result["events"] or 0)
        with_hits = int(result["with_hits"] or 0)
        return {
            "available": True,
            "events": events,
            "with_hits": with_hits,
            "hit_rate": round(with_hits / events, 4) if events else None,
        }

    def hook_coverage(self) -> dict:
        if not self.has("conversation_events"):
            return {
                "available": False,
                "status": "unavailable",
                "events": 0,
                "by_type": {},
                "missing": list(EXPECTED_HOOKS),
                "expected": list(EXPECTED_HOOKS),
                "message": "conversation_events が利用できないため、hook 収集状況を表示できません。",
            }
        grouped = rows(
            self.connection,
            """
            SELECT COALESCE(NULLIF(event_type, ''), 'unknown') AS event_type, COUNT(*) AS count
            FROM conversation_events
            GROUP BY COALESCE(NULLIF(event_type, ''), 'unknown')
            ORDER BY count DESC, event_type
            """,
        )
        by_type = {str(item["event_type"]): int(item["count"]) for item in grouped}
        events = sum(by_type.values())
        missing = [name for name in EXPECTED_HOOKS if not by_type.get(name)]
        if not events:
            status = "no_events"
            message = "conversation_events は存在しますが、hook イベントはまだありません。"
        elif missing:
            status = "partial"
            collected = len(EXPECTED_HOOKS) - len(missing)
            message = f"{collected}/{len(EXPECTED_HOOKS)} 種類の hook を収集しています。"
        else:
            status = "available"
            message = "想定している hook 種別を収集しています。"
        return {
            "available": True,
            "status": status,
            "events": events,
            "by_type": by_type,
            "missing": missing,
            "expected": list(EXPECTED_HOOKS),
            "message": message,
        }

    def persona_status(self) -> dict:
        path = self.info.path.parent / "persona" / "persona.json"
        document: dict | None = None
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                document = loaded if isinstance(loaded, dict) else None
            except (OSError, json.JSONDecodeError, UnicodeError):
                document = None
        candidates = self.grouped_status("persona_candidates", "status")
        revisions = self.count("persona_revisions")
        if document is None:
            message = (
                "persona.json はありますが読み取れません。"
                if path.is_file()
                else "persona.json がまだありません。"
            )
        else:
            message = "Workspace 非依存の global persona を読み取り専用で表示しています。"
        return {
            "available": document is not None,
            "path_present": path.is_file(),
            "scope": None if document is None else document.get("scope"),
            "sections": 0 if document is None else len(document.get("sections") or []),
            "token_estimate": None if document is None else document.get("token_estimate"),
            "token_budget": None if document is None else document.get("token_budget"),
            "updated_at": None if document is None else document.get("updated_at"),
            "candidates": candidates,
            "revisions": revisions,
            "message": message,
        }

    def context_summary(self) -> dict:
        if not self.has("packs"):
            return {
                "available": False,
                "packs": 0,
                "token_estimate": 0,
                "items": 0,
                "message": "packs が利用できないため、context budget を表示できません。",
            }
        columns = table_columns(self.connection, "packs")
        token_expression = "COALESCE(SUM(token_estimate), 0)" if "token_estimate" in columns else "0"
        summary = one(
            self.connection,
            f"SELECT COUNT(*) AS packs, {token_expression} AS token_estimate FROM packs",
        )
        return {
            "available": True,
            "packs": int(summary["packs"] or 0),
            "token_estimate": int(summary["token_estimate"] or 0),
            "items": self.count("pack_items"),
            "message": "Pack の token estimate は保存時の見積もりです。load 時の omitted は replay しません。",
        }

    def capabilities(self) -> dict[str, bool]:
        return {table: self.has(table) for table in TABLES}

    def session_rows(self, query: CollectionQuery, recent: bool = False) -> dict:
        if not self.has("sessions"):
            return {"data": [], "pagination": query.page.metadata(0)}
        prompt_join = (
            "LEFT JOIN turn_state t ON t.conversation_id = s.conversation_id"
            if self.has("turn_state")
            else ""
        )
        prompt_column = "t.last_prompt" if self.has("turn_state") else "NULL"
        conditions, parameters = session_conditions(
            query,
            self.has("turn_state"),
            has_events=self.has("conversation_events"),
        )
        order = (
            "COALESCE(s.last_heartbeat_at, s.updated_at) DESC"
            if recent
            else session_order(query.sort)
        )
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM sessions s {prompt_join} {where}",
            parameters,
        )["total"]
        limit = query.page.size
        job_count = (
            "(SELECT COUNT(*) FROM jobs j WHERE j.conversation_id = s.conversation_id)"
            if self.has("jobs")
            else "0"
        )
        compaction_count = (
            "(SELECT COUNT(*) FROM compact_events c WHERE c.conversation_id = s.conversation_id)"
            if self.has("compact_events")
            else "0"
        )
        session_columns = table_columns(self.connection, "sessions") if self.has("sessions") else set()
        summary_column = (
            "s.summary"
            if "summary" in session_columns
            else "NULL AS summary"
        )
        data = rows(
            self.connection,
            f"""
            SELECT s.conversation_id, s.parent_conversation_id, s.title, {summary_column},
                   s.status, s.model, s.composer_mode, s.is_background, s.transcript_path,
                   s.last_generation_id, s.last_heartbeat_at, s.created_at, s.updated_at,
                   {prompt_column} AS last_prompt,
                   {job_count} AS job_count,
                   {compaction_count} AS compaction_count
            FROM sessions s
            {prompt_join}
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, query.page.offset),
        )
        data = enrich_sessions(
            self.connection,
            data,
            has_events=self.has("conversation_events"),
            has_jobs=self.has("jobs"),
        )
        return {"data": data, "pagination": query.page.metadata(total)}

    def job_rows(self, query: CollectionQuery) -> dict:
        if not self.has("jobs"):
            return {"data": [], "pagination": query.page.metadata(0)}
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = j.conversation_id"
            if self.has("sessions")
            else ""
        )
        session_columns = (
            "s.title AS session_title, s.model AS session_model"
            if self.has("sessions")
            else "NULL AS session_title, NULL AS session_model"
        )
        conditions, parameters = generic_conditions("j", query, "updated_at")
        if query.text:
            pattern = like_pattern(query.text)
            conditions.append(
                "(j.id LIKE ? ESCAPE '\\' OR j.conversation_id LIKE ? ESCAPE '\\' OR "
                "COALESCE(j.kind, '') LIKE ? ESCAPE '\\' OR COALESCE(j.title, '') LIKE ? ESCAPE '\\' "
                "OR COALESCE(j.summary, '') LIKE ? ESCAPE '\\')"
            )
            parameters.extend([pattern] * 5)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM jobs j {where}",
            parameters,
        )["total"]
        order = job_order(query.sort)
        data = rows(
            self.connection,
            f"""
            SELECT j.id, j.conversation_id, j.kind, j.status, j.title, j.summary,
                   j.subagent_type, j.started_at, j.updated_at, j.ended_at, j.rev,
                   {session_columns}
            FROM jobs j
            {session_join}
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        return {"data": data, "pagination": query.page.metadata(total)}

    def candidate_rows(self, query: CollectionQuery) -> dict:
        if not self.has("memory_candidates"):
            return {"data": [], "pagination": query.page.metadata(0)}
        columns = table_columns(self.connection, "memory_candidates")
        conditions, parameters = generic_conditions("c", query, "updated_at")
        if query.text:
            pattern = like_pattern(query.text)
            searchable = [
                column
                for column in ("id", "conversation_id", "kind", "title", "summary", "source_event_id")
                if column in columns
            ]
            if searchable:
                conditions.append(
                    "("
                    + " OR ".join(f"COALESCE(c.{column}, '') LIKE ? ESCAPE '\\'" for column in searchable)
                    + ")"
                )
                parameters.extend([pattern] * len(searchable))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM memory_candidates c {where}",
            parameters,
        )["total"]
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = c.conversation_id"
            if self.has("sessions")
            else ""
        )
        title_column = "s.title AS session_title" if self.has("sessions") else "NULL AS session_title"
        data = rows(
            self.connection,
            f"""
            SELECT c.*, {title_column}
            FROM memory_candidates c
            {session_join}
            {where}
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        return {"data": data, "pagination": query.page.metadata(total)}

    def knowledge_rows(self, query: CollectionQuery) -> dict:
        if not self.has("knowledge"):
            return {"data": [], "pagination": query.page.metadata(0)}
        columns = table_columns(self.connection, "knowledge")
        conditions: list[str] = []
        parameters: list[object] = []
        if query.status and "memory_status" in columns:
            conditions.append("k.memory_status = ?")
            parameters.append(query.status)
        if query.text:
            pattern = like_pattern(query.text)
            searchable = [
                column
                for column in ("id", "kind", "title", "body", "source_conversation_id", "source_event_id")
                if column in columns
            ]
            if searchable:
                conditions.append(
                    "("
                    + " OR ".join(f"COALESCE(k.{column}, '') LIKE ? ESCAPE '\\'" for column in searchable)
                    + ")"
                )
                parameters.extend([pattern] * len(searchable))
        add_date_conditions(conditions, parameters, "k.updated_at", query)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM knowledge k {where}",
            parameters,
        )["total"]
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = k.source_conversation_id"
            if self.has("sessions") and "source_conversation_id" in columns
            else ""
        )
        title_column = "s.title AS session_title" if session_join else "NULL AS session_title"
        data = rows(
            self.connection,
            f"""
            SELECT k.*, {title_column}
            FROM knowledge k
            {session_join}
            {where}
            ORDER BY k.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        for item in data:
            item["body_preview"] = truncate_text(item.get("body"), BODY_PREVIEW_LIMIT)
            item["has_blob"] = bool(item.get("blob_sha"))
            item.pop("body", None)
        return {"data": data, "pagination": query.page.metadata(total)}

    def knowledge_detail(self, knowledge_id: str) -> dict:
        if not self.has("knowledge"):
            return {}
        columns = table_columns(self.connection, "knowledge")
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = k.source_conversation_id"
            if self.has("sessions") and "source_conversation_id" in columns
            else ""
        )
        title_column = "s.title AS session_title" if session_join else "NULL AS session_title"
        item = one(
            self.connection,
            f"""
            SELECT k.*, {title_column}
            FROM knowledge k
            {session_join}
            WHERE k.id = ?
            """,
            (knowledge_id,),
        )
        if not item:
            return {}
        body = item.get("body") or self.blob_text(item.get("blob_sha"))
        truncated = truncate_text(body, DETAIL_TEXT_LIMIT)
        item["body"] = truncated
        item["body_truncated"] = bool(body) and truncated != body
        item["has_blob"] = bool(item.get("blob_sha"))
        return item

    def task_rows(self, query: CollectionQuery) -> dict:
        if not self.has("memory_tasks"):
            return {"data": [], "pagination": query.page.metadata(0)}
        columns = table_columns(self.connection, "memory_tasks")
        date_column = "updated_at" if "updated_at" in columns else "created_at"
        conditions, parameters = generic_conditions("t", query, date_column)
        if query.text:
            pattern = like_pattern(query.text)
            searchable = [
                column
                for column in ("id", "task_type", "entity_type", "entity_id", "last_error")
                if column in columns
            ]
            if searchable:
                conditions.append(
                    "("
                    + " OR ".join(f"COALESCE(t.{column}, '') LIKE ? ESCAPE '\\'" for column in searchable)
                    + ")"
                )
                parameters.extend([pattern] * len(searchable))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM memory_tasks t {where}",
            parameters,
        )["total"]
        selected = [
            column
            for column in (
                "id",
                "task_type",
                "entity_type",
                "entity_id",
                "status",
                "attempts",
                "available_at",
                "locked_at",
                "last_error",
                "created_at",
                "updated_at",
            )
            if column in columns
        ]
        column_sql = ", ".join(f"t.{column}" for column in selected) if selected else "t.*"
        data = rows(
            self.connection,
            f"""
            SELECT {column_sql}
            FROM memory_tasks t
            {where}
            ORDER BY t.{date_column} DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        return {"data": data, "pagination": query.page.metadata(total)}

    def event_rows(self, query: CollectionQuery) -> dict:
        if not self.has("conversation_events"):
            return {"data": [], "pagination": query.page.metadata(0)}
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = e.conversation_id"
            if self.has("sessions")
            else ""
        )
        title_column = "s.title AS session_title" if self.has("sessions") else "NULL AS session_title"
        conditions, parameters = generic_conditions("e", query, "created_at")
        if query.text:
            pattern = like_pattern(query.text)
            conditions.append(
                "(e.conversation_id LIKE ? ESCAPE '\\' OR "
                "COALESCE(e.tool_name, '') LIKE ? ESCAPE '\\' OR "
                "COALESCE(e.input_text, '') LIKE ? ESCAPE '\\' OR "
                "COALESCE(e.output_text, '') LIKE ? ESCAPE '\\')"
            )
            parameters.extend([pattern] * 4)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM conversation_events e {where}",
            parameters,
        )["total"]
        data = rows(
            self.connection,
            f"""
            SELECT e.*, {title_column}
            FROM conversation_events e
            {session_join}
            {where}
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        return {"data": data, "pagination": query.page.metadata(total)}

    def artifact_rows(self, query: CollectionQuery) -> dict:
        if not self.has("artifacts"):
            return {"data": [], "pagination": query.page.metadata(0)}
        columns = table_columns(self.connection, "artifacts")
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = a.conversation_id"
            if self.has("sessions")
            else ""
        )
        title_column = "s.title AS session_title" if self.has("sessions") else "NULL AS session_title"
        conditions: list[str] = []
        parameters: list[object] = []
        if query.scope and "storage_scope" in columns:
            conditions.append("a.storage_scope = ?")
            parameters.append(query.scope)
        add_date_conditions(conditions, parameters, "a.updated_at", query)
        if query.text:
            pattern = like_pattern(query.text)
            conditions.append(
                "(COALESCE(a.path, '') LIKE ? ESCAPE '\\' OR "
                "COALESCE(a.relative_path, '') LIKE ? ESCAPE '\\' OR "
                "COALESCE(a.name, '') LIKE ? ESCAPE '\\' OR "
                "a.conversation_id LIKE ? ESCAPE '\\')"
            )
            parameters.extend([pattern] * 4)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = one(
            self.connection,
            f"SELECT COUNT(*) AS total FROM artifacts a {where}",
            parameters,
        )["total"]
        data = rows(
            self.connection,
            f"""
            SELECT a.*, {title_column}
            FROM artifacts a
            {session_join}
            {where}
            ORDER BY a.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, query.page.size, query.page.offset),
        )
        return {"data": self.enrich_artifacts(data), "pagination": query.page.metadata(total)}

    def artifact_detail(self, artifact_id: str) -> dict:
        if not self.has("artifacts"):
            return {}
        session_join = (
            "LEFT JOIN sessions s ON s.conversation_id = a.conversation_id"
            if self.has("sessions")
            else ""
        )
        title_column = "s.title AS session_title" if session_join else "NULL AS session_title"
        item = one(
            self.connection,
            f"""
            SELECT a.*, {title_column}
            FROM artifacts a
            {session_join}
            WHERE a.id = ?
            """,
            (artifact_id,),
        )
        if not item:
            return {}
        item = self.enrich_artifact(item)
        preview = item.get("content_preview")
        if item["content_availability"] == "blob" and not preview:
            preview = self.blob_text(item.get("blob_sha"))
        item["preview"] = truncate_text(preview, DETAIL_TEXT_LIMIT)
        return item

    def enrich_artifacts(self, artifacts: list[dict]) -> list[dict]:
        recorded = self.recorded_blob_shas(
            item.get("blob_sha") for item in artifacts if item.get("blob_sha")
        )
        return [self.enrich_artifact(item, recorded) for item in artifacts]

    def enrich_artifact(self, artifact: dict, recorded_blobs: set[str] | None = None) -> dict:
        blob_sha = artifact.get("blob_sha")
        scope = artifact.get("storage_scope") or ""
        recorded = recorded_blobs if recorded_blobs is not None else self.recorded_blob_shas([blob_sha] if blob_sha else [])
        blob_on_disk = bool(blob_sha) and self.blob_path(str(blob_sha)).is_file()
        blob_recorded = bool(blob_sha) and (str(blob_sha) in recorded or blob_on_disk)
        path = artifact.get("path")
        try:
            path_exists = bool(path) and Path(str(path)).is_file()
        except OSError:
            path_exists = False
        if blob_recorded:
            availability = "blob"
            origin = "lucid_memories"
        elif scope in {"workspace", "repository"}:
            availability = "external"
            origin = scope
        elif artifact.get("content_preview"):
            availability = "preview"
            origin = scope or "legacy"
        else:
            availability = "unavailable"
            origin = scope or "unknown"
        artifact["content_availability"] = availability
        artifact["origin"] = origin
        artifact["blob_recorded"] = blob_recorded
        artifact["path_exists"] = path_exists
        artifact["message"] = ARTIFACT_MESSAGES[availability]
        return artifact

    def recorded_blob_shas(self, shas: Iterable[object]) -> set[str]:
        values = [str(sha) for sha in shas if sha]
        if not values or not self.has("blobs"):
            return set()
        placeholders = ", ".join("?" for _ in values)
        found = rows(
            self.connection,
            f"SELECT sha256 FROM blobs WHERE sha256 IN ({placeholders})",
            values,
        )
        return {str(item["sha256"]) for item in found}

    def blob_path(self, sha: str) -> Path:
        return self.info.path.parent / "blobs" / sha[:2] / sha

    def blob_text(self, sha: str | None, limit: int = DETAIL_TEXT_LIMIT) -> str | None:
        if not sha:
            return None
        path = self.blob_path(sha)
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()[: limit * 4]
        except OSError:
            return None
        return truncate_text(raw.decode("utf-8", errors="replace"), limit)

    def session_detail(self, conversation_id: str) -> dict:
        if not self.has("sessions"):
            return {}
        session = one(
            self.connection,
            """
            SELECT s.*, t.last_prompt, t.last_prompt_at
            FROM sessions s
            LEFT JOIN turn_state t ON t.conversation_id = s.conversation_id
            WHERE s.conversation_id = ?
            """,
            (conversation_id,),
        ) if self.has("turn_state") else one(
            self.connection,
            "SELECT * FROM sessions WHERE conversation_id = ?",
            (conversation_id,),
        )
        if not session:
            return {}
        session = enrich_session_detail(
            self.connection,
            session,
            has_events=self.has("conversation_events"),
            has_jobs=self.has("jobs"),
        )
        events = (
            rows(
                self.connection,
                """
                SELECT * FROM conversation_events
                WHERE conversation_id = ?
                ORDER BY created_at DESC LIMIT 100
                """,
                (conversation_id,),
            )
            if self.has("conversation_events")
            else []
        )
        output = next(
            (
                event.get("output_text")
                for event in events
                if event.get("output_text")
                and (
                    event.get("role") == "assistant"
                    or event.get("event_type") in {"afterAgentResponse", "agent_response"}
                )
            ),
            None,
        )
        usage_rows = self.related_rows("usage_events", conversation_id, "created_at")
        llm_usage = [
            row for row in usage_rows if row.get("event_type") != "mcp"
        ]
        mcp_threshold = (self.mcp_summary().get("stats") or {}).get("large_threshold")
        mcp_usage = [
            self.enrich_mcp_row(row, large_threshold=mcp_threshold)
            for row in usage_rows
            if row.get("event_type") == "mcp"
        ]
        return {
            "data": session,
            "relationships": {
                "jobs": self.related_rows("jobs", conversation_id, "updated_at"),
                "events": events,
                "usage": llm_usage,
                "mcp": mcp_usage,
                "artifacts": self.related_rows("artifacts", conversation_id, "updated_at"),
                "compactions": self.related_rows("compact_events", conversation_id, "created_at"),
            },
            "input_output": {
                "input": session.get("last_prompt"),
                "output": output,
                "message": "hook payload に含まれる会話入出力を表示しています。",
            },
        }

    def related_rows(self, table: str, conversation_id: str, order_column: str) -> list[dict]:
        if not self.has(table):
            return []
        data = rows(
            self.connection,
            f"""
            SELECT * FROM {table}
            WHERE conversation_id = ?
            ORDER BY {order_column} DESC
            LIMIT 100
            """,
            (conversation_id,),
        )
        if table == "artifacts":
            return self.enrich_artifacts(data)
        return data

    def enrich_mcp_row(
        self,
        row: dict,
        *,
        large_threshold: int | None = None,
    ) -> dict:
        item = dict(row)
        meta: dict = {}
        raw = item.get("metadata_json")
        if isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    meta = loaded
            except json.JSONDecodeError:
                meta = {}
        item["tool_name"] = meta.get("tool_name") or item.get("input_text")
        tokens = int(item.get("total_tokens") or item.get("output_tokens") or 0)
        item["tokens"] = tokens
        item["budget"] = (
            meta.get("budget") if meta.get("budget") is not None else meta.get("budget_tokens")
        )
        item["tokens_used"] = meta.get("tokens_used")
        if large_threshold is not None:
            item["is_large"] = tokens > large_threshold
        return item

    def daily(self, days: int) -> list[dict]:
        start = date.today() - timedelta(days=days - 1)
        calendar = [
            (start + timedelta(days=offset)).isoformat()
            for offset in range(days)
        ]
        result = {day: {"day": day} for day in calendar}
        self.merge_daily(result, "sessions", "created_at", "sessions")
        self.merge_daily(result, "jobs", "COALESCE(ended_at, updated_at)", "jobs")
        self.merge_daily(result, "compact_events", "created_at", "compactions", {
            "context_tokens": "SUM(COALESCE(context_tokens, 0))",
            "context_usage_percent": "AVG(context_usage_percent)",
            "messages_to_compact": "SUM(COALESCE(messages_to_compact, 0))",
        })
        self.merge_daily(result, "retrieval_logs", "created_at", "retrievals", {
            "retrieval_hits": "SUM(CASE WHEN sqlite_hit_count + map_hit_count > 0 THEN 1 ELSE 0 END)",
        })
        self.merge_daily(result, "packs", "created_at", "packs", {
            "pack_tokens": "SUM(COALESCE(token_estimate, 0))",
        })
        self.merge_usage_daily(result, start)
        self.merge_mcp_daily(result, start)
        for item in result.values():
            item.setdefault("sessions", 0)
            item.setdefault("jobs", 0)
            item.setdefault("completed_jobs", 0)
            item.setdefault("compactions", 0)
            item.setdefault("context_tokens", 0)
            item.setdefault("context_usage_percent", None)
            item.setdefault("messages_to_compact", 0)
            item.setdefault("retrievals", 0)
            item.setdefault("retrieval_hits", 0)
            item.setdefault("packs", 0)
            item.setdefault("pack_tokens", 0)
            item.setdefault("usage_events", 0)
            item.setdefault("input_tokens", 0)
            item.setdefault("output_tokens", 0)
            item.setdefault("cached_tokens", 0)
            item.setdefault("cost_usd", 0)
            item.setdefault("mcp_calls", 0)
            item.setdefault("mcp_tokens", 0)
        return list(result.values())

    def merge_daily(
        self,
        result: dict[str, dict],
        table: str,
        date_expression: str,
        count_key: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        if not self.has(table):
            return
        available = table_columns(self.connection, table)
        if date_expression == "COALESCE(ended_at, updated_at)":
            if "updated_at" not in available:
                return
            date_expression = (
                "COALESCE(ended_at, updated_at)"
                if "ended_at" in available
                else "updated_at"
            )
        selected = [f"COUNT(*) AS {count_key}"]
        if extra:
            selected.extend(
                f"{expression} AS {key}"
                for key, expression in extra.items()
                if all(
                    column in available
                    for column in (
                        "context_tokens",
                        "context_usage_percent",
                        "messages_to_compact",
                    )
                )
                if table == "compact_events"
            )
        if table == "jobs":
            selected.append("SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS completed_jobs")
        if table == "retrieval_logs" and not {"sqlite_hit_count", "map_hit_count"}.issubset(available):
            selected = [f"COUNT(*) AS {count_key}"]
        if table == "packs" and "token_estimate" not in available:
            selected = [f"COUNT(*) AS {count_key}"]
        start = min(result)
        grouped = rows(
            self.connection,
            f"""
            SELECT date({date_expression}) AS day, {", ".join(selected)}
            FROM {table}
            WHERE date({date_expression}) >= date(?)
            GROUP BY date({date_expression})
            """,
            (start,),
        )
        for item in grouped:
            if item["day"] in result:
                result[item["day"]].update(item)

    def merge_usage_daily(self, result: dict[str, dict], start: date) -> None:
        if not self.has("usage_events"):
            return
        columns = table_columns(self.connection, "usage_events")
        expressions = {
            "input_tokens": optional_column("u", "input_tokens", columns, "0"),
            "output_tokens": optional_column("u", "output_tokens", columns, "0"),
            "cached_tokens": (
                f"COALESCE({optional_column('u', 'cache_read_tokens', columns, '0')}, 0) + "
                f"COALESCE({optional_column('u', 'cache_write_tokens', columns, '0')}, 0)"
            ),
            "cost_usd": optional_column("u", "cost_usd", columns, "0"),
        }
        source_filter = usage_source_sql("u", columns, mcp=False)
        grouped = rows(
            self.connection,
            f"""
            SELECT date(u.created_at) AS day, COUNT(*) AS usage_events,
                   SUM({expressions["input_tokens"]}) AS input_tokens,
                   SUM({expressions["output_tokens"]}) AS output_tokens,
                   SUM({expressions["cached_tokens"]}) AS cached_tokens,
                   SUM({expressions["cost_usd"]}) AS cost_usd
            FROM usage_events u
            WHERE date(u.created_at) >= date(?) AND {source_filter}
            GROUP BY date(u.created_at)
            """,
            (start.isoformat(),),
        )
        for item in grouped:
            if item["day"] in result:
                result[item["day"]].update(item)

    def merge_mcp_daily(self, result: dict[str, dict], start: date) -> None:
        if not self.has("usage_events"):
            return
        columns = table_columns(self.connection, "usage_events")
        if "event_type" not in columns:
            return
        token_expr = mcp_token_sql("u", columns)
        grouped = rows(
            self.connection,
            f"""
            SELECT date(u.created_at) AS day, COUNT(*) AS mcp_calls,
                   COALESCE(SUM({token_expr}), 0) AS mcp_tokens
            FROM usage_events u
            WHERE date(u.created_at) >= date(?)
              AND {usage_source_sql("u", columns, mcp=True)}
            GROUP BY date(u.created_at)
            """,
            (start.isoformat(),),
        )
        for item in grouped:
            if item["day"] in result:
                result[item["day"]].update(item)

    def count(self, table: str, condition: str | None = None) -> int:
        if not self.has(table):
            return 0
        suffix = f" WHERE {condition}" if condition else ""
        return int(one(self.connection, f"SELECT COUNT(*) AS total FROM {table}{suffix}")["total"])

    def has(self, table: str) -> bool:
        return self.info.has(table) or table_exists(self.connection, table)


def session_conditions(
    query: CollectionQuery,
    has_turn_state: bool,
    *,
    has_events: bool = False,
) -> tuple[list[str], list[object]]:
    from lucid_memories.core.session_metadata import (
        session_kind_sql_condition,
        session_origin_sql_condition,
    )

    conditions = []
    parameters: list[object] = []
    if query.text:
        pattern = like_pattern(query.text)
        fields = [
            "s.conversation_id LIKE ? ESCAPE '\\'",
            "COALESCE(s.title, '') LIKE ? ESCAPE '\\'",
            "COALESCE(s.model, '') LIKE ? ESCAPE '\\'",
        ]
        if has_turn_state:
            fields.append("COALESCE(t.last_prompt, '') LIKE ? ESCAPE '\\'")
        conditions.append(f"({' OR '.join(fields)})")
        parameters.extend([pattern] * len(fields))
    if query.status:
        conditions.append("s.status = ?")
        parameters.append(query.status)
    if query.model:
        conditions.append("s.model = ?")
        parameters.append(query.model)
    kind_condition, kind_params = session_kind_sql_condition(query.session_kind)
    if kind_condition:
        conditions.append(kind_condition)
        parameters.extend(kind_params)
    origin_condition, origin_params = session_origin_sql_condition(
        query.origin,
        has_events=has_events,
    )
    if origin_condition:
        conditions.append(origin_condition)
        parameters.extend(origin_params)
    add_date_conditions(conditions, parameters, "s.updated_at", query)
    return conditions, parameters


def generic_conditions(
    alias: str,
    query: CollectionQuery,
    date_column: str,
) -> tuple[list[str], list[object]]:
    conditions = []
    parameters: list[object] = []
    if query.status:
        conditions.append(f"{alias}.status = ?")
        parameters.append(query.status)
    add_date_conditions(conditions, parameters, f"{alias}.{date_column}", query)
    return conditions, parameters


def add_date_conditions(
    conditions: list[str],
    parameters: list[object],
    column: str,
    query: CollectionQuery,
) -> None:
    if query.start:
        conditions.append(f"date({column}) >= date(?)")
        parameters.append(query.start)
    if query.end:
        conditions.append(f"date({column}) <= date(?)")
        parameters.append(query.end)


def like_pattern(value: str) -> str:
    return f"%{value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"


def truncate_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def optional_column(alias: str, column: str, available: set[str], fallback: str = "NULL") -> str:
    return f"{alias}.{column}" if column in available else fallback


def percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def token_distribution(values: list[int]) -> dict:
    if not values:
        return {"sample_size": 0}
    sample_size = len(values)
    median = round(statistics.median(values))
    stats: dict[str, int | str | None] = {
        "sample_size": sample_size,
        "mean": round(statistics.mean(values)),
        "median": median,
        "p75": percentile(values, 75),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "max": max(values),
        "large_threshold": None,
        "large_label": None,
    }
    if sample_size >= 5:
        stats["large_threshold"] = stats["p90"]
        stats["large_label"] = "p90 超"
    elif sample_size >= 2:
        provisional = max(median * 2, stats["p75"] or median)
        stats["large_threshold"] = provisional
        stats["large_label"] = "暫定 (中央値×2)"
    return stats


def mcp_summary_message(events: int, stats: dict) -> str:
    if not events:
        return "MCP 利用量の記録はまだありません。"
    sample_size = int(stats.get("sample_size") or 0)
    if sample_size < 5:
        return (
            "MCP ツール呼び出しの結果サイズを計測しています。"
            " 記録が 5 件以上になると、p90 ベースの「大きい」判定が安定します。"
        )
    threshold = stats.get("large_threshold")
    return (
        "MCP ツール呼び出しの結果サイズを計測しています。"
        f" 平均 {stats.get('mean')} tok、中央値 {stats.get('median')} tok。"
        f" {threshold} tok 超を大きい ({stats.get('large_label')}) とみなします。"
    )


def usage_source_sql(alias: str, columns: set[str], *, mcp: bool) -> str:
    if "event_type" not in columns:
        return "0=1" if mcp else "1=1"
    if mcp:
        return f"{alias}.event_type = 'mcp'"
    return f"COALESCE({alias}.event_type, '') != 'mcp'"


def mcp_token_sql(alias: str, columns: set[str]) -> str:
    if "total_tokens" in columns:
        return f"COALESCE({alias}.total_tokens, 0)"
    if "output_tokens" in columns:
        return f"COALESCE({alias}.output_tokens, 0)"
    return "0"


def mcp_tool_sql(alias: str, columns: set[str]) -> str:
    parts: list[str] = []
    if "metadata_json" in columns:
        parts.append(f"json_extract({alias}.metadata_json, '$.tool_name')")
    if "input_text" in columns:
        parts.append(f"{alias}.input_text")
    if not parts:
        return "'unknown'"
    expr = parts[0]
    for part in parts[1:]:
        expr = f"COALESCE({expr}, {part})"
    return f"COALESCE({expr}, 'unknown')"


def session_order(sort: str) -> str:
    return {
        "created_at": "s.created_at ASC",
        "title": "COALESCE(s.title, '') ASC",
        "status": "s.status ASC, s.updated_at DESC",
    }.get(sort, "COALESCE(s.last_heartbeat_at, s.updated_at) DESC")


def job_order(sort: str) -> str:
    return {
        "created_at": "j.started_at ASC",
        "title": "COALESCE(j.title, '') ASC",
        "status": "j.status ASC, j.updated_at DESC",
    }.get(sort, "j.updated_at DESC")

