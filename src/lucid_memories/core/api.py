from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lucid_memories.storage import blobs
from lucid_memories.runtime import embedding
from . import memory, persona, retrieval
from lucid_memories.storage.db import checkpoint_passive, connect, connect_readonly, now_iso, write_tx
from lucid_memories.storage.paths import (
    DEFAULT_LOAD_BUDGET,
    INLINE_BODY_LIMIT,
    LEASE_TTL_SECONDS,
    SUMMARY_LIMIT,
    env,
)
from lucid_memories.runtime.util import (
    dumps,
    estimate_tokens,
    fts_match_arg,
    is_stale_heartbeat,
    looks_like_id,
    new_id,
    normalize_root,
    parse_iso,
    parse_roots,
    query_tokens,
    row_dict,
    truncate,
    workspace_contains,
    workspace_matches,
)

JOB_KINDS = ("subagent", "declared", "shell")
JOB_STATUSES = ("pending", "running", "blocked", "done", "error", "aborted", "stale")
KNOWLEDGE_KINDS = ("fact", "decision", "finding", "pointer", "warning", "handoff", "idea")
KNOWLEDGE_SCOPES = ("global", "workspace", "session")
PACK_KINDS = ("primer", "handoff", "compact_snapshot", "job_board")
NOTICE_KINDS = ("job_done", "please_load", "conflict", "compacted")
SOURCES = ("hook", "cli", "mcp")


def _conn() -> sqlite3.Connection:
    return connect()


def _embedding_text(entity_type: str, entity_id: str) -> str | None:
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
        provider_status = embedding.status()
        if provider_status.get("dimensions") is None and stored:
            provider_status["dimensions"] = stored[0]["dimensions"]
        return {
            "ok": True,
            "provider": embedding.provider(),
            "configured_model": embedding.model(),
            "provider_status": provider_status,
            "stored": stored,
        }
    finally:
        conn.close()


def backfill_embeddings(
    *,
    workspace: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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


def _usage_value(payload: dict[str, Any], *names: str) -> Any:
    sources: list[dict[str, Any]] = [payload]
    for key in ("usage", "token_usage", "tokenUsage", "metrics", "billing"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
    for source in sources:
        for name in names:
            if name in source and source[name] is not None:
                return source[name]
    return None


def _usage_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _usage_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def record_usage(
    payload: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    """Persist provider usage when a hook payload includes it.

    Cursor hook payloads have changed names over time, so common snake_case
    and camelCase aliases are accepted. Missing fields stay NULL.
    """
    conversation_id = _conversation_id_from_payload(payload)
    if not conversation_id:
        return {"ok": False, "recorded": False, "error": "conversation_id required"}

    input_tokens = _usage_int(
        _usage_value(
            payload,
            "input_tokens",
            "inputTokens",
            "prompt_tokens",
            "promptTokens",
        )
    )
    output_tokens = _usage_int(
        _usage_value(
            payload,
            "output_tokens",
            "outputTokens",
            "completion_tokens",
            "completionTokens",
        )
    )
    cache_read_tokens = _usage_int(
        _usage_value(
            payload,
            "cache_read_tokens",
            "cacheReadTokens",
            "cache_read_input_tokens",
            "cacheReadInputTokens",
            "cached_tokens",
            "cachedTokens",
        )
    )
    cache_write_tokens = _usage_int(
        _usage_value(
            payload,
            "cache_write_tokens",
            "cacheWriteTokens",
            "cache_creation_input_tokens",
            "cacheCreationInputTokens",
        )
    )
    context_tokens = _usage_int(
        _usage_value(payload, "context_tokens", "contextTokens")
    )
    context_window_size = _usage_int(
        _usage_value(payload, "context_window_size", "contextWindowSize")
    )
    total_tokens = _usage_int(
        _usage_value(payload, "total_tokens", "totalTokens")
    )
    cost_usd = _usage_float(
        _usage_value(
            payload,
            "cost_usd",
            "costUsd",
            "total_cost_usd",
            "totalCostUsd",
        )
    )
    model = _usage_value(payload, "model", "model_id", "modelId", "model_name", "modelName")
    input_text = _usage_value(payload, "prompt", "input_text", "inputText", "user_message")
    output_text = _usage_value(
        payload,
        "output_text",
        "outputText",
        "assistant_output",
        "assistantOutput",
        "response_text",
        "responseText",
    )
    has_usage = any(
        value is not None
        for value in (
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            total_tokens,
            cost_usd,
            input_text,
            output_text,
        )
    )
    if not has_usage:
        return {"ok": True, "recorded": False, "reason": "usage_not_present"}

    generation_id = payload.get("generation_id") or payload.get("generationId")
    metadata = {
        "source": "hook",
        "hook_event_name": payload.get("hook_event_name") or payload.get("event"),
    }
    now = now_iso()
    conn = _conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO usage_events(
                  id, conversation_id, generation_id, event_type, model,
                  input_tokens, output_tokens, cache_read_tokens,
                  cache_write_tokens, total_tokens, context_tokens,
                  context_window_size, cost_usd, input_text, output_text,
                  metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    conversation_id,
                    generation_id,
                    event_type,
                    str(model) if model is not None else None,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    total_tokens,
                    context_tokens,
                    context_window_size,
                    cost_usd,
                    str(input_text) if input_text is not None else None,
                    str(output_text) if output_text is not None else None,
                    dumps(metadata),
                    now,
                ),
            )
        return {"ok": True, "recorded": True, "created_at": now}
    finally:
        conn.close()


def _conversation_id_from_payload(payload: dict[str, Any]) -> str | None:
    return payload.get("conversation_id") or payload.get("session_id") or None


def _payload_text(payload: dict[str, Any], *names: str) -> str | None:
    value = _decode_json_value(_usage_value(payload, *names))
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        if isinstance(value, dict):
            for nested_name in (
                "text",
                "content",
                "output_text",
                "outputText",
                "assistant_output",
                "assistantOutput",
                "result",
                "message",
            ):
                nested = value.get(nested_name)
                if nested is not None and not isinstance(nested, (dict, list)):
                    return truncate(str(nested), 12000)
        return truncate(json.dumps(value, ensure_ascii=False), 12000)
    return truncate(str(value), 12000)


def _payload_artifact_text(payload: dict[str, Any], *names: str) -> str | None:
    """Read generated output for blob storage without the event preview limit."""
    value = _decode_json_value(_usage_value(payload, *names))
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    if not text.strip():
        return None
    try:
        max_chars = max(
            4096,
            int(env("ARTIFACT_MAX_CHARS", "4000000") or "4000000"),
        )
    except ValueError:
        max_chars = 4_000_000
    return text[:max_chars]


def _payload_workspace(payload: dict[str, Any]) -> str | None:
    roots = payload.get("workspace_roots") or []
    if isinstance(roots, str):
        roots = [roots]
    for root in roots:
        if root:
            return normalize_root(str(root))
    return None


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


_PATH_KEYS = {
    "path",
    "filepath",
    "filename",
    "outputfile",
    "targetfile",
    "artifactpath",
    "transcriptpath",
    "outputpath",
}
_PATCH_PATH_RE = re.compile(r"\*\*\*\s+(?:Add|Update|Delete) File:\s*(.+)")


def _artifact_paths(value: Any, *, key: str = "") -> set[str]:
    value = _decode_json_value(value)
    found: set[str] = set()
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            found.update(_artifact_paths(child_value, key=str(child_key)))
        return found
    if isinstance(value, list):
        for child in value:
            found.update(_artifact_paths(child, key=key))
        return found
    if not isinstance(value, str):
        return found

    normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized_key in _PATH_KEYS and value.strip():
        found.add(value.strip().strip('"'))
    if normalized_key in {"patch", "content", "command", "toolinput", "input"}:
        for match in _PATCH_PATH_RE.finditer(value):
            found.add(match.group(1).strip().strip('"'))
    return found


def _repository_root(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        try:
            if (parent / ".git").exists():
                return parent
        except OSError:
            continue
    return None


def _artifact_snapshot(
    raw_path: str,
    *,
    workspace: str | None,
) -> dict[str, Any] | None:
    cleaned = raw_path.strip().strip("`").strip('"')
    if not cleaned or len(cleaned) > 1000:
        return None
    path = Path(cleaned).expanduser()
    if not path.is_absolute() and workspace:
        path = Path(workspace) / path
    try:
        absolute = path.resolve()
    except OSError:
        absolute = path.absolute()
    record: dict[str, Any] = {
        "path": str(absolute),
        "relative_path": None,
        "name": absolute.name or str(absolute),
        "mime_type": mimetypes.guess_type(str(absolute))[0],
        "size_bytes": None,
        "sha256": None,
        "blob_sha": None,
        "content_preview": None,
        "storage_scope": "conversation",
        "storage_policy": "lucid_memories",
    }
    if workspace:
        try:
            record["relative_path"] = str(absolute.relative_to(Path(workspace).resolve()))
            record["storage_scope"] = "workspace"
            record["storage_policy"] = "managed_elsewhere"
        except (ValueError, OSError):
            pass
    repository = _repository_root(absolute)
    if repository is not None:
        record["storage_scope"] = "repository"
        record["storage_policy"] = "managed_elsewhere"
        record["repository_root"] = str(repository)
    try:
        if absolute.is_file():
            digest = hashlib.sha256()
            with absolute.open("rb") as stream:
                first = stream.read(2000)
                digest.update(first)
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            record["size_bytes"] = absolute.stat().st_size
            record["sha256"] = digest.hexdigest()
            if record["mime_type"] is None or record["mime_type"].startswith("text/"):
                record["content_preview"] = first.decode("utf-8", errors="replace")
            if record["storage_scope"] == "conversation":
                try:
                    max_bytes = int(
                        env("ARTIFACT_MAX_BYTES", str(4 * 1024 * 1024))
                        or str(4 * 1024 * 1024)
                    )
                except ValueError:
                    max_bytes = 4 * 1024 * 1024
                if record["size_bytes"] <= max_bytes:
                    record["_content_bytes"] = absolute.read_bytes()
    except OSError:
        pass
    return record


def record_activity(
    payload: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    """Index hook input/output and file paths without copying artifacts."""
    conversation_id = _conversation_id_from_payload(payload)
    if not conversation_id:
        return {"ok": False, "recorded": False, "error": "conversation_id required"}

    workspace = _payload_workspace(payload)
    generation_id = payload.get("generation_id") or payload.get("generationId")
    tool_name = _usage_value(payload, "tool_name", "toolName", "name")
    event_role = {
        "beforeSubmitPrompt": "user",
        "postToolUse": "tool",
        "afterAgentResponse": "assistant",
        "afterAgentThought": "assistant",
        "stop": "assistant",
        "sessionEnd": "system",
    }.get(event_type, "system")
    input_text = _payload_text(
        payload,
        "prompt",
        "content",
        "input_text",
        "inputText",
        "tool_input",
        "toolInput",
        "arguments",
        "command",
        "file_path",
        "filePath",
        "edits",
    )
    output_text = _payload_text(
        payload,
        "output_text",
        "outputText",
        "assistant_output",
        "assistantOutput",
        "tool_output",
        "toolOutput",
        "result",
        "response",
        "output",
        "text",
        "result_json",
        "error_message",
    )
    status = _usage_value(payload, "status", "final_status", "finalStatus")
    paths: set[str] = set()
    for key in (
        "tool_input",
        "toolInput",
        "tool_output",
        "toolOutput",
        "output",
        "result",
        "response",
        "command",
        "transcript_path",
        "transcriptPath",
        "file_path",
        "filePath",
    ):
        if key in payload:
            if re.sub(r"[^a-z0-9]", "", key.lower()) == "transcriptpath":
                continue
            paths.update(_artifact_paths(payload[key], key=key))
    generated_text = (
        _payload_artifact_text(
            payload,
            "assistant_output",
            "assistantOutput",
            "response",
            "output_text",
            "outputText",
            "text",
            "output",
            "content",
        )
        if event_type in {"afterAgentResponse", "stop"}
        else None
    )
    metadata = {
        "source": "hook",
        "hook_event_name": payload.get("hook_event_name") or payload.get("event"),
        "tool_input_type": type(payload.get("tool_input")).__name__,
    }
    now = now_iso()
    event_id = new_id()
    conn = _conn()
    artifacts: list[dict[str, Any]] = []
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO conversation_events(
                  id, conversation_id, generation_id, event_type, role,
                  tool_name, input_text, output_text, status, workspace_root,
                  metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    generation_id,
                    event_type,
                    event_role,
                    str(tool_name) if tool_name is not None else None,
                    input_text,
                    output_text,
                    str(status) if status is not None else None,
                    workspace,
                    dumps(metadata),
                    now,
                ),
            )
            memory.enqueue_event(
                conn,
                event_id,
                payload={"event_type": event_type, "conversation_id": conversation_id},
                at=now,
            )
            if generated_text:
                generated_bytes = generated_text.encode("utf-8")
                blob_sha = blobs.put_blob(
                    conn,
                    generated_bytes,
                    content_type="text/plain; charset=utf-8",
                )
                artifact_id = new_id()
                conn.execute(
                    """
                    INSERT INTO artifacts(
                      id, event_id, conversation_id, generation_id, workspace_root,
                      kind, path, relative_path, name, mime_type, size_bytes,
                      sha256, blob_sha, content_preview, storage_scope,
                      storage_policy, source, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        event_id,
                        conversation_id,
                        generation_id,
                        workspace,
                        "conversation_output",
                        f"conversation://{conversation_id}/{event_id}",
                        None,
                        f"response-{event_id}.txt",
                        "text/plain",
                        len(generated_bytes),
                        blob_sha,
                        blob_sha,
                        truncate(generated_text, 2000),
                        "conversation",
                        "lucid_memories",
                        "hook",
                        dumps(
                            {
                                "event_type": event_type,
                                "tool_name": tool_name,
                                "content_addressed": True,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                artifacts.append(
                    {
                        "id": artifact_id,
                        "kind": "conversation_output",
                        "blob_sha": blob_sha,
                        "storage_scope": "conversation",
                    }
                )
            for raw_path in sorted(paths):
                snapshot = _artifact_snapshot(raw_path, workspace=workspace)
                if snapshot is None:
                    continue
                content_bytes = snapshot.pop("_content_bytes", None)
                blob_sha = None
                if content_bytes is not None:
                    blob_sha = blobs.put_blob(
                        conn,
                        content_bytes,
                        content_type=snapshot["mime_type"]
                        or "application/octet-stream",
                    )
                    snapshot["blob_sha"] = blob_sha
                artifact_id = new_id()
                conn.execute(
                    """
                    INSERT INTO artifacts(
                      id, event_id, conversation_id, generation_id, workspace_root,
                      kind, path, relative_path, name, mime_type, size_bytes,
                      sha256, blob_sha, content_preview, storage_scope,
                      storage_policy, source, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        event_id,
                        conversation_id,
                        generation_id,
                        workspace,
                        "file",
                        snapshot["path"],
                        snapshot["relative_path"],
                        snapshot["name"],
                        snapshot["mime_type"],
                        snapshot["size_bytes"],
                        snapshot["sha256"],
                        snapshot["blob_sha"],
                        snapshot["content_preview"],
                        snapshot["storage_scope"],
                        snapshot["storage_policy"],
                        "hook",
                        dumps(
                            {
                                "event_type": event_type,
                                "tool_name": tool_name,
                                "repository_root": snapshot.get("repository_root"),
                            }
                        ),
                        now,
                        now,
                    ),
                )
                snapshot["id"] = artifact_id
                artifacts.append(snapshot)
        return {
            "ok": True,
            "recorded": True,
            "event_id": event_id,
            "artifact_count": len(artifacts),
        }
    finally:
        conn.close()


def load_artifact(artifact_id: str) -> dict[str, Any]:
    if not artifact_id:
        return {"ok": False, "error": "artifact_id required"}
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "artifact_not_found", "id": artifact_id}
        artifact = row_dict(row)
        content = None
        if row["blob_sha"]:
            content = blobs.get_blob_text(row["blob_sha"])
        artifact["content"] = content
        artifact["content_available"] = content is not None
        artifact["message"] = (
            "lucid-memories blobから復元しました。"
            if content is not None
            else "Workspace/Repositoryの正本を参照してください。lucid-memoriesにはpreviewと索引だけがあります。"
        )
        return {"ok": True, "artifact": artifact}
    finally:
        conn.close()


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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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


PENDING_PROMPT_ID = "_pending"


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
    conn = _conn()
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
    conn = _conn()
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
    conn = _conn()
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


def archive(knowledge_id: str) -> dict[str, Any]:
    if not knowledge_id:
        return {"ok": False, "error": "id required"}
    now = now_iso()
    conn = _conn()
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


_STOP_WORDS = {
    "how",
    "should",
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "for",
    "with",
    "this",
    "that",
    "what",
    "when",
    "where",
    "why",
    "is",
    "are",
    "was",
    "be",
    "do",
    "does",
    "can",
    "could",
    "would",
    "i",
    "we",
    "you",
    "it",
    "vs",
    "from",
}


def _prompt_query(prompt: str, max_chars: int = 180) -> str:
    tokens: list[str] = []
    for token in query_tokens(prompt):
        if token.lower() in _STOP_WORDS:
            continue
        tokens.append(token)
        if len(" ".join(tokens)) >= max_chars:
            break
    if tokens:
        return " ".join(tokens)[:max_chars]
    return " ".join((prompt or "").split())[:max_chars]


def _search_hits_for_prompt(
    prompt: str,
    workspace: str | None,
    *,
    conversation_id: str | None = None,
    generation_id: str | None = None,
) -> list[dict[str, Any]]:
    q = _prompt_query(prompt)
    tokens = [t for t in q.split() if len(t) >= 3][:6]
    queries = [q] + [t for t in tokens if t != q]
    seen: set[str] = set()
    hits: list[dict[str, Any]] = []
    semantic = semantic_search(
        prompt,
        workspace=workspace,
        limit=4,
        conversation_id=conversation_id,
        generation_id=generation_id,
        track=False,
    )
    for item in semantic.get("knowledge") or []:
        kid = str(item.get("id") or "")
        if not kid or kid in seen:
            continue
        seen.add(kid)
        hits.append(item)
        if len(hits) >= 4:
            return hits
    for query in queries:
        if not query:
            continue
        found = search(
            query,
            workspace=workspace,
            limit=4,
            conversation_id=conversation_id,
            generation_id=generation_id,
            track=False,
        )
        for item in found.get("knowledge") or []:
            kid = str(item.get("id") or "")
            if not kid or kid in seen:
                continue
            seen.add(kid)
            hits.append(item)
            if len(hits) >= 4:
                return hits
    return hits


def _flatten_retrieval(
    recall: dict[str, Any],
    knowledge: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    hits: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    dbs: list[str] = []
    seen_hits: set[str] = set()
    seen_links: set[str] = set()

    def add_hit(db: str, kind: str, item_id: Any, title: Any, extra: dict[str, Any] | None = None) -> None:
        key = f"{db}:{item_id or title}"
        if not item_id and not title:
            return
        if key in seen_hits:
            return
        seen_hits.add(key)
        rec = {"db": db, "kind": kind, "id": item_id, "title": title}
        if extra:
            rec.update(extra)
        hits.append(rec)
        if db not in dbs:
            dbs.append(db)

    def add_link(
        rel: str,
        to_db: str,
        to_kind: str,
        to_id: Any,
        to_title: Any,
        *,
        status: Any = None,
        from_id: Any = None,
        from_title: Any = None,
    ) -> None:
        key = f"{rel}:{from_id}:{to_id or to_title}"
        if key in seen_links:
            return
        seen_links.add(key)
        links.append(
            {
                "rel": rel,
                "from_id": from_id,
                "from_title": from_title,
                "to_db": to_db,
                "to_kind": to_kind,
                "to_id": to_id,
                "to_title": to_title,
                "status": status,
            }
        )

    for rank, item in enumerate(knowledge, start=1):
        add_hit(
            "sqlite",
            str(item.get("kind") or "knowledge"),
            item.get("id"),
            item.get("title"),
            {
                "rank": item.get("rank", rank),
                "score": item.get("score"),
                "memory_score": item.get("memory_score"),
            },
        )
    for topic in recall.get("topics") or []:
        add_hit("map", "topic", topic.get("id"), topic.get("title"), {"sense": topic.get("sense")})
        add_link("ABOUT", "map", "topic", topic.get("id"), topic.get("title"), status=topic.get("status"))
    for ctx in recall.get("contexts") or []:
        add_hit("map", "context", ctx.get("id"), ctx.get("title"), {"sense": ctx.get("sense")})
        add_link("IN_CONTEXT", "map", "context", ctx.get("id"), ctx.get("title"), status=ctx.get("status"))
    for proc in recall.get("procedures") or []:
        add_hit(
            "map",
            "procedure",
            proc.get("id"),
            proc.get("title"),
            {"pointer": proc.get("pointer")},
        )
        via = proc.get("triggers") or {}
        add_link(
            str(via.get("rel") or "TRIGGERS"),
            "map",
            "procedure",
            proc.get("id"),
            proc.get("title"),
            status=via.get("status"),
            from_id=via.get("directive_id"),
            from_title=via.get("directive_title"),
        )
        for mat in proc.get("materials") or []:
            db = "sqlite" if mat.get("knowledge_id") else "map"
            add_hit(db, "material", mat.get("knowledge_id") or mat.get("id"), mat.get("title"))
            add_link(
                "USES",
                db,
                "material",
                mat.get("knowledge_id") or mat.get("id"),
                mat.get("title"),
                from_id=proc.get("id"),
                from_title=proc.get("title"),
            )
    return hits, links, dbs


def collect_retrieval(
    prompt: str,
    workspace: str | None = None,
    *,
    conversation_id: str | None = None,
    generation_id: str | None = None,
    source: str = "api",
    request_id: str | None = None,
    track: bool = True,
) -> dict[str, Any]:
    q = _prompt_query(prompt)
    tracking_id = request_id
    if track and tracking_id is None:
        tracking_id = retrieval.start_request(
            "collect_retrieval",
            query=q,
            workspace=workspace,
            conversation_id=conversation_id,
            generation_id=generation_id,
            source=source,
        )
    recall: dict[str, Any] = {"ok": True, "procedures": [], "topics": [], "contexts": []}
    knowledge: list[dict[str, Any]] = []
    if q:
        try:
            from . import graph

            recall = graph.recall(
                q,
                workspace=workspace,
                budget_tokens=400,
                conversation_id=conversation_id,
                generation_id=generation_id,
                track=False,
            )
        except Exception as exc:
            recall = {"ok": False, "error": str(exc), "procedures": [], "topics": [], "contexts": []}
        try:
            knowledge = _search_hits_for_prompt(
                prompt,
                workspace,
                conversation_id=conversation_id,
                generation_id=generation_id,
            )
        except Exception:
            knowledge = []
    hits, links, dbs = _flatten_retrieval(recall, knowledge)
    if tracking_id:
        try:
            retrieval.record_results(
                tracking_id,
                retrieval.normalize_results(hits),
            )
            retrieval.finish_request(tracking_id)
        except Exception:
            retrieval.fail_request(tracking_id)
            raise
    return {
        "query": q,
        "recall": recall,
        "knowledge": knowledge,
        "hits": hits,
        "links": links,
        "dbs": dbs,
        "retrieval_request_id": tracking_id,
    }


def log_retrieval(
    prompt: str,
    *,
    conversation_id: str | None = None,
    prompt_at: str | None = None,
    query: str | None = None,
    source: str = "digest",
    workspace: str | None = None,
    hits: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    dbs: list[str] | None = None,
    expected: list[dict[str, Any]] | None = None,
    matched_count: int | None = None,
    expected_count: int | None = None,
    generation_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        return {"ok": False, "error": "prompt required"}
    hit_list = hits or []
    link_list = links or []
    db_list = dbs or sorted({str(h.get("db")) for h in hit_list if h.get("db")})
    sqlite_n = sum(1 for h in hit_list if h.get("db") == "sqlite")
    map_n = sum(1 for h in hit_list if h.get("db") == "map")
    now = now_iso()
    tracking_id = request_id or retrieval.start_request(
        "log_retrieval",
        query=query,
        workspace=workspace,
        conversation_id=conversation_id,
        generation_id=generation_id,
        source=source,
        at=prompt_at or now,
    )
    retrieval.record_results(
        tracking_id,
        retrieval.normalize_results(hit_list),
        at=prompt_at or now,
    )
    retrieval.finish_request(tracking_id, at=prompt_at or now)
    log_id = new_id()
    conn = _conn()
    try:
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO retrieval_logs(
                  id, conversation_id, request_id, prompt, prompt_at, query, source, workspace_root,
                  dbs_json, links_json, hits_json, sqlite_hit_count, map_hit_count,
                  link_count, expected_json, matched_count, expected_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    conversation_id,
                    tracking_id,
                    text,
                    prompt_at or now,
                    query,
                    source,
                    normalize_root(workspace) if workspace else None,
                    dumps(db_list),
                    dumps(link_list),
                    dumps(hit_list),
                    sqlite_n,
                    map_n,
                    len(link_list),
                    dumps(expected) if expected is not None else None,
                    matched_count,
                    expected_count,
                    now,
                ),
            )
        return {
            "ok": True,
            "id": log_id,
            "request_id": tracking_id,
            "sqlite_hit_count": sqlite_n,
            "map_hit_count": map_n,
            "link_count": len(link_list),
            "dbs": db_list,
        }
    finally:
        conn.close()


def retrieval_gaps(
    hits: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[str]:
    has_sqlite = any(h.get("db") == "sqlite" for h in hits)
    has_map = any(h.get("db") == "map" for h in hits)
    has_proc = any(h.get("kind") == "procedure" for h in hits)
    has_topic = any(h.get("kind") == "topic" for h in hits)
    has_ctx = any(h.get("kind") == "context" for h in hits)
    has_uses = any(str(link.get("rel") or "").upper() == "USES" for link in links)
    gaps: list[str] = []
    if not hits:
        gaps.append("empty")
        return gaps
    if has_sqlite and not has_map:
        gaps.append("sqlite_only")
    if (has_topic or has_ctx) and not has_proc:
        gaps.append("map_without_procedure")
    if has_proc and has_sqlite and not has_uses:
        gaps.append("procedure_without_material")
    return gaps


def _patch_retrieval_eval(
    log_id: str,
    gaps: list[str],
    improved: list[dict[str, Any]],
) -> None:
    conn = _conn()
    try:
        with write_tx(conn):
            conn.execute(
                "UPDATE retrieval_logs SET gaps_json = ?, improved_json = ? WHERE id = ?",
                (dumps(gaps), dumps(improved), log_id),
            )
    finally:
        conn.close()


def improve_retrieval(
    prompt: str,
    collected: dict[str, Any],
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
    log_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    hits = list(collected.get("hits") or [])
    links = list(collected.get("links") or [])
    gaps = retrieval_gaps(hits, links)
    proposed: list[dict[str, Any]] = []
    if not apply or not gaps or gaps == ["empty"]:
        if log_id:
            _patch_retrieval_eval(log_id, gaps, proposed)
        return {"ok": True, "gaps": gaps, "proposed": proposed}
    topics = [h for h in hits if h.get("kind") == "topic" and h.get("title")]
    contexts = [h for h in hits if h.get("kind") == "context" and h.get("title")]
    procs = [h for h in hits if h.get("kind") == "procedure" and h.get("title")]
    dirs = [h for h in hits if h.get("kind") == "directive" and h.get("title")]
    sqlite_hits = [h for h in hits if h.get("db") == "sqlite" and h.get("id")]
    needles = query_tokens(prompt) + [str(t.get("title") or "") for t in topics]

    def _score(item: dict[str, Any]) -> int:
        title = str(item.get("title") or "").lower()
        return sum(1 for needle in needles if needle and needle.lower() in title)

    sqlite_hits = [item for item in sqlite_hits if _score(item) > 0]
    sqlite_hits.sort(key=_score, reverse=True)
    from . import graph

    def _same_name(left: str, right: str) -> bool:
        return " ".join(left.strip().lower().split()) == " ".join(right.strip().lower().split())

    def _propose(**kwargs: Any) -> None:
        if len(proposed) >= 4:
            return
        kwargs.setdefault("workspace", workspace)
        kwargs.setdefault("conversation_id", conversation_id)
        kwargs.setdefault("source", "auto")
        kwargs.setdefault("label", "eval improve")
        result = graph.link(**kwargs)
        proposed.append(
            {
                "rel": kwargs.get("rel"),
                "from": kwargs.get("from_ref"),
                "to": kwargs.get("to_ref"),
                "ok": bool(result.get("ok")),
                "status": (result.get("edge") or {}).get("status"),
                "error": result.get("error"),
            }
        )

    about_sources = [(d, "directive") for d in dirs[:2]] + [(p, "procedure") for p in procs[:2]]
    for src, src_type in about_sources:
        src_title = str(src.get("title") or "")
        if not src_title:
            continue
        for topic in topics[:2]:
            title = str(topic.get("title") or "")
            if title:
                _propose(
                    from_ref=src_title,
                    to_ref=title,
                    rel="ABOUT",
                    from_type=src_type,
                    to_type="topic",
                    sense="topic",
                )
        for ctx in contexts[:1]:
            title = str(ctx.get("title") or "")
            if title:
                _propose(
                    from_ref=src_title,
                    to_ref=title,
                    rel="IN_CONTEXT",
                    from_type=src_type,
                    to_type="context",
                    sense="context",
                )
    if "map_without_procedure" in gaps:
        for directive in dirs[:2]:
            d_title = str(directive.get("title") or "")
            for proc in procs[:2]:
                p_title = str(proc.get("title") or "")
                if not d_title or not p_title or _same_name(d_title, p_title):
                    continue
                _propose(
                    from_ref=d_title,
                    to_ref=p_title,
                    rel="TRIGGERS",
                    from_type="directive",
                    to_type="procedure",
                    sense="related",
                )
    if "map_without_procedure" in gaps or "procedure_without_material" in gaps:
        for proc in procs[:2]:
            p_title = str(proc.get("title") or "")
            if not p_title:
                continue
            for item in sqlite_hits[:2]:
                title = str(item.get("title") or "")
                if not title:
                    continue
                _propose(
                    from_ref=p_title,
                    to_ref=title,
                    rel="USES",
                    from_type="procedure",
                    to_type="material",
                    knowledge_id=str(item.get("id")),
                    sense="related",
                )
    if log_id:
        _patch_retrieval_eval(log_id, gaps, proposed)
    return {"ok": True, "gaps": gaps, "proposed": proposed}


def improve_from_logs(
    limit: int = 20,
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM retrieval_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        hits = json.loads(row["hits_json"] or "[]")
        links = json.loads(row["links_json"] or "[]")
        gaps = retrieval_gaps(hits, links)
        already = row["improved_json"] if "improved_json" in row.keys() else None
        if already:
            continue
        if not gaps or gaps == ["empty"]:
            continue
        collected = {
            "query": row["query"],
            "hits": hits,
            "links": links,
        }
        results.append(
            improve_retrieval(
                row["prompt"],
                collected,
                workspace=workspace or row["workspace_root"],
                conversation_id=conversation_id or row["conversation_id"],
                log_id=row["id"],
                apply=True,
            )
        )
    return {"ok": True, "n": len(results), "results": results, "summary": measure_retrieval(limit)}


def _match_expected(hits: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    used: set[int] = set()
    for exp in expected:
        exp_id = str(exp.get("id") or "")
        exp_title = str(exp.get("title") or "").strip().lower()
        exp_db = str(exp.get("db") or "")
        exp_kind = str(exp.get("kind") or "")
        for idx, hit in enumerate(hits):
            if idx in used:
                continue
            if exp_db and hit.get("db") != exp_db:
                continue
            if exp_kind and str(hit.get("kind") or "") != exp_kind:
                continue
            hit_id = str(hit.get("id") or "")
            hit_title = str(hit.get("title") or "").strip().lower()
            id_ok = bool(exp_id) and hit_id == exp_id
            title_ok = bool(exp_title) and (hit_title == exp_title or exp_title in hit_title or hit_title in exp_title)
            if id_ok or title_ok:
                used.add(idx)
                matched.append(hit)
                break
    return matched


def _rate(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 4)


def measure_retrieval(limit: int = 50) -> dict[str, Any]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM retrieval_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        n = len(rows)
        any_hit = 0
        sqlite_hit = 0
        map_hit = 0
        linked = 0
        eval_n = 0
        eval_matched = 0
        eval_expected = 0
        gapped = 0
        recent: list[dict[str, Any]] = []
        for row in rows:
            sqlite_n = int(row["sqlite_hit_count"] or 0)
            map_n = int(row["map_hit_count"] or 0)
            if sqlite_n + map_n > 0:
                any_hit += 1
            if sqlite_n > 0:
                sqlite_hit += 1
            if map_n > 0:
                map_hit += 1
            if int(row["link_count"] or 0) > 0:
                linked += 1
            if row["expected_count"] is not None:
                eval_n += 1
                eval_matched += int(row["matched_count"] or 0)
                eval_expected += int(row["expected_count"] or 0)
            keys = row.keys()
            gaps = json.loads(row["gaps_json"] or "[]") if "gaps_json" in keys and row["gaps_json"] else []
            if gaps:
                gapped += 1
            recent.append(
                {
                    "id": row["id"],
                    "prompt": row["prompt"],
                    "prompt_at": row["prompt_at"],
                    "source": row["source"],
                    "dbs": json.loads(row["dbs_json"] or "[]"),
                    "sqlite_hit_count": sqlite_n,
                    "map_hit_count": map_n,
                    "link_count": int(row["link_count"] or 0),
                    "matched_count": row["matched_count"],
                    "expected_count": row["expected_count"],
                    "gaps": gaps,
                }
            )
        return {
            "ok": True,
            "n": n,
            "hit_rate": _rate(any_hit, n),
            "empty_rate": _rate(n - any_hit, n),
            "sqlite_hit_rate": _rate(sqlite_hit, n),
            "map_hit_rate": _rate(map_hit, n),
            "link_rate": _rate(linked, n),
            "gap_rate": _rate(gapped, n),
            "eval_n": eval_n,
            "eval_recall": _rate(eval_matched, eval_expected),
            "recent": recent,
        }
    finally:
        conn.close()


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
    conn = connect_readonly(database) if database is not None else _conn()
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


DEFAULT_EVAL_CASES = [
    {
        "prompt": "指示パイプラインで検索と提案書き込みをして",
        "expect": [
            {"db": "map", "kind": "topic", "title": "指示パイプライン"},
            {"db": "sqlite", "title": "指示パイプラインは Auto、確定だけ Ask"},
        ],
    },
    {
        "prompt": "lucid-memories の文脈でパイプラインを引け",
        "expect": [
            {"db": "map", "kind": "context", "title": "lucid-memories"},
        ],
    },
]


def eval_retrieval(
    cases: list[dict[str, Any]],
    *,
    workspace: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    if not cases:
        return {"ok": False, "error": "cases required"}
    results: list[dict[str, Any]] = []
    matched_all = 0
    expected_all = 0
    any_hit = 0
    for case in cases:
        prompt = str(case.get("prompt") or "").strip()
        expected = list(case.get("expect") or [])
        collected = collect_retrieval(
            prompt,
            workspace,
            conversation_id=conversation_id,
            source="eval",
        )
        matched = _match_expected(collected["hits"], expected)
        expected_all += len(expected)
        matched_all += len(matched)
        if collected["hits"]:
            any_hit += 1
        logged = log_retrieval(
            prompt,
            conversation_id=conversation_id,
            query=collected["query"],
            source="eval",
            workspace=workspace,
            hits=collected["hits"],
            links=collected["links"],
            dbs=collected["dbs"],
            expected=expected,
            matched_count=len(matched),
            expected_count=len(expected),
            request_id=collected.get("retrieval_request_id"),
        )
        results.append(
            {
                "prompt": prompt,
                "ok": logged.get("ok"),
                "dbs": collected["dbs"],
                "hits": collected["hits"],
                "links": collected["links"],
                "matched_count": len(matched),
                "expected_count": len(expected),
            }
        )
    n = len(cases)
    return {
        "ok": True,
        "n": n,
        "hit_rate": _rate(any_hit, n),
        "eval_recall": _rate(matched_all, expected_all),
        "matched": matched_all,
        "expected": expected_all,
        "cases": results,
        "summary": measure_retrieval(),
    }


def _digest_memory_lines(
    prompt: str,
    workspace: str | None,
    *,
    conversation_id: str | None = None,
    prompt_at: str | None = None,
    generation_id: str | None = None,
) -> list[str]:
    collected = collect_retrieval(
        prompt,
        workspace,
        conversation_id=conversation_id,
        generation_id=generation_id,
        source="digest",
    )
    q = collected["query"]
    if not q:
        return []
    try:
        logged = log_retrieval(
            prompt,
            conversation_id=conversation_id,
            prompt_at=prompt_at,
            query=q,
            source="digest",
            workspace=workspace,
            hits=collected["hits"],
            links=collected["links"],
            dbs=collected["dbs"],
            generation_id=generation_id,
            request_id=collected.get("retrieval_request_id"),
        )
    except Exception:
        logged = {}
    try:
        improved = improve_retrieval(
            prompt,
            collected,
            workspace=workspace,
            conversation_id=conversation_id,
            log_id=logged.get("id"),
            apply=False,
        )
    except Exception as exc:
        improved = {"gaps": [], "proposed": [], "error": str(exc)}
    lines: list[str] = []
    rec = collected["recall"]
    procs = rec.get("procedures") or []
    topics = rec.get("topics") or []
    ctxs = rec.get("contexts") or []
    if rec.get("ok") is False:
        lines.append("recall: (skipped)")
    elif procs or topics or ctxs:
        lines.append("recall:")
        for proc in procs[:4]:
            title = proc.get("title") or proc.get("id")
            lines.append(f"  Procedure {title}")
        for topic in topics[:2]:
            lines.append(f"  Topic {topic.get('title') or topic.get('id')}")
        for ctx in ctxs[:2]:
            lines.append(f"  Context {ctx.get('title') or ctx.get('id')}")
    else:
        lines.append("recall: (none)")
    items = collected["knowledge"]
    if items:
        lines.append("search:")
        for item in items[:3]:
            lines.append(f"  {item.get('kind')} {item.get('id')} {item.get('title')}")
    else:
        lines.append("search: (none)")
    if improved.get("error"):
        lines.append("eval: error=" + str(improved.get("error")))
    else:
        gaps = improved.get("gaps") or []
        lines.append("eval: gaps=" + (",".join(gaps) if gaps else "none"))
    proposed = [p for p in (improved.get("proposed") or []) if p.get("ok")]
    if proposed:
        bits = [f"{p.get('rel')} {p.get('from')} -> {p.get('to')}" for p in proposed[:4]]
        lines.append("improve: proposed " + "; ".join(bits))
    lines.append(
        "Auto: persist proposed remember/link this turn; ask the user only for confirm/forbid/archive."
    )
    return lines


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


def generation_changed(conversation_id: str, generation_id: str | None) -> bool:
    if not generation_id:
        return False
    conn = _conn()
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
