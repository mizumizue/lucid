from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lucid_memories.core.session_metadata import (
    compute_session_summary,
    ORIGIN_AGENT,
    ORIGIN_HUMAN,
    ORIGIN_UNKNOWN,
    SESSION_KIND_BACKGROUND,
    SESSION_KIND_MAIN,
    SESSION_KIND_SUB,
    build_brief,
    enrich_sessions,
    session_kind_sql_condition,
    session_origin_sql_condition,
    session_kind,
    session_origin,
)


class SessionMetadataTests(unittest.TestCase):
    def test_session_kind_and_origin(self) -> None:
        self.assertEqual(session_kind({"parent_conversation_id": "parent-1"}), SESSION_KIND_SUB)
        self.assertEqual(session_kind({"is_background": 1}), SESSION_KIND_BACKGROUND)
        self.assertEqual(session_kind({}), SESSION_KIND_MAIN)
        self.assertEqual(session_origin("beforeSubmitPrompt"), ORIGIN_HUMAN)
        self.assertEqual(session_origin("postToolUse"), ORIGIN_AGENT)
        self.assertEqual(session_origin(None), ORIGIN_UNKNOWN)

    def test_build_brief_prefers_first_user_prompt(self) -> None:
        brief = build_brief(
            last_prompt="latest prompt",
            first_user_prompt="first user prompt",
            last_output="final answer",
        )
        self.assertEqual(brief, "first user prompt → final answer")

    def test_sql_filters_match_kind_and_origin(self) -> None:
        main_sql, _ = session_kind_sql_condition("main")
        self.assertIn("parent_conversation_id", main_sql)
        human_sql, human_params = session_origin_sql_condition("human", has_events=True)
        self.assertIn("beforeSubmitPrompt", human_params)
        self.assertIn("conversation_events", human_sql)

    def test_compute_session_summary_includes_jobs(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        path = Path(tempdir.name) / "bus.sqlite"
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE conversation_events (
              id TEXT PRIMARY KEY, conversation_id TEXT, event_type TEXT,
              role TEXT, input_text TEXT, output_text TEXT, created_at TEXT
            );
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY, conversation_id TEXT, kind TEXT, status TEXT,
              title TEXT, summary TEXT, subagent_id TEXT, subagent_type TEXT,
              updated_at TEXT
            );
            INSERT INTO conversation_events VALUES
              ('ev-1', 'main-1', 'beforeSubmitPrompt', 'user', 'fix tests', NULL, '2026-01-01'),
              ('ev-2', 'main-1', 'afterAgentResponse', 'assistant', NULL, 'tests fixed', '2026-01-02');
            INSERT INTO jobs VALUES
              ('job-1', 'main-1', 'subagent', 'done', 'Explore', 'found root cause', NULL, 'explore', '2026-01-03');
            """
        )
        connection.commit()
        summary = compute_session_summary(
            connection,
            "main-1",
            has_events=True,
            has_jobs=True,
        )
        connection.close()
        tempdir.cleanup()
        self.assertIn("fix tests", summary or "")
        self.assertIn("tests fixed", summary or "")
        self.assertIn("explore", summary or "")

    def test_enrich_sessions_adds_metadata(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        path = Path(tempdir.name) / "bus.sqlite"
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE sessions (
              conversation_id TEXT PRIMARY KEY,
              parent_conversation_id TEXT,
              title TEXT,
              is_background INTEGER,
              status TEXT,
              model TEXT,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE conversation_events (
              id TEXT PRIMARY KEY,
              conversation_id TEXT,
              event_type TEXT,
              role TEXT,
              input_text TEXT,
              output_text TEXT,
              created_at TEXT
            );
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY,
              conversation_id TEXT,
              kind TEXT,
              status TEXT,
              subagent_id TEXT,
              subagent_type TEXT,
              updated_at TEXT
            );
            INSERT INTO sessions VALUES
              ('main-1', NULL, 'Main session', 0, 'active', 'model-a', '2026-01-01', '2026-01-02'),
              ('sub-1', 'main-1', NULL, 0, 'active', 'model-b', '2026-01-01', '2026-01-02');
            INSERT INTO conversation_events VALUES
              ('ev-1', 'main-1', 'beforeSubmitPrompt', 'user', 'fix the dashboard', NULL, '2026-01-01T00:00:00Z'),
              ('ev-2', 'main-1', 'afterAgentResponse', 'assistant', NULL, 'dashboard updated', '2026-01-01T00:01:00Z'),
              ('ev-3', 'sub-1', 'postToolUse', 'tool', NULL, 'search complete', '2026-01-01T00:02:00Z');
            INSERT INTO jobs VALUES
              ('job-1', 'main-1', 'subagent', 'done', 'sub-1', 'explore', '2026-01-01T00:03:00Z');
            """
        )
        connection.commit()
        sessions = [
            {
                "conversation_id": "main-1",
                "parent_conversation_id": None,
                "title": "Main session",
                "is_background": 0,
                "last_prompt": "fix the dashboard",
            },
            {
                "conversation_id": "sub-1",
                "parent_conversation_id": "main-1",
                "title": None,
                "is_background": 0,
                "last_prompt": None,
            },
        ]
        enriched = enrich_sessions(connection, sessions, has_events=True, has_jobs=True)
        connection.close()
        tempdir.cleanup()

        main = enriched[0]
        sub = enriched[1]
        self.assertEqual(main["session_kind"], SESSION_KIND_MAIN)
        self.assertEqual(main["origin"], ORIGIN_HUMAN)
        self.assertEqual(main["subagent_types"], ["explore"])
        self.assertIn("fix the dashboard", main["brief"] or "")
        self.assertEqual(sub["session_kind"], SESSION_KIND_SUB)
        self.assertEqual(sub["origin"], ORIGIN_AGENT)
        self.assertEqual(sub["subagent_types"], ["explore"])
        self.assertEqual(sub["parent_title"], "Main session")


if __name__ == "__main__":
    unittest.main()
