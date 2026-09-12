"""Permission gates for lucid-memories MCP/CLI. Search and proposed writes auto-allow."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from lucid_memories.core import privacy_gate

AUTO_TOOLS = {
    "whoami",
    "status",
    "search",
    "list",
    "load",
    "reload",
    "recall",
    "map",
    "remember",
    "link",
    "job",
    "relay",
    "measure",
    "memory",
    "artifact",
}
ASK_TOOLS = {"confirm", "forbid", "archive"}
PRODUCT_MARKERS = (
    "lucid-memories",
    "user-lucid-memories",
)
CLI_NAMES = {"cli.py", "lucid-memories"}


def _mentions_product(text: str | None) -> bool:
    low = (text or "").replace("\\", "/").lower()
    return any(marker in low for marker in PRODUCT_MARKERS)


def _tool_short(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("mcp:"):
        raw = raw.split(":", 1)[-1]
    if "_" in raw and _mentions_product(raw):
        return raw.rsplit("_", 1)[-1].lower()
    return raw.lower()


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}:
        return True
    return False


def _parse_params(tool_input: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(tool_input, dict):
        if isinstance(tool_input.get("arguments"), dict):
            return tool_input["arguments"]
        return tool_input
    if isinstance(tool_input, str) and tool_input.strip():
        try:
            parsed = json.loads(tool_input)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("arguments"), dict):
                    return parsed["arguments"]
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _is_bus_mcp(
    *,
    tool_name: str | None = None,
    mcp_server_name: str | None = None,
    command: str | None = None,
) -> bool:
    server = (mcp_server_name or "").strip().lower()
    if _mentions_product(server) or server.endswith("lucid-memories"):
        return True
    if _mentions_product(command):
        return True
    return _mentions_product(tool_name)


def mcp_permission(
    *,
    tool_name: str | None = None,
    tool_input: str | dict[str, Any] | None = None,
    mcp_server_name: str | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    if not _is_bus_mcp(
        tool_name=tool_name,
        mcp_server_name=mcp_server_name,
        command=command,
    ):
        return {}
    short = _tool_short(tool_name)
    params = _parse_params(tool_input)
    if short in ASK_TOOLS:
        return {"permission": "ask"}
    if short == "link" and _truthy(params.get("confirm")):
        return {"permission": "ask"}
    if short in AUTO_TOOLS:
        return {"permission": "allow"}
    return {"permission": "ask"}


def _bus_subcommand(command: str) -> str | None:
    try:
        tokens = shlex.split(command or "", posix=False)
    except ValueError:
        tokens = (command or "").split()
    for i, tok in enumerate(tokens):
        norm = tok.strip("'\"").replace("\\", "/").lower()
        base = norm.rsplit("/", 1)[-1]
        looks_bus = _mentions_product(norm) or base in CLI_NAMES
        if not looks_bus:
            continue
        if base in CLI_NAMES and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if not nxt.startswith("-"):
                return nxt.lower()
    return None


def _check_git_privacy(command: str, repo_root: Path | None = None) -> dict[str, Any]:
    cmd = (command or "").strip()
    if not cmd:
        return {}

    git_match = re.search(r"\bgit\s+(commit|add)\b", cmd, re.IGNORECASE)
    if not git_match:
        return {}

    subcmd = git_match.group(1).lower()

    if subcmd == "commit":
        check = privacy_gate.check_staged_files(repo_root=repo_root)
        if not check.get("ok"):
            violations = check.get("violations", [])
            summary = "; ".join(f"{v.get('file')}: {v.get('reason')}" for v in violations)
            return {
                "permission": "ask",
                "user_message": f"Git commit blocked by privacy gate: Personal or sensitive information detected ({summary}).",
            }
    elif subcmd == "add":
        if privacy_gate.is_local_only_repo(repo_root):
            return {}
        tokens = cmd.split()
        for tok in tokens:
            cleaned = tok.strip("'\"")
            sensitive, reason = privacy_gate.check_sensitive_path(cleaned)
            if sensitive:
                return {
                    "permission": "ask",
                    "user_message": f"Git add blocked by privacy gate: Attempting to stage sensitive file '{cleaned}' ({reason}).",
                }
    return {}


def shell_permission(command: str | None, repo_root: Path | None = None) -> dict[str, Any]:
    cmd = command or ""
    git_gate = _check_git_privacy(cmd, repo_root=repo_root)
    if git_gate:
        return git_gate

    if not _mentions_product(cmd):
        return {}
    sub = _bus_subcommand(cmd)
    if sub in ASK_TOOLS:
        return {"permission": "ask"}
    if sub == "link" and "--confirm" in cmd.lower():
        return {"permission": "ask"}
    return {"permission": "allow"}


def pre_tool_permission(payload: dict[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    name = str(payload.get("tool_name") or payload.get("toolName") or "")
    tool_input = payload.get("tool_input") or payload.get("arguments") or {}
    if name.lower() == "shell":
        command = None
        if isinstance(tool_input, dict):
            command = tool_input.get("command")
        elif isinstance(tool_input, str):
            command = tool_input
        return shell_permission(command, repo_root=repo_root)
    server = payload.get("mcp_server_name") or payload.get("server")
    if isinstance(tool_input, dict) and not server:
        server = tool_input.get("mcp_server_name") or tool_input.get("server")
    inner_name = name
    if isinstance(tool_input, dict):
        inner_name = (
            tool_input.get("tool_name")
            or tool_input.get("toolName")
            or tool_input.get("name")
            or name
        )
    return mcp_permission(
        tool_name=str(inner_name or name),
        tool_input=tool_input,
        mcp_server_name=str(server) if server else None,
        command=payload.get("command"),
    )
