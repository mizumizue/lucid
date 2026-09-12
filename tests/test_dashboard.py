"""Retrieval relationship graph and local dashboard tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories.core import api

        self.api = api
        self.cid = "55555555-5555-5555-5555-555555555555"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(
            self.cid,
            workspace_roots=[self.ws],
            title="想起グラフの会話",
            status="active",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    def _seed_log(self) -> str:
        saved = self.api.remember(
            "dashboard memory",
            "A memory visible from the retrieval dashboard.",
            kind="decision",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertTrue(saved["ok"], saved)
        logged = self.api.log_retrieval(
            "どの記憶が想起されたか",
            conversation_id=self.cid,
            prompt_at="2026-09-11T00:00:00+00:00",
            query="記憶 想起",
            source="digest",
            workspace=self.ws,
            hits=[
                {
                    "db": "sqlite",
                    "kind": "decision",
                    "id": saved["id"],
                    "title": "dashboard memory",
                },
                {
                    "db": "map",
                    "kind": "procedure",
                    "id": "procedure-1",
                    "title": "retrieval dashboard",
                },
            ],
            links=[
                {
                    "rel": "TRIGGERS",
                    "from_id": "directive-1",
                    "from_title": "会話の想起を確認する",
                    "to_db": "map",
                    "to_kind": "procedure",
                    "to_id": "procedure-1",
                    "to_title": "retrieval dashboard",
                    "status": "confirmed",
                }
            ],
        )
        self.assertTrue(logged["ok"], logged)
        return saved["id"]

    def test_dashboard_graph_connects_conversation_event_and_memory(self) -> None:
        memory_id = self._seed_log()
        result = self.api.dashboard_graph(workspace=self.ws)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["meta"]["log_count"], 1)
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertIn(f"conversation:{self.cid}", nodes)
        self.assertIn(f"retrieval:{result['edges'][0]['id'].split(':')[-1]}", nodes)
        memory_nodes = [
            node for node in nodes.values()
            if node.get("type") == "memory" and node.get("item_id") == memory_id
        ]
        self.assertEqual(len(memory_nodes), 1)
        self.assertGreater(memory_nodes[0]["memory_score"], 0)
        self.assertGreater(nodes[f"conversation:{self.cid}"]["temperature"], 0)
        self.assertTrue(any(edge["relation"] == "recalled" for edge in result["edges"]))
        self.assertTrue(any(edge["relation"] == "TRIGGERS" for edge in result["edges"]))

    def test_dashboard_graph_can_filter_conversation_and_keeps_unknown_logs(self) -> None:
        self._seed_log()
        unknown = self.api.log_retrieval(
            "unknown conversation",
            conversation_id=None,
            prompt_at="2026-09-11T01:00:00+00:00",
            workspace=self.ws,
            hits=[{"db": "map", "kind": "topic", "id": "topic-1", "title": "unknown"}],
        )
        self.assertTrue(unknown["ok"], unknown)

        all_graph = self.api.dashboard_graph(workspace=self.ws)
        self.assertIn(
            "conversation:unknown",
            {node["id"] for node in all_graph["nodes"]},
        )
        filtered = self.api.dashboard_graph(
            workspace=self.ws,
            conversation_id=self.cid,
        )
        self.assertEqual(filtered["meta"]["log_count"], 1)
        self.assertNotIn(
            "conversation:unknown",
            {node["id"] for node in filtered["nodes"]},
        )

    def test_dashboard_graph_aggregates_duplicate_prompt_retrievals(self) -> None:
        saved = self.api.remember(
            "common memory",
            "A shared memory recalled multiple times.",
            kind="fact",
            scope="workspace",
            workspace=self.ws,
            conversation_id=self.cid,
        )
        prompt = "同じプロンプトでの複数回想起"
        hit = {
            "db": "sqlite",
            "kind": "fact",
            "id": saved["id"],
            "title": "common memory",
        }
        # Log first retrieval
        self.api.log_retrieval(
            prompt,
            conversation_id=self.cid,
            prompt_at="2026-09-11T02:00:00+00:00",
            source="digest",
            workspace=self.ws,
            hits=[hit],
        )
        # Log second retrieval with same prompt
        self.api.log_retrieval(
            prompt,
            conversation_id=self.cid,
            prompt_at="2026-09-11T02:05:00+00:00",
            source="digest",
            workspace=self.ws,
            hits=[hit],
        )

        graph = self.api.dashboard_graph(workspace=self.ws, conversation_id=self.cid)
        retrieval_nodes = [
            n for n in graph["nodes"]
            if n["type"] == "retrieval" and n["prompt"] == prompt
        ]
        self.assertEqual(len(retrieval_nodes), 1)
        self.assertEqual(retrieval_nodes[0]["retrieval_count"], 2)
        self.assertEqual(len(retrieval_nodes[0]["request_ids"]), 2)

        # Check edge from conversation to retrieval
        contains_edges = [
            e for e in graph["edges"]
            if e["relation"] == "contains" and e["target"] == retrieval_nodes[0]["id"]
        ]
        self.assertEqual(len(contains_edges), 1)
        self.assertEqual(contains_edges[0]["count"], 2)

        # Check edge from retrieval to memory
        recalled_edges = [
            e for e in graph["edges"]
            if e["relation"] == "recalled" and e["source"] == retrieval_nodes[0]["id"]
        ]
        self.assertEqual(len(recalled_edges), 1)
        self.assertEqual(recalled_edges[0]["count"], 2)

    def test_dashboard_main_argument_parsing(self) -> None:
        from unittest.mock import patch
        from lucid_memories.web import dashboard

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            dashboard.main([])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=False)

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            dashboard.main(["open"])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=True)

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            dashboard.main(["--open", "--port", "8888"])
            mock_run.assert_called_with(host="127.0.0.1", port=8888, open_browser=True)

    def test_cli_open_and_dashboard_dispatch(self) -> None:
        from unittest.mock import patch
        from lucid_memories.entrypoints import cli
        from lucid_memories.web import dashboard

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            cli.main(["open"])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=True)

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            cli.main(["dashboard", "open"])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=True)

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            cli.main(["dashboard", "--open"])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=True)

        with patch.object(dashboard, "run_dashboard", return_value=0) as mock_run:
            cli.main(["dashboard"])
            mock_run.assert_called_with(host="127.0.0.1", port=8765, open_browser=False)


if __name__ == "__main__":
    unittest.main()
