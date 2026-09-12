#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lucid_memories.core import api
from lucid_memories.core import graph
from lucid_memories.storage.paths import DEFAULT_LOAD_BUDGET

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lucid-memories"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "whoami",
        "description": "Resolve this agent session's conversation_id on lucid-memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
        },
    },
    {
        "name": "status",
        "description": "List live sessions, running jobs, unread notices, and compact reload availability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
        },
    },
    {
        "name": "search",
        "description": "Full-text search knowledge and packs on lucid-memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace": {"type": "string"},
                "limit": {"type": "integer"},
                "kind": {"type": "string"},
                "conversation_id": {"type": "string"},
                "generation_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vsearch",
        "description": "Semantic vector search over knowledge stored in SQLite.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace": {"type": "string"},
                "kind": {"type": "string"},
                "limit": {"type": "integer"},
                "min_score": {"type": "number"},
                "conversation_id": {"type": "string"},
                "generation_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "embedding_status",
        "description": "Show the local embedding provider and indexed vector counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "list",
        "description": "List knowledge on lucid-memories, optionally filtered by kind (idea, fact, decision, ...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "workspace": {"type": "string"},
                "limit": {"type": "integer"},
                "any_workspace": {"type": "boolean"},
            },
        },
    },
    {
        "name": "load",
        "description": "Load a pack or knowledge by id or query, within a token budget.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "budget_tokens": {"type": "integer"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
                "generation_id": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "remember",
        "description": "Write knowledge onto lucid-memories for other agents to load.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "kind": {"type": "string"},
                "scope": {"type": "string"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
                "importance": {"type": "number"},
                "salience": {"type": "number"},
                "decay_half_life_days": {"type": "number"},
                "expires_at": {"type": "string"},
                "source_event_id": {"type": "string"},
                "provenance": {"type": "object"},
                "id": {"type": "string"},
                "rev": {"type": "integer"},
                "created_at": {"type": "string"},
            },
        },
    },
    {
        "name": "job",
        "description": "Start, update, complete, or claim a job on lucid-memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "update", "done", "claim"]},
                "title": {"type": "string"},
                "kind": {"type": "string"},
                "summary": {"type": "string"},
                "id": {"type": "string"},
                "rev": {"type": "integer"},
                "status": {"type": "string"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "reload",
        "description": "Reload the latest compact_snapshot pack for this session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "budget_tokens": {"type": "integer"},
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "relay",
        "description": "Save or load a handoff Pack so another agent can continue without a SubAgent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "load"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "suggested_skills": {"type": "array", "items": {"type": "string"}},
                "focus": {"type": "string"},
                "scope": {"type": "string"},
                "pack_id": {"type": "string"},
                "query": {"type": "string"},
                "budget_tokens": {"type": "integer"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "recall",
        "description": "Recall procedures and materials for this kind of instruction from the Map (not a full knowledge dump).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace": {"type": "string"},
                "budget_tokens": {"type": "integer"},
                "conversation_id": {"type": "string"},
                "generation_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "link",
        "description": "Link Map nodes by meaning: ABOUT (topic), IN_CONTEXT (context), RELATED, TRIGGERS, USES.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ref": {"type": "string"},
                "to_ref": {"type": "string"},
                "rel": {"type": "string"},
                "confirm": {"type": "boolean"},
                "role": {"type": "string"},
                "pointer": {"type": "string"},
                "subtype": {"type": "string"},
                "knowledge_id": {"type": "string"},
                "from_type": {"type": "string"},
                "to_type": {"type": "string"},
                "sense": {"type": "string"},
                "label": {"type": "string"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
            "required": ["from_ref", "to_ref"],
        },
    },
    {
        "name": "forbid",
        "description": "Teach the Map that two nodes must not be related.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ref": {"type": "string"},
                "to_ref": {"type": "string"},
                "reason": {"type": "string"},
                "workspace": {"type": "string"},
                "from_type": {"type": "string"},
                "to_type": {"type": "string"},
            },
            "required": ["from_ref", "to_ref", "reason"],
        },
    },
    {
        "name": "confirm",
        "description": "Confirm a proposed Map edge so recall will use it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ref": {"type": "string"},
                "to_ref": {"type": "string"},
                "rel": {"type": "string"},
                "workspace": {"type": "string"},
            },
            "required": ["from_ref", "to_ref"],
        },
    },
    {
        "name": "map",
        "description": "Show Map node and edge counts (no document bodies).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "archive",
        "description": "Hide knowledge from search by expiring it now. Ask the user before calling.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "artifact",
        "description": "Load a lucid-memories stored conversation artifact or inspect its workspace/repository pointer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["load"]},
                "id": {"type": "string"},
            },
            "required": ["action", "id"],
        },
    },
    {
        "name": "measure",
        "description": "Retrieval hit-rate. eval runs known cases; improve backfills proposed links from logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "eval": {"type": "boolean"},
                "improve": {"type": "boolean"},
                "limit": {"type": "integer"},
                "workspace": {"type": "string"},
                "conversation_id": {"type": "string"},
                "cases": {"type": "array"},
            },
        },
    },
    {
        "name": "memory",
        "description": "Inspect lifecycle state, run bounded consolidation, list candidates, or promote a candidate memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "worker", "candidates", "promote"],
                },
                "limit": {"type": "integer"},
                "status": {"type": "string"},
                "candidate_id": {"type": "string"},
                "workspace": {"type": "string"},
                "sweep_faded": {"type": "boolean"},
            },
            "required": ["action"],
        },
    },
]


def _call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "whoami":
        return api.whoami(
            workspace=args.get("workspace"),
            conversation_id=args.get("conversation_id"),
        )
    if name == "status":
        return api.status(
            workspace=args.get("workspace"),
            conversation_id=args.get("conversation_id"),
        )
    if name == "search":
        return api.search(
            args.get("query") or "",
            workspace=args.get("workspace"),
            limit=int(args.get("limit") or 20),
            kind=args.get("kind"),
            conversation_id=args.get("conversation_id"),
            generation_id=args.get("generation_id"),
            source="mcp",
        )
    if name == "vsearch":
        return api.semantic_search(
            args.get("query") or "",
            workspace=args.get("workspace"),
            kind=args.get("kind"),
            limit=int(args.get("limit") or 8),
            min_score=float(args.get("min_score") or 0.20),
            conversation_id=args.get("conversation_id"),
            generation_id=args.get("generation_id"),
            source="mcp",
        )
    if name == "embedding_status":
        return api.embedding_status()
    if name == "list":
        return api.list_knowledge(
            kind=args.get("kind"),
            workspace=args.get("workspace"),
            limit=int(args.get("limit") or 50),
            any_workspace=bool(args.get("any_workspace")),
        )
    if name == "load":
        return api.load(
            args.get("target") or "",
            budget_tokens=int(args.get("budget_tokens") or DEFAULT_LOAD_BUDGET),
            workspace=args.get("workspace"),
            conversation_id=args.get("conversation_id"),
            generation_id=args.get("generation_id"),
            source="mcp",
        )
    if name == "remember":
        return api.remember(
            args.get("title"),
            args.get("body"),
            kind=args.get("kind"),
            scope=args.get("scope") or "workspace",
            workspace=args.get("workspace"),
            conversation_id=args.get("conversation_id"),
            tags=args.get("tags") if "tags" in args else None,
            confidence=args.get("confidence"),
            importance=args.get("importance"),
            salience=args.get("salience"),
            decay_half_life_days=args.get("decay_half_life_days"),
            expires_at=args.get("expires_at"),
            source_event_id=args.get("source_event_id"),
            provenance=args.get("provenance"),
            source="mcp",
            knowledge_id=args.get("id"),
            rev=args.get("rev"),
            created_at=args.get("created_at"),
        )
    if name == "reload":
        return api.reload(
            conversation_id=args.get("conversation_id"),
            budget_tokens=int(args.get("budget_tokens") or DEFAULT_LOAD_BUDGET),
            workspace=args.get("workspace"),
        )
    if name == "relay":
        action = args.get("action")
        if action == "save":
            return api.save_handoff(
                args.get("title") or "handoff",
                args.get("body") or "",
                workspace=args.get("workspace"),
                conversation_id=args.get("conversation_id"),
                suggested_skills=args.get("suggested_skills") or [],
                focus=args.get("focus"),
                scope=args.get("scope") or "workspace",
                source="mcp",
            )
        if action == "load":
            return api.load_handoff(
                pack_id=args.get("pack_id"),
                target=args.get("query"),
                workspace=args.get("workspace"),
                budget_tokens=int(args.get("budget_tokens") or DEFAULT_LOAD_BUDGET),
            )
        return {"ok": False, "error": "invalid_action", "action": action}
    if name == "job":
        action = args.get("action")
        if action == "start":
            return api.job_start(
                args.get("title") or "job",
                kind=args.get("kind") or "declared",
                conversation_id=args.get("conversation_id"),
                workspace=args.get("workspace"),
                summary=args.get("summary"),
                source="mcp",
            )
        if action == "update":
            return api.job_update(
                args.get("id") or "",
                int(args.get("rev") or 0),
                status=args.get("status"),
                summary=args.get("summary"),
                title=args.get("title"),
                source="mcp",
            )
        if action == "done":
            return api.job_done(
                args.get("id") or "",
                int(args.get("rev") or 0),
                summary=args.get("summary"),
                status=args.get("status") or "done",
                source="mcp",
            )
        if action == "claim":
            return api.job_claim(
                args.get("id") or "",
                int(args.get("rev") or 0),
                conversation_id=args.get("conversation_id"),
                workspace=args.get("workspace"),
                source="mcp",
            )
        return {"ok": False, "error": "invalid_action", "action": action}
    if name == "recall":
        return graph.recall(
            args.get("query") or "",
            workspace=args.get("workspace"),
            budget_tokens=int(args.get("budget_tokens") or DEFAULT_LOAD_BUDGET),
            conversation_id=args.get("conversation_id"),
            generation_id=args.get("generation_id"),
            source="mcp",
        )
    if name == "link":
        return graph.link(
            args.get("from_ref") or "",
            args.get("to_ref") or "",
            rel=args.get("rel") or "TRIGGERS",
            confirm=bool(args.get("confirm")),
            role=args.get("role"),
            pointer=args.get("pointer"),
            subtype=args.get("subtype"),
            knowledge_id=args.get("knowledge_id"),
            from_type=args.get("from_type"),
            to_type=args.get("to_type"),
            sense=args.get("sense"),
            label=args.get("label"),
            workspace=args.get("workspace"),
            source="mcp",
            conversation_id=args.get("conversation_id"),
        )
    if name == "forbid":
        return graph.forbid(
            args.get("from_ref") or "",
            args.get("to_ref") or "",
            reason=args.get("reason") or "",
            workspace=args.get("workspace"),
            source="mcp",
            from_type=args.get("from_type"),
            to_type=args.get("to_type"),
        )
    if name == "confirm":
        return graph.confirm(
            args.get("from_ref") or "",
            args.get("to_ref") or "",
            rel=args.get("rel") or "TRIGGERS",
            workspace=args.get("workspace"),
        )
    if name == "map":
        return graph.map_status()
    if name == "archive":
        return api.archive(args.get("id") or "")
    if name == "artifact":
        if args.get("action") == "load":
            return api.load_artifact(args.get("id") or "")
        return {"ok": False, "error": "invalid_action", "action": args.get("action")}
    if name == "measure":
        if args.get("improve"):
            return api.improve_from_logs(
                limit=int(args.get("limit") or 20),
                workspace=args.get("workspace"),
                conversation_id=args.get("conversation_id"),
            )
        if args.get("eval"):
            cases = args.get("cases") or api.DEFAULT_EVAL_CASES
            return api.eval_retrieval(
                cases,
                workspace=args.get("workspace"),
                conversation_id=args.get("conversation_id"),
            )
        return api.measure_retrieval(limit=int(args.get("limit") or 50))
    if name == "memory":
        action = args.get("action")
        if action == "status":
            return api.memory_status()
        if action == "worker":
            return api.memory_worker(
                limit=int(args.get("limit") or 20),
                sweep_faded=bool(args.get("sweep_faded", True)),
            )
        if action == "candidates":
            return api.memory_candidates(
                workspace=args.get("workspace"),
                status=args.get("status") or "pending",
                limit=int(args.get("limit") or 50),
            )
        if action == "promote":
            return api.promote_memory_candidate(args.get("candidate_id") or "")
        return {"ok": False, "error": "invalid_action", "action": action}
    return {"ok": False, "error": "unknown_tool", "name": name}


def _read_message() -> dict[str, Any] | None:
    header = sys.stdin.buffer.readline()
    if not header:
        return None
    if header.lstrip().startswith(b"{"):
        return json.loads(header.decode("utf-8"))
    headers: dict[str, str] = {}
    line = header
    while line not in (b"\r\n", b"\n", b""):
        decoded = line.decode("utf-8", errors="replace")
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        line = sys.stdin.buffer.readline()
        if not line:
            return None
    length = int(headers.get("content-length", "0"))
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(raw.encode("utf-8"))
    sys.stdout.buffer.flush()


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method is None:
        return None
    if method == "notifications/initialized" or str(method).startswith("notifications/"):
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = _call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(exc)})}],
                    "isError": True,
                },
            }
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    while True:
        try:
            msg = _read_message()
        except Exception:
            break
        if msg is None:
            break
        reply = _handle(msg)
        if reply is not None:
            _write_message(reply)


if __name__ == "__main__":
    main()
