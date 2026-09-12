#!/usr/bin/env python3
"""Compact snapshot + reload/load budget tests. Uses a temp LUCID_MEMORIES_HOME."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class CompactReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        # Import after HOME is set so paths resolve to temp.
        from lucid_memories.core import api

        self.api = api
        self.cid = "11111111-1111-1111-1111-111111111111"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_remember_search_load(self) -> None:
        remembered = self.api.remember(
            "auth uses cookies",
            "Session cookies live in HttpOnly jar. Do not store tokens in localStorage.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
            tags=["auth"],
            source="cli",
        )
        self.assertTrue(remembered["ok"], remembered)
        found = self.api.search("cookies", workspace=self.ws)
        self.assertTrue(found["ok"])
        self.assertGreaterEqual(len(found["knowledge"]), 1)
        loaded = self.api.load("cookies", workspace=self.ws, budget_tokens=2000)
        self.assertTrue(loaded["ok"])
        self.assertGreaterEqual(len(loaded["items"]), 1)

    def test_job_cas_and_claim(self) -> None:
        started = self.api.job_start(
            "explore auth",
            kind="declared",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        self.assertTrue(started["ok"], started)
        job = started["job"]
        bad = self.api.job_update(job["id"], job["rev"] + 9, status="blocked")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"], "rev_conflict")
        claimed = self.api.job_claim(job["id"], job["rev"], conversation_id=self.cid, workspace=self.ws)
        self.assertTrue(claimed["ok"], claimed)
        done = self.api.job_done(job["id"], claimed["job"]["rev"], summary="finished")
        self.assertTrue(done["ok"], done)
        self.assertEqual(done["job"]["status"], "done")

    def test_compact_snapshot_reload_budget(self) -> None:
        long_body = ("決定: 共有バスは SQLite WAL を使う。" * 80)
        self.api.remember(
            "wal decision",
            long_body,
            kind="decision",
            scope="session",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.api.remember(
            "pointer to schema",
            "See ~/.cursor/lucid-memories/schema.sql",
            kind="pointer",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.api.job_start(
            "background explore",
            kind="declared",
            conversation_id=self.cid,
            workspace=self.ws,
            summary="still running",
        )
        snap = self.api.snapshot_compact(self.cid, workspace=self.ws, trigger="auto")
        self.assertTrue(snap["ok"], snap)
        self.assertTrue(snap["pack_id"])
        self.assertGreater(snap["token_estimate"], 0)

        full = self.api.reload(conversation_id=self.cid, workspace=self.ws, budget_tokens=8000)
        self.assertTrue(full["ok"], full)
        self.assertGreaterEqual(len(full["items"]), 1)

        tight = self.api.reload(conversation_id=self.cid, workspace=self.ws, budget_tokens=8)
        self.assertTrue(tight["ok"], tight)
        self.assertGreaterEqual(len(tight["omitted"]), 1)

        loaded_pack = self.api.load(snap["pack_id"], budget_tokens=8000, workspace=self.ws)
        self.assertTrue(loaded_pack["ok"])
        self.assertEqual(loaded_pack["pack"]["id"], snap["pack_id"])

    def test_whoami_and_status(self) -> None:
        me = self.api.whoami(workspace=self.ws, conversation_id=self.cid)
        self.assertEqual(me["conversation_id"], self.cid)
        st = self.api.status(workspace=self.ws, conversation_id=self.cid)
        self.assertTrue(st["ok"])
        ids = [s["conversation_id"] for s in st["sessions"]]
        self.assertIn(self.cid, ids)

    def test_whoami_uses_current_hook_binding_across_mcp_process(self) -> None:
        self.assertTrue(self.api.bind_current_session(self.cid, workspace=self.ws)["ok"])
        me = self.api.whoami(workspace=str(Path(self.tmp.name)))
        self.assertEqual(me["conversation_id"], self.cid)
        self.assertEqual(me["source"], "hook_binding")

        self.assertTrue(self.api.unbind_current_session(self.cid)["ok"])
        unbound = self.api.whoami(workspace=str(Path(self.tmp.name)))
        self.assertIsNone(unbound["conversation_id"])

    def test_whoami_does_not_fallback_to_stale_session(self) -> None:
        with patch.object(self.api, "is_stale_heartbeat", return_value=True):
            me = self.api.whoami(workspace=self.ws)
        self.assertIsNone(me["conversation_id"])
        self.assertFalse(me["ambiguous"])

    def test_whoami_does_not_choose_child_session_from_parent_workspace(self) -> None:
        parent = Path(self.tmp.name)
        me = self.api.whoami(workspace=str(parent))
        self.assertIsNone(me["conversation_id"])
        self.assertFalse(me["ambiguous"])
        self.assertEqual(me["candidates"], [])

        st = self.api.status(workspace=str(parent))
        self.assertNotIn(self.cid, [row["conversation_id"] for row in st["sessions"]])

    def test_whoami_matches_project_descendants(self) -> None:
        nested = Path(self.ws) / "src"
        me = self.api.whoami(workspace=str(nested))
        self.assertEqual(me["conversation_id"], self.cid)
        self.assertFalse(me["ambiguous"])

    def test_whoami_does_not_choose_between_multiple_sessions(self) -> None:
        second = "22222222-2222-2222-2222-222222222222"
        self.api.ensure_session(second, workspace_roots=[self.ws], status="active")

        me = self.api.whoami(workspace=self.ws)
        self.assertIsNone(me["conversation_id"])
        self.assertTrue(me["ambiguous"])
        self.assertIsNone(me["session"])
        self.assertEqual(
            {candidate["conversation_id"] for candidate in me["candidates"]},
            {self.cid, second},
        )

    def test_normalize_msys_path(self) -> None:
        from lucid_memories.runtime.util import normalize_root, workspace_matches

        win = normalize_root(r"C:\workspace\Alice Smith")
        msys = normalize_root("/c/workspace/Alice Smith")
        self.assertEqual(win, msys)
        self.assertTrue(workspace_matches([win], "/c/workspace/Alice Smith"))
        alice = normalize_root(r"C:\workspace\Alice")
        alice_smith = normalize_root(r"C:\workspace\Alice Smith")
        self.assertFalse(workspace_matches([alice], alice_smith))
        self.assertFalse(workspace_matches([alice_smith], alice))

    def test_hook_precompact_and_digest(self) -> None:
        from lucid_memories.entrypoints.hook import handle_hook

        hook_cid = "33333333-3333-3333-3333-333333333333"
        before_prompt = handle_hook(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": hook_cid,
                "workspace_roots": [self.ws],
                "prompt": "register this current session",
            }
        )
        self.assertIsInstance(before_prompt, dict)
        self.assertEqual(
            self.api.whoami(workspace=self.ws)["conversation_id"],
            hook_cid,
        )

        start = handle_hook(
            {
                "hook_event_name": "sessionStart",
                "conversation_id": self.cid,
                "session_id": self.cid,
                "workspace_roots": [self.ws],
                "composer_mode": "agent",
            }
        )
        self.assertEqual(start["env"]["LUCID_MEMORIES_CONVERSATION_ID"], self.cid)
        digest = handle_hook(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": self.cid,
                "generation_id": "gen-1",
                "workspace_roots": [self.ws],
                "tool_name": "Read",
            }
        )
        self.assertIn("additional_context", digest)
        self.assertIn(self.cid, digest["additional_context"])
        again = handle_hook(
            {
                "hook_event_name": "postToolUse",
                "conversation_id": self.cid,
                "generation_id": "gen-1",
                "workspace_roots": [self.ws],
                "tool_name": "Read",
            }
        )
        self.assertEqual(again, {})
        compact = handle_hook(
            {
                "hook_event_name": "preCompact",
                "conversation_id": self.cid,
                "generation_id": "gen-1",
                "workspace_roots": [self.ws],
                "trigger": "auto",
                "context_usage_percent": 90,
                "context_tokens": 100000,
                "context_window_size": 128000,
                "message_count": 40,
                "messages_to_compact": 20,
                "is_first_compaction": True,
            }
        )
        self.assertIn("user_message", compact)
        reloaded = self.api.reload(conversation_id=self.cid, workspace=self.ws)
        self.assertTrue(reloaded["ok"])
        self.assertIsNotNone(reloaded.get("pack"))

    def test_idea_list_and_update(self) -> None:
        created = self.api.remember(
            "メモ題",
            "本文A",
            kind="idea",
            scope="global",
            tags=["memo"],
            created_at="2026-07-19T00:00:00+00:00",
        )
        self.assertTrue(created["ok"], created)
        listed = self.api.list_knowledge(kind="idea", workspace=self.ws)
        self.assertTrue(listed["ok"])
        ids = [k["id"] for k in listed["knowledge"]]
        self.assertIn(created["id"], ids)
        found = self.api.search("本文A", workspace=self.ws, kind="idea")
        self.assertGreaterEqual(len(found["knowledge"]), 1)
        row = next(k for k in listed["knowledge"] if k["id"] == created["id"])
        updated = self.api.remember(
            knowledge_id=created["id"],
            rev=row["rev"],
            body="本文B",
            kind="idea",
        )
        self.assertTrue(updated["ok"], updated)
        loaded = self.api.load(created["id"], workspace=self.ws)
        self.assertIn("本文B", loaded["items"][0]["body"])

    def test_relay_save_and_load(self) -> None:
        saved = self.api.save_handoff(
            "auth 続き",
            "状態: ログインまで完了。次は refresh token。\n\nポインタ: src/auth.ts",
            workspace=self.ws,
            conversation_id=self.cid,
            suggested_skills=["tdd", "lucid-memories"],
            focus="refresh token を実装する",
            scope="workspace",
        )
        self.assertTrue(saved["ok"], saved)
        self.assertTrue(saved["pack_id"])
        loaded = self.api.load_handoff(pack_id=saved["pack_id"], workspace=self.ws)
        self.assertTrue(loaded["ok"], loaded)
        bodies = " ".join(i.get("body") or "" for i in loaded.get("items") or [])
        self.assertIn("refresh token", bodies)
        self.assertIn("tdd", bodies)
        latest = self.api.load_handoff(workspace=self.ws)
        self.assertEqual(latest.get("pack", {}).get("id"), saved["pack_id"])


if __name__ == "__main__":
    unittest.main()
