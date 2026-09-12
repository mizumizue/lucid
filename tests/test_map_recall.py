#!/usr/bin/env python3
"""Map recall: FORBIDS, recency/count, ALIAS, bodies stay in SQLite."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


class MapRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api, graph

        self.api = api
        self.graph = graph
        self.cid = "11111111-1111-1111-1111-111111111111"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def _seed_relay(self, *, confirm: bool = True, uses_knowledge: bool = True) -> dict:
        knowledge = None
        if uses_knowledge:
            knowledge = self.api.remember(
                "relay skill pointer",
                "SECRET_BODY_NOT_IN_GRAPH 引き継ぎ正本は Pack kind=handoff。",
                kind="fact",
                scope="global",
                workspace=self.ws,
                conversation_id=self.cid,
            )
            self.assertTrue(knowledge["ok"], knowledge)
        trig = self.graph.link(
            "relay load",
            "relay",
            rel="TRIGGERS",
            confirm=confirm,
            pointer="skill:relay",
            subtype="skill",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(trig["ok"], trig)
        if uses_knowledge:
            used = self.graph.link(
                "relay",
                "relay skill pointer",
                rel="USES",
                confirm=confirm,
                role="skill-doc",
                knowledge_id=knowledge["id"],
                workspace=self.ws,
                source="cli",
            )
            self.assertTrue(used["ok"], used)
        return trig

    def test_confirmed_walk_loads_sqlite_body_not_ladybug(self) -> None:
        self._seed_relay()
        found = self.graph.recall("relay load", workspace=self.ws)
        self.assertTrue(found["ok"], found)
        self.assertGreaterEqual(len(found["procedures"]), 1)
        proc = found["procedures"][0]
        self.assertEqual(proc["pointer"], "skill:relay")
        self.assertGreaterEqual(len(proc["materials"]), 1)
        mat = proc["materials"][0]
        self.assertIn("SECRET_BODY_NOT_IN_GRAPH", mat.get("body") or "")
        listed = self.graph.map_status()
        self.assertNotIn("SECRET_BODY_NOT_IN_GRAPH", json.dumps(listed, ensure_ascii=False))

    def test_forbid_hides_procedure(self) -> None:
        self._seed_relay()
        banned = self.graph.forbid("relay load", "relay", reason="dame", source="cli")
        self.assertTrue(banned["ok"], banned)
        found = self.graph.recall("relay load", workspace=self.ws)
        self.assertTrue(found["ok"], found)
        self.assertEqual(found["procedures"], [])
        again = self.graph.link(
            "relay load",
            "relay",
            rel="TRIGGERS",
            confirm=True,
            workspace=self.ws,
            source="cli",
        )
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"], "forbidden")

    def test_linked_edges_stay_recallable_and_rank_confirmed(self) -> None:
        once = self.graph.link(
            "scene generate",
            "cypher-cli",
            rel="TRIGGERS",
            pointer="cli:db/cypher.py",
            subtype="cli",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(once["ok"], once)
        shown = self.graph.recall("scene generate", workspace=self.ws)
        self.assertEqual(len(shown["procedures"]), 0)
        stale = self.graph.link(
            "scene generate",
            "old-walk",
            rel="TRIGGERS",
            confirm=True,
            pointer="cli:old",
            subtype="cli",
            at=_iso_days_ago(400),
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(stale["ok"], stale)
        recent = self.graph.link(
            "scene generate",
            "cypher-cli",
            rel="TRIGGERS",
            confirm=True,
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(recent["ok"], recent)
        found = self.graph.recall("scene generate", workspace=self.ws)
        pointers = [p["pointer"] for p in found["procedures"]]
        self.assertIn("cli:db/cypher.py", pointers)
        self.assertIn("cli:old", pointers)
        self.assertEqual(found["procedures"][0]["pointer"], "cli:db/cypher.py")
        self.assertEqual(found["procedures"][0]["triggers"]["status"], "confirmed")

    def test_alias_resolves_to_canonical(self) -> None:
        self._seed_relay()
        aliased = self.graph.link(
            "引き継ぎを載せて",
            "relay load",
            rel="ALIAS_OF",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(aliased["ok"], aliased)
        found = self.graph.recall("引き継ぎを載せて", workspace=self.ws)
        self.assertTrue(found["ok"], found)
        pointers = [p["pointer"] for p in found["procedures"]]
        self.assertIn("skill:relay", pointers)

    def test_recall_by_topic_and_context(self) -> None:
        trig = self.graph.link(
            "relay load",
            "relay",
            rel="TRIGGERS",
            confirm=True,
            pointer="skill:relay",
            subtype="skill",
            sense="related",
            label="引き継ぎの手順",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(trig["ok"], trig)
        about = self.graph.link(
            "relay",
            "handoff",
            rel="ABOUT",
            confirm=True,
            label="引き継ぎという話題",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(about["ok"], about)
        self.assertEqual(about["to"]["type"], "topic")
        self.assertEqual(about["edge"]["sense"], "topic")
        ctx = self.graph.link(
            "relay",
            "lucid-memories bootstrap",
            rel="IN_CONTEXT",
            confirm=True,
            label="bus を載せる文脈",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(ctx["ok"], ctx)
        self.assertEqual(ctx["to"]["type"], "context")
        by_topic = self.graph.recall("handoff", workspace=self.ws)
        self.assertTrue(by_topic["ok"], by_topic)
        self.assertIn("skill:relay", [p["pointer"] for p in by_topic["procedures"]])
        self.assertTrue(any(t.get("title") == "handoff" for t in by_topic.get("topics") or []))
        by_ctx = self.graph.recall("lucid-memories bootstrap", workspace=self.ws)
        self.assertTrue(by_ctx["ok"], by_ctx)
        self.assertIn("skill:relay", [p["pointer"] for p in by_ctx["procedures"]])

    def test_confirm_about_and_context(self) -> None:
        about = self.graph.link(
            "pipeline auto",
            "指示パイプライン",
            rel="ABOUT",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(about["ok"], about)
        self.assertEqual(about["edge"]["status"], "proposed")
        confirmed = self.graph.confirm(
            "pipeline auto",
            "指示パイプライン",
            rel="ABOUT",
            workspace=self.ws,
        )
        self.assertTrue(confirmed["ok"], confirmed)
        self.assertEqual(confirmed["status"], "confirmed")
        ctx = self.graph.link(
            "pipeline auto",
            "lucid-memories",
            rel="IN_CONTEXT",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(ctx["ok"], ctx)
        ctx_ok = self.graph.confirm(
            "pipeline auto",
            "lucid-memories",
            rel="IN_CONTEXT",
            workspace=self.ws,
        )
        self.assertTrue(ctx_ok["ok"], ctx_ok)
        self.assertEqual(ctx_ok["status"], "confirmed")

    def test_confirm_procedure_about_topic(self) -> None:
        trig = self.graph.link(
            "relay load",
            "relay",
            rel="TRIGGERS",
            confirm=True,
            pointer="skill:relay",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(trig["ok"], trig)
        about = self.graph.link(
            "relay",
            "handoff",
            rel="ABOUT",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(about["ok"], about)
        self.assertEqual(about["from"]["type"], "procedure")
        self.assertEqual(about["to"]["type"], "topic")
        self.assertEqual(about["edge"]["status"], "proposed")
        hidden = self.graph.recall("handoff", workspace=self.ws)
        self.assertEqual(hidden.get("procedures") or [], [])
        confirmed = self.graph.confirm("relay", "handoff", rel="ABOUT", workspace=self.ws)
        self.assertTrue(confirmed["ok"], confirmed)
        self.assertEqual(confirmed["from"]["type"], "procedure")
        found = self.graph.recall("handoff", workspace=self.ws)
        self.assertIn("skill:relay", [p["pointer"] for p in found["procedures"]])

    def test_archive_hides_map_body(self) -> None:
        seeded = self._seed_relay()
        self.assertTrue(seeded["ok"], seeded)
        found = self.graph.recall("relay load", workspace=self.ws)
        self.assertIn("SECRET_BODY_NOT_IN_GRAPH", found["procedures"][0]["materials"][0].get("body") or "")
        kid = found["procedures"][0]["materials"][0]["knowledge_id"]
        archived = self.api.archive(kid)
        self.assertTrue(archived["ok"], archived)
        hidden = self.graph.recall("relay load", workspace=self.ws)
        body = (hidden["procedures"][0]["materials"][0].get("body") if hidden.get("procedures") else None) or ""
        self.assertNotIn("SECRET_BODY_NOT_IN_GRAPH", body)

    def test_japanese_utterance_resolves_directive_via_fts(self) -> None:
        from lucid_memories.runtime.util import fts_match_arg, useful_query_tokens

        self._seed_relay()
        tokens = useful_query_tokens("relay を載せて")
        self.assertIn("relay", tokens)
        match = fts_match_arg("relay を載せて") or ""
        self.assertIn('"relay"', match)
        found = self.graph.recall("relay を載せて", workspace=self.ws)
        self.assertTrue(found["ok"], found)
        pointers = [p["pointer"] for p in found["procedures"]]
        self.assertEqual(pointers, ["skill:relay"])
        body = found["procedures"][0]["materials"][0].get("body") or ""
        self.assertIn("SECRET_BODY_NOT_IN_GRAPH", body)
        self.assertFalse(any("idea" in (p.get("title") or "").lower() for p in found["procedures"]))

    def test_hot_proposed_recallable_stale_proposed_hidden(self) -> None:
        for _ in range(3):
            linked = self.graph.link(
                "hot compile",
                "hot-cli",
                rel="TRIGGERS",
                pointer="cli:hot",
                subtype="cli",
                workspace=self.ws,
                source="cli",
            )
            self.assertTrue(linked["ok"], linked)
        hot = self.graph.recall("hot compile", workspace=self.ws)
        self.assertIn("cli:hot", [p["pointer"] for p in hot["procedures"]])
        for _ in range(3):
            linked = self.graph.link(
                "zzz-unique-stale",
                "old-walk",
                rel="TRIGGERS",
                pointer="cli:old",
                subtype="cli",
                at=_iso_days_ago(400),
                workspace=self.ws,
                source="cli",
            )
            self.assertTrue(linked["ok"], linked)
        stale = self.graph.recall("zzz-unique-stale", workspace=self.ws)
        self.assertEqual(stale.get("procedures") or [], [])

    def test_recall_does_not_mix_other_workspace(self) -> None:
        other = str(Path(self.tmp.name) / "other-ws")
        Path(other).mkdir(parents=True, exist_ok=True)
        here = self.graph.link(
            "relay load",
            "relay-here",
            rel="TRIGGERS",
            confirm=True,
            pointer="skill:relay",
            workspace=self.ws,
            source="cli",
        )
        there = self.graph.link(
            "relay load",
            "relay-there",
            rel="TRIGGERS",
            confirm=True,
            pointer="skill:other",
            workspace=other,
            source="cli",
        )
        self.assertTrue(here["ok"], here)
        self.assertTrue(there["ok"], there)
        found = self.graph.recall("relay load", workspace=self.ws)
        self.assertEqual([p["pointer"] for p in found["procedures"]], ["skill:relay"])


class MapIsolationTests(unittest.TestCase):
    def test_map_path_is_not_charactor_lines(self) -> None:
        old = os.environ.pop("LUCID_MEMORIES_HOME", None)
        try:
            from lucid_memories.storage.paths import map_path

            path = map_path()
            charactor_lines = (
                Path.home() / "cursor-workspace" / "charactor" / "db" / "lines.lbdb"
            )
            self.assertEqual(path.name, "map.lbdb")
            self.assertNotEqual(path.resolve(), charactor_lines.resolve())
            self.assertNotIn("charactor", path.as_posix().lower())
        finally:
            if old is not None:
                os.environ["LUCID_MEMORIES_HOME"] = old

    def test_user_mcp_has_no_official_ladybug(self) -> None:
        mcp = Path.home() / ".cursor" / "mcp.json"
        self.assertTrue(mcp.is_file(), mcp)
        text = mcp.read_text(encoding="utf-8")
        self.assertNotIn("mcp-server-ladybug", text)
        self.assertIn("lucid-memories", text)


if __name__ == "__main__":
    unittest.main()
