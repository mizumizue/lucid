from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from lucid_memories.storage import blobs
from . import memory
from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.storage.paths import env
from lucid_memories.runtime.util import dumps, estimate_tokens, new_id, normalize_root, row_dict, truncate
from . import api_common
from .identity_ops import whoami

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
    conn = api_common._conn()
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

def record_mcp_usage(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    result_text: str | None = None,
) -> dict[str, Any]:
    """Record MCP tool-result size without mixing it into model token totals."""
    arguments = arguments or {}
    payload = result if isinstance(result, dict) else {}
    conversation_id = arguments.get("conversation_id") or arguments.get("conversationId")
    if not conversation_id:
        conversation_id = whoami(workspace=arguments.get("workspace")).get("conversation_id")
    if not conversation_id:
        return {"ok": True, "recorded": False, "reason": "conversation_id required"}

    text = result_text
    if text is None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    tokens = estimate_tokens(text)
    generation_id = arguments.get("generation_id") or arguments.get("generationId")
    budget = payload.get("budget")
    if budget is None:
        budget = arguments.get("budget_tokens")
    metadata = {
        "source": "mcp",
        "tool_name": tool_name,
        "budget": budget,
        "tokens_used": payload.get("tokens_used"),
        "budget_tokens": arguments.get("budget_tokens"),
    }
    now = now_iso()
    conn = api_common._conn()
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
                    "mcp",
                    None,
                    None,
                    tokens,
                    None,
                    None,
                    tokens,
                    None,
                    None,
                    None,
                    tool_name,
                    None,
                    dumps(metadata),
                    now,
                ),
            )
        return {"ok": True, "recorded": True, "created_at": now, "tokens": tokens}
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
    conn = api_common._conn()
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
    conn = api_common._conn()
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
