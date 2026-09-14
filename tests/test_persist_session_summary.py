from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lucid_memories.core import api
from lucid_memories.storage.db import connect, migrate


class PersistSessionSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "bus.sqlite"
        connection = sqlite3.connect(self.path)
        migrate(connection)
        connection.executescript(
            """
            INSERT INTO sessions(
              conversation_id, parent_conversation_id, workspace_roots_json,
              composer_mode, is_background, model, transcript_path, title, summary,
              status, last_generation_id, last_heartbeat_at, ended_reason,
              created_at, updated_at
            ) VALUES (
              'session-1', NULL, '[]', 'agent', 0, 'model-a', NULL, 'Alpha', NULL,
              'active', NULL, '2026-01-03T00:00:00Z', NULL,
              '2026-01-01T00:00:00Z', '2026-01-03T00:00:00Z'
            );
            INSERT INTO conversation_events(
              id, conversation_id, generation_id, event_type, role,
              tool_name, input_text, output_text, status, workspace_root,
              metadata_json, created_at
            ) VALUES
              ('ev-1', 'session-1', NULL, 'beforeSubmitPrompt', 'user',
               NULL, 'ship session summaries', NULL, NULL, NULL, '{}', '2026-01-01T00:00:00Z'),
              ('ev-2', 'session-1', NULL, 'afterAgentResponse', 'assistant',
               NULL, NULL, 'summary shipped', NULL, NULL, '{}', '2026-01-02T00:00:00Z');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_persist_session_summary_writes_summary_column(self) -> None:
        with patch("lucid_memories.core.api_common._conn", lambda: connect(self.path)):
            result = api.persist_session_summary("session-1")
        self.assertTrue(result["saved"])
        self.assertIn("ship session summaries", result["summary"])

        connection = sqlite3.connect(self.path)
        row = connection.execute(
            "SELECT summary FROM sessions WHERE conversation_id = 'session-1'"
        ).fetchone()
        connection.close()
        self.assertIn("summary shipped", row[0])


if __name__ == "__main__":
    unittest.main()
