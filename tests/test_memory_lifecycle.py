"""Lifecycle scoring and durable event consolidation tests."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api

        self.api = api
        self.cid = "44444444-4444-4444-4444-444444444444"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_search_tracks_access_and_returns_memory_score(self) -> None:
        saved = self.api.remember(
            "lifecycle decision",
            "The memory lifecycle uses a decay score.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(saved["ok"], saved)
        found = self.api.search("lifecycle", workspace=self.ws)
        self.assertEqual(len(found["knowledge"]), 1)
        item = found["knowledge"][0]
        self.assertIn("memory_score", item)
        self.assertGreater(item["memory_score"], 0)
        loaded = self.api.load(saved["id"], workspace=self.ws)
        self.assertTrue(loaded["ok"], loaded)
        conn = self.api._conn()
        try:
            row = conn.execute(
                "SELECT access_count, last_accessed_at FROM knowledge WHERE id = ?",
                (saved["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertGreaterEqual(row["access_count"], 2)
        self.assertIsNotNone(row["last_accessed_at"])

    def test_event_worker_creates_pending_candidate(self) -> None:
        event = self.api.record_activity(
            {
                "conversation_id": self.cid,
                "workspace_roots": [self.ws],
                "prompt": "決定: durable memory should keep the source event.",
                "response": "方針として原文イベントを保持します。",
                "hook_event_name": "afterAgentResponse",
            },
            event_type="afterAgentResponse",
        )
        self.assertTrue(event["ok"], event)
        worker = self.api.memory_worker(limit=10, sweep_faded=False)
        self.assertTrue(worker["ok"], worker)
        candidates = self.api.memory_candidates(workspace=self.ws)
        self.assertGreaterEqual(len(candidates["candidates"]), 1)
        candidate = candidates["candidates"][0]
        self.assertEqual(candidate["status"], "pending")
        self.assertEqual(candidate["source_event_id"], event["event_id"])

    def test_sweep_fades_without_deleting_and_explicit_load_survives(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
        saved = self.api.remember(
            "old fading memory",
            "This memory is intentionally old and low importance.",
            kind="finding",
            scope="global",
            importance=0.1,
            salience=0.1,
            confidence=0.1,
            decay_half_life_days=1,
            created_at=old,
        )
        self.assertTrue(saved["ok"], saved)
        worker = self.api.memory_worker(limit=0, sweep_faded=True)
        self.assertTrue(worker["ok"], worker)
        conn = self.api._conn()
        try:
            status = conn.execute(
                "SELECT memory_status FROM knowledge WHERE id = ?",
                (saved["id"],),
            ).fetchone()["memory_status"]
        finally:
            conn.close()
        self.assertEqual(status, "faded")
        hidden = self.api.search("old fading", workspace=self.ws)
        self.assertEqual(hidden["knowledge"], [])
        loaded = self.api.load(saved["id"], workspace=self.ws)
        self.assertEqual(loaded["items"][0]["id"], saved["id"])


if __name__ == "__main__":
    unittest.main()
