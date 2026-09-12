"""SQLite vector storage and cosine retrieval tests."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class EmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LUCID_MEMORIES_HOME"] = self.tmp.name
        from lucid_memories import api, embedding

        self.api = api
        self.embedding = embedding
        self.cid = "33333333-3333-3333-3333-333333333333"
        self.ws = str(Path(self.tmp.name) / "ws")
        Path(self.ws).mkdir(parents=True, exist_ok=True)
        self.api.ensure_session(self.cid, workspace_roots=[self.ws])

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("LUCID_MEMORIES_HOME", None)

    @staticmethod
    def fake_embed(text: str):
        # Deterministic two-dimensional fixture: "SQLite" and "Cursor" are
        # intentionally orthogonal so cosine ranking is observable.
        lower = text.lower()
        sqlite = 1.0 if "sqlite" in lower or "vector" in lower else 0.0
        cursor = 1.0 if "cursor" in lower or "agent" in lower else 0.0
        return __import__("lucid_memories.embedding", fromlist=["Embedding"]).Embedding(
            model="fixture",
            values=[sqlite, cursor],
        )

    def test_remember_persists_float32_vector_and_vsearch_ranks(self) -> None:
        with patch.object(self.embedding, "embed", side_effect=self.fake_embed):
            sqlite_item = self.api.remember(
                "SQLite vector store",
                "SQLite stores embedding vectors for agent context.",
                kind="decision",
                scope="workspace",
                workspace=self.ws,
                conversation_id=self.cid,
            )
            cursor_item = self.api.remember(
                "Cursor agent job",
                "Cursor agents share job progress.",
                kind="fact",
                scope="workspace",
                workspace=self.ws,
                conversation_id=self.cid,
            )
            self.assertTrue(sqlite_item["embedding"]["ok"], sqlite_item)
            self.assertTrue(cursor_item["embedding"]["ok"], cursor_item)
            result = self.api.semantic_search(
                "SQLite embeddings",
                workspace=self.ws,
                min_score=0.1,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["knowledge"][0]["id"], sqlite_item["id"])
        self.assertGreater(result["knowledge"][0]["score"], 0.6)
        self.assertEqual(result["model"], "fixture")

        conn = self.api._conn()
        try:
            row = conn.execute(
                "SELECT dimensions, length(vector) AS bytes FROM embeddings WHERE entity_id = ?",
                (sqlite_item["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["dimensions"], 2)
        self.assertEqual(row["bytes"], 8)

    def test_unavailable_provider_does_not_block_remember(self) -> None:
        with patch.object(
            self.embedding,
            "embed",
            side_effect=self.embedding.EmbeddingError("offline"),
        ):
            result = self.api.remember(
                "offline fact",
                "Knowledge remains available while the provider is offline.",
                scope="global",
            )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["embedding"]["ok"])
        self.assertEqual(result["embedding"]["error"], "embedding_unavailable")


if __name__ == "__main__":
    unittest.main()
