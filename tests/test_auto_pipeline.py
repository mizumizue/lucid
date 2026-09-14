#!/usr/bin/env python3
"""Auto pipeline: prompt digest, archive hides search, permission gates."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class AutoPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api, gate
        from lucid_memories.entrypoints import hook

        self.api = api
        self.gate = gate
        self.hook = hook
        self.cid = "22222222-2222-2222-2222-222222222222"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_archive_hides_from_search(self) -> None:
        remembered = self.api.remember(
            "wal decision",
            "SQLite WAL is the bus body store.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(remembered["ok"], remembered)
        found = self.api.search("WAL", workspace=self.ws)
        self.assertGreaterEqual(len(found["knowledge"]), 1)
        archived = self.api.archive(remembered["id"])
        self.assertTrue(archived["ok"], archived)
        hidden = self.api.search("WAL", workspace=self.ws)
        ids = [item["id"] for item in hidden["knowledge"]]
        self.assertNotIn(remembered["id"], ids)

    def test_prompt_digest_includes_search(self) -> None:
        remembered = self.api.remember(
            "Map links are semantic",
            "ABOUT is topic, IN_CONTEXT is context, RELATED is leftover relatedness.",
            kind="fact",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(remembered["ok"], remembered)
        saved = self.api.save_prompt(
            "How should Map links express topic vs context?",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        self.assertTrue(saved["ok"], saved)
        digest = self.api.digest_text(self.cid, workspace=self.ws)
        self.assertIn(remembered["id"], digest)
        self.assertIn("Map links are semantic", digest)
        self.assertIn("confirm/forbid/archive", digest)

    def test_pending_prompt_binds_on_digest(self) -> None:
        remembered = self.api.remember(
            "relay uses Pack kind=handoff",
            "reload is compact only.",
            kind="fact",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(remembered["ok"], remembered)
        self.api.save_prompt("relay handoff pack", workspace=self.ws)
        digest = self.api.digest_text(self.cid, workspace=self.ws)
        self.assertIn("relay uses Pack", digest)

    def test_mcp_permissions(self) -> None:
        allow = self.gate.mcp_permission(
            tool_name="search",
            mcp_server_name="lucid-memories",
            tool_input={"query": "wal"},
        )
        self.assertEqual(allow.get("permission"), "allow")
        remember = self.gate.mcp_permission(
            tool_name="user-lucid-memories_remember",
            mcp_server_name="user-lucid-memories",
            tool_input={"title": "x", "body": "y"},
        )
        self.assertEqual(remember.get("permission"), "allow")
        link = self.gate.mcp_permission(
            tool_name="link",
            mcp_server_name="lucid-memories",
            tool_input={"from_ref": "a", "to_ref": "b"},
        )
        self.assertEqual(link.get("permission"), "allow")
        confirm_link = self.gate.mcp_permission(
            tool_name="link",
            mcp_server_name="lucid-memories",
            tool_input={"from_ref": "a", "to_ref": "b", "confirm": True},
        )
        self.assertEqual(confirm_link.get("permission"), "ask")
        confirm = self.gate.mcp_permission(
            tool_name="confirm",
            mcp_server_name="lucid-memories",
            tool_input={"from_ref": "a", "to_ref": "b"},
        )
        self.assertEqual(confirm.get("permission"), "ask")
        archive = self.gate.mcp_permission(
            tool_name="archive",
            mcp_server_name="lucid-memories",
            tool_input={"id": "abc"},
        )
        self.assertEqual(archive.get("permission"), "ask")
        other = self.gate.mcp_permission(
            tool_name="search",
            mcp_server_name="github",
            tool_input={"query": "x"},
        )
        self.assertEqual(other, {})
        unknown = self.gate.mcp_permission(
            tool_name="drop_all",
            mcp_server_name="lucid-memories",
            tool_input={},
        )
        self.assertEqual(unknown.get("permission"), "ask")

    def test_shell_permissions(self) -> None:
        cli = r'python "C:\workspace\lucid-memories\cli.py"'
        self.assertEqual(
            self.gate.shell_permission(f"{cli} search WAL").get("permission"),
            "allow",
        )
        self.assertEqual(
            self.gate.shell_permission(f"{cli} remember --title x --body y").get("permission"),
            "allow",
        )
        self.assertEqual(
            self.gate.shell_permission(f"{cli} link --from a --to b").get("permission"),
            "allow",
        )
        self.assertEqual(
            self.gate.shell_permission(f"{cli} link --from a --to b --confirm").get("permission"),
            "ask",
        )
        self.assertEqual(
            self.gate.shell_permission(f"{cli} archive --id abc").get("permission"),
            "ask",
        )
        self.assertEqual(
            self.gate.shell_permission(f"{cli} confirm --from a --to b").get("permission"),
            "ask",
        )
        self.assertEqual(self.gate.shell_permission("git status"), {})

    def test_hooks_save_prompt_and_gate(self) -> None:
        out = self.hook.handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": self.cid,
                "workspace_roots": [self.ws],
                "prompt": "search the wal decision",
            }
        )
        self.assertIn("bootstrap", out.get("additional_context", "").lower())
        self.assertIn("wal", (self.api.last_prompt(self.cid) or "").lower())
        mcp_cid = "55555555-5555-5555-5555-555555555555"
        mcp = self.hook.handle_hook(
            {
                "hook_event_name": "beforeMCPExecution",
                "conversation_id": mcp_cid,
                "workspace_roots": [self.ws],
                "mcp_server_name": "lucid-memories",
                "tool_name": "remember",
                "tool_input": json.dumps({"title": "x", "body": "y"}),
            }
        )
        self.assertEqual(mcp.get("permission"), "allow")
        self.assertEqual(self.api.whoami(workspace=self.ws)["conversation_id"], mcp_cid)
        ask = self.hook.handle_hook(
            {
                "hook_event_name": "beforeMCPExecution",
                "conversation_id": mcp_cid,
                "workspace_roots": [self.ws],
                "mcp_server_name": "lucid-memories",
                "tool_name": "forbid",
                "tool_input": {"from_ref": "a", "to_ref": "b", "reason": "no"},
            }
        )
        self.assertEqual(ask.get("permission"), "ask")

    def test_mcp_stdio_is_newline_json(self) -> None:
        from io import BytesIO
        from unittest.mock import patch

        from lucid_memories.entrypoints import mcp_server as mcp

        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        stdin = BytesIO((json.dumps(init) + "\n").encode("utf-8"))
        stdout = BytesIO()

        class _Pipe:
            def __init__(self, buf: BytesIO) -> None:
                self.buffer = buf

        with patch.object(mcp.sys, "stdin", _Pipe(stdin)), patch.object(
            mcp.sys, "stdout", _Pipe(stdout)
        ):
            msg = mcp._read_message()
            self.assertEqual(msg["method"], "initialize")
            reply = mcp._handle(msg)
            self.assertIsNotNone(reply)
            mcp._write_message(reply)
        out = stdout.getvalue().decode("utf-8")
        self.assertNotIn("Content-Length", out)
        self.assertTrue(out.endswith("\n"))
        parsed = json.loads(out)
        self.assertEqual(parsed["result"]["serverInfo"]["name"], "lucid-memories")
        listed = mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "whoami",
                "status",
                "search",
                "vsearch",
                "embedding_status",
                "list",
                "load",
                "remember",
                "job",
                "reload",
                "relay",
                "recall",
                "link",
                "forbid",
                "confirm",
                "map",
                "archive",
                "artifact",
                "measure",
                "memory",
                "persona_workspace",
            },
        )


if __name__ == "__main__":
    unittest.main()
