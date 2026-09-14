"""MCP tool-result usage is recorded and kept out of model token totals."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class McpUsageRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api
        from lucid_memories.entrypoints import mcp_server

        self.api = api
        self.mcp = mcp_server
        self.cid = "77777777-7777-7777-7777-777777777777"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def _usage_rows(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(Path(self.tmp.name) / ".db" / "lucid-memories.sqlite")
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute("SELECT * FROM usage_events ORDER BY created_at"))
        finally:
            conn.close()

    def test_mcp_tool_records_result_tokens_for_conversation(self) -> None:
        result = self.mcp._call_tool(
            "whoami",
            {"workspace": self.ws, "conversation_id": self.cid},
        )
        self.assertTrue(result.get("ok"), result)
        rows = self._usage_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event_type"], "mcp")
        self.assertEqual(row["conversation_id"], self.cid)
        self.assertEqual(row["input_text"], "whoami")
        self.assertGreater(row["total_tokens"], 0)
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(metadata["source"], "mcp")
        self.assertEqual(metadata["tool_name"], "whoami")

    def test_mcp_tool_records_generation_and_inner_budget(self) -> None:
        result = self.mcp._call_tool(
            "recall",
            {
                "query": "MCP usage visualization",
                "workspace": self.ws,
                "conversation_id": self.cid,
                "generation_id": "generation-mcp-usage",
                "budget_tokens": 500,
            },
        )
        self.assertTrue(result.get("ok"), result)
        row = self._usage_rows()[0]
        self.assertEqual(row["generation_id"], "generation-mcp-usage")
        metadata = json.loads(row["metadata_json"])
        self.assertEqual(metadata.get("budget"), 500)
        self.assertIn("tokens_used", metadata)

    def test_mcp_tool_without_conversation_does_not_record(self) -> None:
        other_ws = str(Path(self.tmp.name) / "unbound")
        Path(other_ws).mkdir(parents=True, exist_ok=True)
        result = self.mcp._call_tool("whoami", {"workspace": other_ws})
        self.assertTrue(result.get("ok"), result)
        self.assertIsNone(result.get("conversation_id"))
        self.assertEqual(self._usage_rows(), [])


if __name__ == "__main__":
    unittest.main()
