#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class UsageEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api
        from lucid_memories.entrypoints import hook

        self.api = api
        self.hook = hook
        self.cid = "33333333-3333-3333-3333-333333333333"
        self.api.ensure_session(self.cid, workspace_roots=[self.tmp.name], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_hook_records_common_usage_aliases(self) -> None:
        result = self.hook.handle_hook(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": self.cid,
                "generation_id": "generation-1",
                "model_id": "test-model",
                "usage": {
                    "promptTokens": 120,
                    "completionTokens": 45,
                    "cacheReadInputTokens": 30,
                    "cacheCreationInputTokens": 5,
                    "totalTokens": 165,
                    "costUsd": 0.0123,
                },
                "prompt": "入力テスト",
                "assistant_output": "出力テスト",
            }
        )
        self.assertIn("additional_context", result)
        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        try:
            row = conn.execute(
                """
                SELECT generation_id, model, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens, total_tokens,
                       cost_usd, input_text, output_text
                FROM usage_events
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            row,
            (
                "generation-1",
                "test-model",
                120,
                45,
                30,
                5,
                165,
                0.0123,
                "入力テスト",
                "出力テスト",
            ),
        )

    def test_hook_ignores_payload_without_usage(self) -> None:
        self.hook.handle_hook(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": self.cid,
                "generation_id": "generation-without-usage",
            }
        )
        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        try:
            count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_hook_indexes_conversation_output_and_artifact(self) -> None:
        workspace = Path(self.tmp.name) / "workspace"
        workspace.mkdir()
        output_file = workspace / "result.md"
        output_file.write_text("# generated\n", encoding="utf-8")
        result = self.hook.handle_hook(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": self.cid,
                "generation_id": "generation-2",
                "workspace_roots": [str(workspace)],
                "tool_name": "write_file",
                "tool_input": {"file_path": "result.md", "content": "# generated\n"},
                "tool_output": {"output_text": "created result.md"},
            }
        )
        self.assertIn("additional_context", result)
        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        try:
            event = conn.execute(
                """
                SELECT event_type, role, tool_name, output_text
                FROM conversation_events
                """
            ).fetchone()
            artifact = conn.execute(
                """
                SELECT relative_path, name, size_bytes, sha256,
                       blob_sha, storage_scope, storage_policy
                FROM artifacts
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(event, ("postToolUse", "tool", "write_file", "created result.md"))
        self.assertEqual(artifact[0], "result.md")
        self.assertEqual(artifact[1], "result.md")
        self.assertEqual(artifact[2], output_file.stat().st_size)
        self.assertTrue(artifact[3])
        self.assertIsNone(artifact[4])
        self.assertEqual(artifact[5], "workspace")
        self.assertEqual(artifact[6], "managed_elsewhere")

    def test_agent_response_is_stored_in_lucid_memories_blob(self) -> None:
        from lucid_memories.storage import blobs

        generated = "Generated design artifact\n" + ("content line\n" * 40)
        self.hook.handle_hook(
            {
                "hook_event_name": "afterAgentResponse",
                "conversation_id": self.cid,
                "generation_id": "generation-blob",
                "assistant_output": generated,
            }
        )
        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        try:
            artifact = conn.execute(
                """
                SELECT id, kind, storage_scope, storage_policy, sha256, blob_sha
                FROM artifacts
                WHERE kind = 'conversation_output'
                """
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact[1:4], ("conversation_output", "conversation", "lucid_memories"))
        self.assertEqual(artifact[4], artifact[5])
        self.assertEqual(blobs.get_blob_text(artifact[5]), generated)
        loaded = self.api.load_artifact(artifact[0])
        self.assertTrue(loaded["ok"], loaded)
        self.assertEqual(loaded["artifact"]["content"], generated)

    def test_hook_indexes_agent_response_shell_and_file_edit(self) -> None:
        workspace = Path(self.tmp.name) / "workspace"
        workspace.mkdir()
        output_file = workspace / "answer.txt"
        output_file.write_text("answer", encoding="utf-8")
        for payload in (
            {
                "hook_event_name": "afterAgentResponse",
                "conversation_id": self.cid,
                "text": "assistant response",
            },
            {
                "hook_event_name": "afterShellExecution",
                "conversation_id": self.cid,
                "command": "type answer.txt",
                "output": "answer",
            },
            {
                "hook_event_name": "afterFileEdit",
                "conversation_id": self.cid,
                "workspace_roots": [str(workspace)],
                "file_path": str(output_file),
                "edits": [],
            },
        ):
            self.hook.handle_hook(payload)

        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        try:
            event_types = {
                row[0]
                for row in conn.execute(
                    "SELECT event_type FROM conversation_events"
                ).fetchall()
            }
            response = conn.execute(
                """
                SELECT output_text
                FROM conversation_events
                WHERE event_type = 'afterAgentResponse'
                """
            ).fetchone()[0]
            artifact_count = conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(
            {"afterAgentResponse", "afterShellExecution", "afterFileEdit"}
            <= event_types
        )
        self.assertEqual(response, "assistant response")
        self.assertGreaterEqual(artifact_count, 1)


if __name__ == "__main__":
    unittest.main()
