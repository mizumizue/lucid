"""Normalized retrieval request/result attribution tests."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class RetrievalTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories import api

        self.api = api
        self.cid = "66666666-6666-6666-6666-666666666666"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def _rows(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        conn = self.api._conn()
        try:
            requests = conn.execute(
                "SELECT * FROM retrieval_requests ORDER BY started_at"
            ).fetchall()
            results = conn.execute(
                "SELECT * FROM retrieval_results ORDER BY created_at, ordinal"
            ).fetchall()
            return requests, results
        finally:
            conn.close()

    def test_search_records_conversation_generation_rank_and_score(self) -> None:
        saved = self.api.remember(
            "tracked memory",
            "This memory is returned by a tracked full text search.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        result = self.api.search(
            "tracked memory",
            workspace=self.ws,
            conversation_id=self.cid,
            generation_id="generation-1",
        )
        self.assertTrue(result["ok"], result)
        requests, results = self._rows()
        request = next(
            row for row in requests if row["id"] == result["retrieval_request_id"]
        )
        self.assertEqual(request["conversation_id"], self.cid)
        self.assertEqual(request["generation_id"], "generation-1")
        self.assertEqual(request["method"], "search")
        self.assertEqual(request["status"], "completed")
        hit = next(row for row in results if row["entity_id"] == saved["id"])
        self.assertEqual(hit["source_db"], "sqlite")
        self.assertEqual(hit["rank"], 1)
        self.assertGreater(hit["memory_score"], 0)

    def test_semantic_failure_is_recorded_as_error(self) -> None:
        from lucid_memories import embedding

        with patch.object(embedding, "embed", side_effect=embedding.EmbeddingError("offline")):
            result = self.api.semantic_search(
                "tracked semantic query",
                workspace=self.ws,
                conversation_id=self.cid,
                generation_id="generation-2",
            )
        self.assertFalse(result["ok"])
        requests, _ = self._rows()
        request = next(
            row for row in requests if row["id"] == result["retrieval_request_id"]
        )
        self.assertEqual(request["method"], "semantic_search")
        self.assertEqual(request["status"], "error")

    def test_load_and_map_recall_are_attributed(self) -> None:
        saved = self.api.remember(
            "loadable tracked memory",
            "A body that can be loaded and attributed.",
            kind="fact",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        loaded = self.api.load(
            saved["id"],
            workspace=self.ws,
            conversation_id=self.cid,
            generation_id="generation-3",
        )
        recalled = __import__("lucid_memories.graph", fromlist=["recall"]).recall(
            "no map match",
            workspace=self.ws,
            conversation_id=self.cid,
            generation_id="generation-4",
        )
        self.assertTrue(loaded["ok"], loaded)
        self.assertTrue(recalled["ok"], recalled)
        requests, results = self._rows()
        methods = {row["method"]: row for row in requests}
        self.assertEqual(methods["load"]["conversation_id"], self.cid)
        self.assertEqual(methods["load"]["generation_id"], "generation-3")
        self.assertIn("map_recall", methods)
        self.assertTrue(any(row["entity_id"] == saved["id"] for row in results))

    def test_digest_uses_one_parent_request_without_duplicate_children(self) -> None:
        self.api.remember(
            "digest tracked memory",
            "The digest should attribute its combined retrieval once.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.api.save_prompt(
            "digest tracked memory",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        self.api.digest_text(
            self.cid,
            workspace=self.ws,
            generation_id="generation-digest",
        )
        requests, _ = self._rows()
        digest_requests = [
            row for row in requests if row["generation_id"] == "generation-digest"
        ]
        self.assertEqual(len(digest_requests), 1)
        self.assertEqual(digest_requests[0]["method"], "collect_retrieval")
        self.assertEqual(digest_requests[0]["status"], "completed")

        conn = self.api._conn()
        try:
            linked = conn.execute(
                "SELECT request_id FROM retrieval_logs WHERE conversation_id = ?",
                (self.cid,),
            ).fetchall()
        finally:
            conn.close()
        self.assertTrue(linked)
        self.assertEqual(linked[-1]["request_id"], digest_requests[0]["id"])

    def test_mcp_search_propagates_attribution(self) -> None:
        from lucid_memories import mcp_server

        result = mcp_server._call_tool(
            "search",
            {
                "query": "mcp tracked query",
                "workspace": self.ws,
                "conversation_id": self.cid,
                "generation_id": "generation-mcp",
            },
        )
        self.assertTrue(result["ok"], result)
        requests, _ = self._rows()
        request = next(
            row for row in requests if row["id"] == result["retrieval_request_id"]
        )
        self.assertEqual(request["source"], "mcp")
        self.assertEqual(request["conversation_id"], self.cid)
        self.assertEqual(request["generation_id"], "generation-mcp")

    def test_old_retrieval_logs_are_backfilled_once(self) -> None:
        old_home = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = old_home.name
        home = Path(old_home.name)
        db_path = home / "bus.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE retrieval_logs (
                  id TEXT PRIMARY KEY,
                  conversation_id TEXT,
                  prompt TEXT NOT NULL,
                  prompt_at TEXT NOT NULL,
                  query TEXT,
                  source TEXT NOT NULL,
                  workspace_root TEXT,
                  dbs_json TEXT NOT NULL DEFAULT '[]',
                  links_json TEXT NOT NULL DEFAULT '[]',
                  hits_json TEXT NOT NULL DEFAULT '[]',
                  sqlite_hit_count INTEGER NOT NULL DEFAULT 0,
                  map_hit_count INTEGER NOT NULL DEFAULT 0,
                  link_count INTEGER NOT NULL DEFAULT 0,
                  expected_json TEXT,
                  matched_count INTEGER,
                  expected_count INTEGER,
                  gaps_json TEXT,
                  improved_json TEXT,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("PRAGMA user_version = 12")
            conn.execute(
                """
                INSERT INTO retrieval_logs(
                  id, conversation_id, prompt, prompt_at, query, source,
                  hits_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-log-1",
                    self.cid,
                    "legacy prompt",
                    "2026-09-11T00:00:00+00:00",
                    "legacy",
                    "digest",
                    json.dumps(
                        [
                            {
                                "db": "sqlite",
                                "kind": "fact",
                                "id": "legacy-memory",
                                "title": "Legacy memory",
                            }
                        ]
                    ),
                    "2026-09-11T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        migrated = self.api._conn()
        try:
            request = migrated.execute(
                "SELECT * FROM retrieval_requests WHERE id = 'legacy-log-1'"
            ).fetchone()
            result = migrated.execute(
                "SELECT * FROM retrieval_results WHERE request_id = 'legacy-log-1'"
            ).fetchone()
            log = migrated.execute(
                "SELECT request_id FROM retrieval_logs WHERE id = 'legacy-log-1'"
            ).fetchone()
        finally:
            migrated.close()
        self.assertEqual(request["method"], "legacy_log")
        self.assertEqual(result["entity_id"], "legacy-memory")
        self.assertEqual(log["request_id"], "legacy-log-1")

        again = self.api._conn()
        try:
            count = again.execute(
                "SELECT COUNT(*) FROM retrieval_results WHERE request_id = 'legacy-log-1'"
            ).fetchone()[0]
        finally:
            again.close()
        self.assertEqual(count, 1)
        old_home.cleanup()


if __name__ == "__main__":
    unittest.main()
