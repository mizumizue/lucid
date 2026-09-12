from __future__ import annotations

import os
import hashlib
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class StorageLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories import api, blobs, hook
        from lucid_memories.paths import db_path, map_path

        self.api = api
        self.blobs = blobs
        self.hook = hook
        self.db_path = db_path
        self.map_path = map_path

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_database_paths_are_grouped_and_renamed(self) -> None:
        self.api.ensure_session("storage-session", workspace_roots=[self.tmp.name])
        self.assertEqual(self.db_path().relative_to(self.tmp.name).as_posix(), ".db/lucid-memories.sqlite")
        self.assertEqual(self.map_path().relative_to(self.tmp.name).as_posix(), ".db/map.lbdb")

    def test_blob_payload_and_runtime_log_are_database_backed(self) -> None:
        conn = self.api._conn()
        try:
            payload = b"embedded blob"
            sha = self.blobs.put_blob(conn, payload, "application/octet-stream")
        finally:
            conn.close()

        self.hook._log_hook_call("storage-test", "storage-session")

        conn = sqlite3.connect(self.db_path())
        try:
            blob = conn.execute(
                "SELECT bytes, data FROM blobs WHERE sha256 = ?",
                (sha,),
            ).fetchone()
            log = conn.execute(
                "SELECT log_name, content FROM runtime_logs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(blob, (len(payload), payload))
        self.assertEqual(log[0], "hook_calls.log")
        self.assertIn(b"storage-test", log[1])
        self.assertEqual(self.blobs.get_blob(sha), payload)
        self.assertFalse((Path(self.tmp.name) / "blobs").exists())
        self.assertFalse((Path(self.tmp.name) / "logs").exists())

    def test_migration_moves_legacy_storage_and_removes_external_payloads(self) -> None:
        home = Path(self.tmp.name)
        sqlite3.connect(home / "bus.sqlite").close()
        payload = b"legacy blob"
        digest = hashlib.sha256(payload).hexdigest()
        blob_path = home / "blobs" / digest[:2] / digest
        blob_path.parent.mkdir(parents=True)
        blob_path.write_bytes(payload)
        log_path = home / "logs" / "hook_calls.log"
        log_path.parent.mkdir()
        log_path.write_bytes(b"legacy log\n")

        spec = importlib.util.spec_from_file_location(
            "migrate_storage",
            ROOT / "scripts" / "migrate_storage.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        result = migration.migrate()

        self.assertTrue(result["ok"])
        self.assertTrue((home / ".db" / "lucid-memories.sqlite").is_file())
        self.assertEqual(result["database_move"], "moved")
        self.assertEqual(result["blobs_migrated"], 1)
        self.assertEqual(result["logs_migrated"], 1)
        self.assertFalse((home / "bus.sqlite").exists())
        self.assertFalse((home / "blobs").exists())
        self.assertFalse((home / "logs").exists())


if __name__ == "__main__":
    unittest.main()
