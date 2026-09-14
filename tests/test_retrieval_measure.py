#!/usr/bin/env python3
"""Retrieval logs and hit-rate measurement."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class RetrievalMeasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api, graph

        self.api = api
        self.graph = graph
        self.cid = "33333333-3333-3333-3333-333333333333"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws], status="active")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def test_digest_logs_instruction_and_hits(self) -> None:
        remembered = self.api.remember(
            "指示パイプラインは Auto、確定だけ Ask",
            "search and proposed link are automatic.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(remembered["ok"], remembered)
        self.api.save_prompt(
            "指示パイプラインで検索と提案書き込みをして",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        digest = self.api.digest_text(self.cid, workspace=self.ws)
        self.assertIn(remembered["id"], digest)
        summary = self.api.measure_retrieval()
        self.assertGreaterEqual(summary["n"], 1)
        row = summary["recent"][0]
        self.assertIn("指示パイプライン", row["prompt"])
        self.assertGreaterEqual(row["sqlite_hit_count"], 1)
        self.assertIn("sqlite", row["dbs"])

    def test_eval_hit_rate_map_and_sqlite(self) -> None:
        knowledge = self.api.remember(
            "指示パイプラインは Auto、確定だけ Ask",
            "bodies stay in sqlite.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(knowledge["ok"], knowledge)
        about = self.graph.link(
            "ユーザー指示が回ったら検索と提案書き込み",
            "指示パイプライン",
            rel="ABOUT",
            confirm=True,
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(about["ok"], about)
        ctx = self.graph.link(
            "ユーザー指示が回ったら検索と提案書き込み",
            "lucid-memories",
            rel="IN_CONTEXT",
            confirm=True,
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(ctx["ok"], ctx)
        result = self.api.eval_retrieval(
            [
                {
                    "prompt": "指示パイプラインで検索と提案書き込みをして",
                    "expect": [
                        {"db": "map", "kind": "topic", "title": "指示パイプライン"},
                        {"db": "sqlite", "title": "指示パイプラインは Auto、確定だけ Ask"},
                    ],
                },
                {
                    "prompt": "lucid-memories の文脈でパイプラインを引け",
                    "expect": [{"db": "map", "kind": "context", "title": "lucid-memories"}],
                },
            ],
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["n"], 2)
        self.assertGreater(result["hit_rate"], 0)
        self.assertGreater(result["eval_recall"], 0)
        summary = self.api.measure_retrieval()
        self.assertGreaterEqual(summary["eval_n"], 2)

    def test_digest_evaluates_gaps_without_autolink(self) -> None:
        knowledge = self.api.remember(
            "指示パイプラインは Auto、確定だけ Ask",
            "bodies stay in sqlite.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(knowledge["ok"], knowledge)
        about = self.graph.link(
            "ユーザー指示が回ったら検索と提案書き込み",
            "指示パイプライン",
            rel="ABOUT",
            confirm=True,
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(about["ok"], about)
        before = self.graph.recall("指示パイプラインで検索と提案", workspace=self.ws)
        self.assertEqual(before.get("procedures") or [], [])
        self.api.save_prompt(
            "指示パイプラインで検索と提案書き込みをして",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        digest = self.api.digest_text(self.cid, workspace=self.ws)
        self.assertIn("eval:", digest)
        self.assertNotIn("improve:", digest)
        after = self.graph.recall("指示パイプラインで検索と提案", workspace=self.ws)
        self.assertEqual(after.get("procedures") or [], [])
        summary = self.api.measure_retrieval()
        self.assertTrue(any(row.get("gaps") for row in summary["recent"]))

    def test_query_tokens_mixed_japanese(self) -> None:
        from lucid_memories.runtime.util import query_tokens

        tokens = query_tokens("提案書き込みをして")
        self.assertNotIn("提案書", tokens)
        self.assertNotIn("みをして", tokens)
        self.assertIn("提案", tokens)
        self.assertIn("書き", tokens)
        self.assertIn("込み", tokens)
        pipeline = query_tokens("指示パイプラインで検索と提案書き込みをして")
        self.assertIn("指示", pipeline)
        self.assertIn("パイプライン", pipeline)
        self.assertIn("検索", pipeline)
        self.assertIn("提案", pipeline)

    def test_digest_twice_does_not_increment_count(self) -> None:
        trig = self.graph.link(
            "ユーザー指示が回ったら検索と提案書き込み",
            "search-pipeline",
            rel="TRIGGERS",
            confirm=True,
            pointer="skill:pipeline",
            workspace=self.ws,
            source="cli",
        )
        self.assertTrue(trig["ok"], trig)
        count_before = trig["edge"]["count"]
        self.api.save_prompt(
            "指示パイプラインで検索と提案書き込みをして",
            conversation_id=self.cid,
            workspace=self.ws,
        )
        self.api.digest_text(self.cid, workspace=self.ws)
        self.api.digest_text(self.cid, workspace=self.ws)
        again = self.graph.link(
            "ユーザー指示が回ったら検索と提案書き込み",
            "search-pipeline",
            rel="TRIGGERS",
            workspace=self.ws,
            source="auto",
        )
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["edge"]["count"], count_before)
        found = self.graph.recall("ユーザー指示が回ったら検索と提案書き込み", workspace=self.ws)
        self.assertEqual(found["procedures"][0]["triggers"]["count"], count_before)

    def test_improve_skips_zero_score_sqlite_uses(self) -> None:
        unrelated = self.api.remember(
            "unrelated wal leftover",
            "should not become a USES target",
            kind="fact",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(unrelated["ok"], unrelated)
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
        collected = {
            "query": "relay load",
            "hits": [
                {"db": "map", "kind": "procedure", "id": trig["to"]["id"], "title": "relay"},
                {
                    "db": "sqlite",
                    "kind": "knowledge",
                    "id": unrelated["id"],
                    "title": unrelated.get("title") or "unrelated wal leftover",
                },
            ],
            "links": [],
        }
        result = self.api.improve_retrieval(
            "relay load",
            collected,
            workspace=self.ws,
            conversation_id=self.cid,
            apply=True,
        )
        self.assertTrue(result["ok"], result)
        uses = [p for p in result.get("proposed") or [] if p.get("rel") == "USES"]
        self.assertEqual(uses, [])
        found = self.graph.recall("relay load", workspace=self.ws)
        mats = found["procedures"][0]["materials"] if found.get("procedures") else []
        self.assertFalse(any(m.get("knowledge_id") == unrelated["id"] for m in mats))

    def test_digest_eval_error_is_not_gaps_none(self) -> None:
        self.api.save_prompt("anything to digest", conversation_id=self.cid, workspace=self.ws)
        from unittest.mock import patch

        with patch(
            "lucid_memories.core.retrieval_ops.improve_retrieval",
            side_effect=RuntimeError("ladybug down"),
        ):
            digest = self.api.digest_text(self.cid, workspace=self.ws)
        self.assertIn("eval: error=", digest)
        self.assertNotIn("eval: gaps=none", digest)


if __name__ == "__main__":
    unittest.main()
