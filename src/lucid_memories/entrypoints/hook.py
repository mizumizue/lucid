#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lucid_memories.core.api import (
    bind_current_session,
    digest_text,
    ensure_session,
    generation_changed,
    heartbeat,
    job_start,
    job_update,
    post_notice,
    record_activity,
    record_usage,
    release_leases_for_session,
    save_prompt,
    snapshot_compact,
    unbind_current_session,
)
from lucid_memories.core import api
from lucid_memories.core.gate import mcp_permission, pre_tool_permission, shell_permission
from lucid_memories.core import persona
from lucid_memories.storage.db import connect, record_runtime_log
from lucid_memories.storage.paths import env
from lucid_memories.runtime.util import truncate

SYSTEM_BOOTSTRAP_PROMPT = (
    "[lucid-memories bootstrap]\n"
    "作業開始時に whoami と status を一度呼べ。compact 後は reload。\n"
    "ユーザー指示が回ったら search / recall / proposed remember / proposed link は Agent が自動で行う。\n"
    "ユーザー承認は confirm / forbid / archive のみ。他 Agent と共有すべき決定・ポインタは remember。\n"
    "CLI または MCP lucid-memories を使い、生 SQL / 生 Cypher は書かない。"
)


def _log_error(exc: BaseException, raw: str | None = None) -> None:
    try:
        parts = [f"{exc!r}\n"]
        if raw is not None:
            parts.append(f"raw ({len(raw)} chars): {raw[:500]!r}\n")
        parts.append(f"{traceback.format_exc()}\n---\n")
        conn = connect()
        try:
            record_runtime_log(conn, "error.log", "".join(parts), metadata={"level": "error"})
        finally:
            conn.close()
    except Exception:
        pass


def _log_hook_call(event: str, cid: str | None) -> None:
    try:
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = connect()
        try:
            record_runtime_log(
                conn,
                "hook_calls.log",
                f"[{now}] event={event} cid={cid}\n",
                created_at=now,
                metadata={"event": event, "conversation_id": cid},
            )
        finally:
            conn.close()
    except Exception:
        pass


def _roots(payload: dict[str, Any]) -> list[str]:
    roots = (
        payload.get("workspace_roots")
        or payload.get("workspaceRoots")
        or payload.get("workspace")
        or []
    )
    if isinstance(roots, str):
        return [roots]
    return [str(r) for r in roots if r]


def _workspace(payload: dict[str, Any]) -> str | None:
    roots = _roots(payload)
    return roots[0] if roots else None


def _conversation_id(payload: dict[str, Any]) -> str | None:
    return (
        payload.get("conversation_id")
        or payload.get("session_id")
        or payload.get("conversationId")
        or payload.get("sessionId")
        or None
    )


def _refresh_session(payload: dict[str, Any]) -> str | None:
    cid = _conversation_id(payload)
    if not cid:
        return None
    ensure_session(
        cid,
        workspace_roots=_roots(payload),
        composer_mode=payload.get("composer_mode"),
        is_background=bool(payload.get("is_background_agent")),
        model=payload.get("model") or payload.get("model_id"),
        transcript_path=payload.get("transcript_path"),
        status="active",
        generation_id=payload.get("generation_id") or None,
    )
    bind_current_session(cid, workspace=_workspace(payload))
    return cid


def _record_activity(payload: dict[str, Any], event_type: str) -> None:
    try:
        record_activity(payload, event_type=event_type)
    except Exception as exc:
        _log_error(exc)


def _record_usage(payload: dict[str, Any], event_type: str) -> None:
    try:
        record_usage(payload, event_type=event_type)
    except Exception as exc:
        _log_error(exc)


def _run_memory_worker() -> None:
    if str(env("MEMORY_WORKER", "1") or "1").lower() in {
        "0",
        "false",
        "no",
    }:
        return
    try:
        api.memory_worker(limit=2, sweep_faded=False)
    except Exception as exc:
        _log_error(exc)


def _run_persona_worker() -> None:
    if str(env("PERSONA_AUTO_APPLY", "1") or "1").lower() in {
        "0",
        "false",
        "no",
    }:
        return
    try:
        persona.persona_worker(limit=2, auto_apply=True)
    except Exception as exc:
        _log_error(exc)


MOJIBAKE_CHARS = set("縺荳隕繝繧縲縢豁螳蟇菴\ufffd")


def is_mojibake(text: str | None) -> bool:
    if not text:
        return False
    return any(ch in text for ch in MOJIBAKE_CHARS)


def _latest_transcript_prompt(cid: str) -> str | None:
    if not cid:
        return None
    transcripts_base = Path.home() / ".cursor" / "projects"
    matches = list(transcripts_base.glob(f"**/{cid}.jsonl"))
    if not matches:
        return None
    try:
        latest = None
        with open(matches[0], "r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                data = json.loads(line)
                if data.get("role") == "user":
                    for c in data.get("message", {}).get("content", []):
                        t = c.get("text", "")
                        if "<user_query>" in t:
                            latest = (
                                t.split("<user_query>")[1]
                                .split("</user_query>")[0]
                                .strip()
                            )
        return latest
    except Exception:
        return None


def sanitize_prompt(prompt: str, cid: str | None = None) -> str:
    if not prompt or not is_mojibake(prompt):
        return prompt
    if cid:
        recovered = _latest_transcript_prompt(cid)
        if recovered:
            return recovered
    try:
        raw = prompt.encode("cp932", errors="ignore")
        decoded = raw.decode("utf-8", errors="ignore")
        if decoded:
            return decoded
    except Exception:
        pass
    return prompt


def handle_before_submit_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    _refresh_session(payload)
    cid = _conversation_id(payload)
    raw_prompt = payload.get("prompt") or payload.get("content") or ""
    prompt = sanitize_prompt(str(raw_prompt), cid=cid) if raw_prompt else ""
    if "prompt" in payload:
        payload["prompt"] = prompt
    if "content" in payload:
        payload["content"] = prompt

    if str(prompt).strip():
        save_prompt(
            str(prompt),
            conversation_id=cid,
            workspace=_workspace(payload),
        )
        _record_activity(payload, event_type="beforeSubmitPrompt")

    try:
        injection = persona.get_injection()
        content = (injection.get("content") or "").strip()
        parts = [SYSTEM_BOOTSTRAP_PROMPT]
        if content:
            parts.append(content)
        return {"additional_context": "\n\n".join(parts)}
    except FileNotFoundError:
        return {"additional_context": SYSTEM_BOOTSTRAP_PROMPT}
    except Exception as exc:
        _log_error(exc)
        return {}


def handle_before_mcp_execution(payload: dict[str, Any]) -> dict[str, Any]:
    _refresh_session(payload)
    return mcp_permission(
        tool_name=payload.get("tool_name") or payload.get("toolName"),
        tool_input=payload.get("tool_input") or payload.get("arguments") or payload.get("input"),
        mcp_server_name=payload.get("mcp_server_name") or payload.get("server"),
        command=payload.get("command"),
    )


def handle_before_shell_execution(payload: dict[str, Any]) -> dict[str, Any]:
    ws = _workspace(payload)
    repo_root = Path(ws) if ws else None
    return shell_permission(payload.get("command"), repo_root=repo_root)


def handle_pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    ws = _workspace(payload)
    repo_root = Path(ws) if ws else None
    return pre_tool_permission(payload, repo_root=repo_root)


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    cid = _refresh_session(payload)
    if not cid:
        return {}
    _record_activity(payload, event_type="sessionStart")
    return {
        "env": {
            "LUCID_MEMORIES_CONVERSATION_ID": cid,
        }
    }


def handle_post_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    cid = _conversation_id(payload)
    if not cid:
        return {}
    _record_activity(payload, event_type="postToolUse")
    _record_usage(payload, event_type="postToolUse")
    _run_memory_worker()
    _run_persona_worker()
    gen = payload.get("generation_id") or None
    changed = generation_changed(cid, gen)
    heartbeat(
        cid,
        generation_id=gen,
        status="active",
        workspace_roots=_roots(payload),
        model=payload.get("model") or payload.get("model_id"),
        transcript_path=payload.get("transcript_path"),
    )
    if not changed:
        return {}
    return {
        "additional_context": digest_text(
            cid,
            workspace=_workspace(payload),
            generation_id=payload.get("generation_id"),
        )
    }


def handle_post_tool_use_failure(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="postToolUseFailure")
    return {}


def handle_after_shell_execution(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="afterShellExecution")
    return {}


def handle_after_mcp_execution(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="afterMCPExecution")
    return {}


def handle_after_file_edit(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="afterFileEdit")
    return {}


def handle_after_agent_response(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="afterAgentResponse")
    return {}


def handle_after_agent_thought(payload: dict[str, Any]) -> dict[str, Any]:
    if not _conversation_id(payload):
        return {}
    _record_activity(payload, event_type="afterAgentThought")
    return {}


def handle_subagent_start(payload: dict[str, Any]) -> dict[str, Any]:
    parent = payload.get("parent_conversation_id") or _conversation_id(payload)
    if not parent:
        return {}
    ensure_session(
        parent,
        workspace_roots=_roots(payload),
        status="active",
        generation_id=payload.get("generation_id") or None,
    )
    sub_id = payload.get("subagent_id")
    if sub_id and sub_id != parent:
        ensure_session(
            sub_id,
            parent_conversation_id=parent,
            workspace_roots=_roots(payload),
            status="active",
            model=payload.get("subagent_model"),
        )
    job_start(
        title=truncate(payload.get("task") or payload.get("description") or "subagent", 200) or "subagent",
        kind="subagent",
        conversation_id=parent,
        workspace=_workspace(payload),
        summary=payload.get("task"),
        subagent_id=sub_id,
        subagent_type=payload.get("subagent_type"),
        tool_call_id=payload.get("tool_call_id"),
        source="hook",
    )
    return {}


def handle_subagent_stop(payload: dict[str, Any]) -> dict[str, Any]:
    parent = payload.get("parent_conversation_id") or _conversation_id(payload)
    status_map = {"completed": "done", "error": "error", "aborted": "aborted"}
    terminal = status_map.get(payload.get("status") or "", "done")
    sub_id = payload.get("subagent_id")
    from lucid_memories.storage.db import connect

    conn = connect()
    try:
        row = None
        if sub_id:
            row = conn.execute("SELECT * FROM jobs WHERE subagent_id = ?", (sub_id,)).fetchone()
        if row is None and parent:
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE conversation_id = ? AND kind = 'subagent' AND status = 'running'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (parent,),
            ).fetchone()
    finally:
        conn.close()
    if row is not None:
        job_update(
            row["id"],
            row["rev"],
            status=terminal,
            summary=payload.get("summary") or payload.get("task"),
            source="hook",
        )
        post_notice(
            kind="job_done",
            body=truncate(payload.get("summary") or payload.get("task") or row["title"], 500),
            to_conversation_id=parent or row["conversation_id"],
            from_conversation_id=parent or row["conversation_id"],
            workspace=_workspace(payload),
        )
    return {}


def handle_pre_compact(payload: dict[str, Any]) -> dict[str, Any]:
    cid = _conversation_id(payload)
    if not cid:
        return {}
    result = snapshot_compact(
        cid,
        workspace=_workspace(payload),
        generation_id=payload.get("generation_id"),
        trigger=payload.get("trigger"),
        context_usage_percent=payload.get("context_usage_percent"),
        context_tokens=payload.get("context_tokens"),
        context_window_size=payload.get("context_window_size"),
        message_count=payload.get("message_count"),
        messages_to_compact=payload.get("messages_to_compact"),
        is_first_compaction=payload.get("is_first_compaction"),
    )
    pack_id = result.get("pack_id") or ""
    return {
        "user_message": f"lucid-memories: compact snapshot saved ({pack_id}). Reload with lucid-memories reload."
    }


def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    cid = _conversation_id(payload)
    if not cid:
        return {}
    _record_activity(payload, event_type="stop")
    _record_usage(payload, event_type="stop")
    heartbeat(
        cid,
        generation_id=payload.get("generation_id"),
        status="idle",
        workspace_roots=_roots(payload),
        model=payload.get("model") or payload.get("model_id"),
        transcript_path=payload.get("transcript_path"),
    )
    return {}


def handle_session_end(payload: dict[str, Any]) -> dict[str, Any]:
    cid = _conversation_id(payload)
    if not cid:
        return {}
    _record_activity(payload, event_type="sessionEnd")
    _record_usage(payload, event_type="sessionEnd")
    ensure_session(
        cid,
        workspace_roots=_roots(payload),
        status="ended",
        ended_reason=payload.get("reason") or payload.get("final_status"),
        generation_id=payload.get("generation_id"),
    )
    unbind_current_session(cid)
    release_leases_for_session(cid)
    return {}


HANDLERS = {
    "sessionStart": handle_session_start,
    "beforeSubmitPrompt": handle_before_submit_prompt,
    "preToolUse": handle_pre_tool_use,
    "postToolUse": handle_post_tool_use,
    "postToolUseFailure": handle_post_tool_use_failure,
    "beforeMCPExecution": handle_before_mcp_execution,
    "afterMCPExecution": handle_after_mcp_execution,
    "beforeShellExecution": handle_before_shell_execution,
    "afterShellExecution": handle_after_shell_execution,
    "afterFileEdit": handle_after_file_edit,
    "afterAgentResponse": handle_after_agent_response,
    "afterAgentThought": handle_after_agent_thought,
    "subagentStart": handle_subagent_start,
    "subagentStop": handle_subagent_stop,
    "preCompact": handle_pre_compact,
    "stop": handle_stop,
    "sessionEnd": handle_session_end,
}


def handle_hook(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("hook_event_name") or payload.get("event") or ""
    handler = HANDLERS.get(event)
    if handler is None:
        return {}
    return handler(payload) or {}


def main() -> None:
    raw = ""
    try:
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

        try:
            raw_bytes = sys.stdin.buffer.read()
            try:
                raw = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    raw = raw_bytes.decode("cp932")
                except UnicodeDecodeError:
                    raw = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            raw = sys.stdin.read()

        stripped = raw.lstrip("\ufeff").strip()
        if not stripped:
            _log_hook_call("empty_stdin", None)
            sys.stdout.write("{}")
            sys.stdout.flush()
            return

        payload = json.loads(stripped)
        event = payload.get("hook_event_name") or payload.get("event") or "unknown"
        cid = _conversation_id(payload)
        _log_hook_call(event, cid)

        out = handle_hook(payload)
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        _log_error(exc, raw=raw)
        sys.stdout.write("{}")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
