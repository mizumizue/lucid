from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lucid_memories.web.backend.config import Settings
from lucid_memories.web.backend.http_api import handler_factory
from lucid_memories.web.backend.models import CollectionQuery, Page
from lucid_memories.web.backend.services import DashboardService


class DashboardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "bus.sqlite"
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE sessions (
              conversation_id TEXT PRIMARY KEY, parent_conversation_id TEXT,
              title TEXT, summary TEXT, status TEXT, model TEXT, composer_mode TEXT,
              is_background INTEGER, transcript_path TEXT, workspace_roots_json TEXT,
              last_generation_id TEXT,
              last_heartbeat_at TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY, conversation_id TEXT, kind TEXT, status TEXT,
              title TEXT, summary TEXT, subagent_id TEXT, subagent_type TEXT,
              started_at TEXT, updated_at TEXT, ended_at TEXT, rev INTEGER
            );
            CREATE TABLE conversation_events (
              id TEXT PRIMARY KEY, conversation_id TEXT, event_type TEXT,
              role TEXT, output_text TEXT, input_text TEXT, created_at TEXT
            );
            CREATE TABLE compact_events (
              id TEXT PRIMARY KEY, conversation_id TEXT, created_at TEXT,
              context_tokens INTEGER, context_usage_percent REAL,
              messages_to_compact INTEGER
            );
            CREATE TABLE usage_events (
              id TEXT PRIMARY KEY, conversation_id TEXT, created_at TEXT,
              input_tokens INTEGER, output_tokens INTEGER,
              cache_read_tokens INTEGER, cache_write_tokens INTEGER, cost_usd REAL
            );
            INSERT INTO sessions VALUES
              ('session-1', NULL, 'Alpha', NULL, 'active', 'model-a', 'agent', 0, NULL, NULL,
               NULL, '2026-01-03T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-03T00:00:00Z'),
              ('session-2', NULL, 'Beta', NULL, 'done', 'model-b', 'agent', 0, NULL, NULL,
               NULL, '2026-01-02T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z');
            INSERT INTO jobs VALUES
              ('job-1', 'session-1', 'test', 'done', 'First', NULL, NULL, NULL,
               '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z', 1);
            INSERT INTO conversation_events VALUES
              ('event-1', 'session-1', 'afterAgentResponse', NULL, 'generated', NULL,
               '2026-01-03T00:00:00Z');
            INSERT INTO usage_events VALUES
              ('usage-1', 'session-1', '2026-01-03T00:00:00Z', 10, 20, 3, 4, 0.12);
            """
        )
        connection.commit()
        connection.close()
        self.service = DashboardService(self.path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_overview_reports_usage_and_capabilities(self) -> None:
        payload = self.service.overview()
        self.assertEqual(payload["counts"]["sessions"], 2)
        self.assertEqual(payload["usage"]["status"], "available")
        self.assertTrue(payload["capabilities"]["conversation_events"])
        self.assertEqual(payload["database"], "bus.sqlite")
        self.assertEqual(payload["mcp"]["events"], 0)

    def test_mcp_usage_is_separated_from_model_tokens(self) -> None:
        from datetime import date

        today = date.today().isoformat()
        connection = sqlite3.connect(self.path)
        connection.executescript(
            f"""
            ALTER TABLE usage_events ADD COLUMN event_type TEXT;
            ALTER TABLE usage_events ADD COLUMN total_tokens INTEGER;
            ALTER TABLE usage_events ADD COLUMN generation_id TEXT;
            ALTER TABLE usage_events ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{{}}';
            UPDATE usage_events SET event_type = 'postToolUse', created_at = '{today}T00:00:00Z';
            INSERT INTO usage_events(
              id, conversation_id, created_at, input_tokens, output_tokens,
              cache_read_tokens, cache_write_tokens, cost_usd, event_type,
              total_tokens, generation_id, metadata_json
            ) VALUES
              ('mcp-1', 'session-1', '{today}T00:00:00Z', 0, 80, 0, 0, 0,
               'mcp', 80, 'gen-1', '{{"source":"mcp","tool_name":"recall","budget":2000}}'),
              ('mcp-2', 'session-1', '{today}T00:01:00Z', 0, 20, 0, 0, 0,
               'mcp', 20, 'gen-1', '{{"source":"mcp","tool_name":"whoami"}}'),
              ('mcp-3', 'session-1', '{today}T00:02:00Z', 0, 200, 0, 0, 0,
               'mcp', 200, 'gen-1', '{{"source":"mcp","tool_name":"status"}}');
            """
        )
        connection.commit()
        connection.close()
        payload = self.service.overview()
        self.assertEqual(payload["usage"]["input_tokens"], 10)
        self.assertEqual(payload["usage"]["output_tokens"], 20)
        self.assertEqual(payload["mcp"]["status"], "available")
        self.assertEqual(payload["mcp"]["events"], 3)
        self.assertEqual(payload["mcp"]["tokens"], 300)
        self.assertEqual(payload["mcp"]["stats"]["mean"], 100)
        self.assertEqual(payload["mcp"]["stats"]["median"], 80)
        self.assertEqual(payload["mcp"]["stats"]["large_threshold"], 160)
        tools = {item["tool"]: item for item in payload["mcp"]["by_tool"]}
        self.assertEqual(tools["recall"]["events"], 1)
        self.assertEqual(tools["recall"]["tokens"], 80)
        detail = self.service.session("session-1")
        self.assertEqual(len(detail["relationships"]["usage"]), 1)
        self.assertEqual(detail["relationships"]["usage"][0]["input_tokens"], 10)
        self.assertEqual(len(detail["relationships"]["mcp"]), 3)
        by_tool = {item["tool_name"]: item for item in detail["relationships"]["mcp"]}
        self.assertFalse(by_tool["whoami"]["is_large"])
        self.assertFalse(by_tool["recall"]["is_large"])
        self.assertTrue(by_tool["status"]["is_large"])
        daily = {row["day"]: row for row in self.service.daily(3)["data"]}
        self.assertEqual(daily[today]["mcp_calls"], 3)
        self.assertEqual(daily[today]["mcp_tokens"], 300)
        self.assertEqual(daily[today]["input_tokens"], 10)

    def test_missing_optional_tables_return_partial_data(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE compact_events")
        connection.execute("DROP TABLE jobs")
        connection.commit()
        connection.close()
        payload = self.service.overview()
        self.assertEqual(payload["counts"]["jobs"], 0)
        self.assertEqual(payload["recent_sessions"][0]["job_count"], 0)
        self.assertEqual(self.service.daily(3)["data"][-1]["jobs"], 0)

    def test_search_is_paginated_and_literal(self) -> None:
        payload = self.service.sessions(
            CollectionQuery(text="Alpha%", page=Page(number=1, size=1))
        )
        self.assertEqual(payload["pagination"]["total"], 0)
        payload = self.service.sessions(
            CollectionQuery(text="Alpha", page=Page(number=1, size=1))
        )
        self.assertEqual(payload["data"][0]["conversation_id"], "session-1")
        self.assertFalse(payload["pagination"]["has_next"])

    def test_response_event_is_used_as_session_output(self) -> None:
        detail = self.service.session("session-1")
        self.assertEqual(detail["input_output"]["output"], "generated")

    def test_sessions_include_derived_metadata(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            INSERT INTO sessions VALUES
              ('session-sub', 'session-1', 'Sub session', NULL, 'active', 'model-c', 'agent', 0, NULL, NULL,
               NULL, '2026-01-04T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-04T00:00:00Z');
            INSERT INTO conversation_events VALUES
              ('event-user', 'session-1', 'beforeSubmitPrompt', 'user', NULL, 'hello dashboard',
               '2026-01-01T00:00:00Z');
            INSERT INTO jobs(
              id, conversation_id, kind, status, title, summary,
              subagent_id, subagent_type, started_at, updated_at, ended_at, rev
            ) VALUES (
              'job-sub', 'session-1', 'subagent', 'done', 'Explore task', 'found files',
              'session-sub', 'explore',
              '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', '2026-01-02T00:00:00Z', 1
            );
            """
        )
        connection.commit()
        connection.close()

        payload = self.service.sessions(CollectionQuery(page=Page(number=1, size=10)))
        by_id = {row["conversation_id"]: row for row in payload["data"]}
        self.assertEqual(by_id["session-1"]["session_kind"], "main")
        self.assertEqual(by_id["session-1"]["origin"], "human")
        self.assertEqual(by_id["session-1"]["subagent_types"], ["explore"])
        self.assertIn("hello dashboard", by_id["session-1"]["brief"])
        self.assertEqual(by_id["session-sub"]["session_kind"], "sub")
        self.assertEqual(by_id["session-sub"]["parent_title"], "Alpha")

        detail = self.service.session("session-sub")
        self.assertEqual(detail["data"]["session_kind"], "sub")
        self.assertEqual(detail["data"]["parent_conversation_id"], "session-1")

    def test_sessions_filter_by_kind_and_origin(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            INSERT INTO conversation_events VALUES
              ('event-human', 'session-1', 'beforeSubmitPrompt', 'user', NULL, 'hello dashboard',
               '2026-01-01T00:00:00Z'),
              ('event-agent', 'session-2', 'postToolUse', 'tool', 'tool output', NULL,
               '2026-01-01T00:00:00Z');
            INSERT INTO sessions VALUES
              ('session-sub', 'session-1', 'Sub session', NULL, 'active', 'model-c', 'agent', 0, NULL, NULL,
               NULL, '2026-01-04T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-04T00:00:00Z');
            """
        )
        connection.commit()
        connection.close()

        sub_only = self.service.sessions(
            CollectionQuery(session_kind="sub", page=Page(number=1, size=10))
        )
        self.assertEqual(sub_only["pagination"]["total"], 1)
        self.assertEqual(sub_only["data"][0]["conversation_id"], "session-sub")

        human_only = self.service.sessions(
            CollectionQuery(origin="human", page=Page(number=1, size=10))
        )
        self.assertEqual(
            {row["conversation_id"] for row in human_only["data"]},
            {"session-1"},
        )

        agent_only = self.service.sessions(
            CollectionQuery(origin="agent", page=Page(number=1, size=10))
        )
        self.assertEqual(
            {row["conversation_id"] for row in agent_only["data"]},
            {"session-2"},
        )

    def test_unknown_session_raises_not_found(self) -> None:
        with self.assertRaises(LookupError):
            self.service.session("missing")

    def test_memory_status_and_candidates_are_read_only(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE knowledge (
              id TEXT PRIMARY KEY, kind TEXT, title TEXT, body TEXT,
              blob_sha TEXT, memory_status TEXT, source_conversation_id TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE memory_tasks (
              id TEXT PRIMARY KEY, task_type TEXT, entity_type TEXT,
              entity_id TEXT, status TEXT, attempts INTEGER, last_error TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE memory_candidates (
              id TEXT PRIMARY KEY, event_id TEXT, conversation_id TEXT,
              kind TEXT, title TEXT, summary TEXT, confidence REAL,
              salience REAL, status TEXT, source_event_id TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE embeddings (
              id TEXT PRIMARY KEY, model TEXT, dimensions INTEGER,
              vector BLOB, updated_at TEXT
            );
            CREATE TABLE blobs (
              sha256 TEXT PRIMARY KEY, bytes INTEGER
            );
            CREATE TABLE ontology_nodes (
              id TEXT PRIMARY KEY, type TEXT, title TEXT
            );
            CREATE TABLE artifacts (
              id TEXT PRIMARY KEY, conversation_id TEXT, path TEXT,
              relative_path TEXT, name TEXT, mime_type TEXT, size_bytes INTEGER,
              sha256 TEXT, blob_sha TEXT, content_preview TEXT,
              storage_scope TEXT, storage_policy TEXT, source TEXT,
              created_at TEXT, updated_at TEXT
            );
            INSERT INTO knowledge VALUES
              ('memory-1', 'fact', 'Active memory', 'Active body', NULL, 'active',
               'session-1', '2026-01-01', '2026-01-03'),
              ('memory-2', 'fact', 'Faded memory', 'Faded body', NULL, 'faded',
               'session-2', '2026-01-01', '2026-01-03');
            INSERT INTO memory_tasks VALUES
              ('task-1', 'consolidate_event', 'conversation_event', 'event-1',
               'pending', 0, NULL, '2026-01-03', '2026-01-03');
            INSERT INTO memory_candidates VALUES
              ('candidate-1', 'event-1', 'session-1', 'finding', 'A finding',
               'A useful summary', 0.8, 0.7, 'pending', 'event-1',
               '2026-01-03', '2026-01-03');
            INSERT INTO embeddings VALUES ('embedding-1', 'test-model', 3, X'000102', '2026-01-03');
            INSERT INTO blobs VALUES ('blob-1', 42);
            INSERT INTO ontology_nodes VALUES ('node-1', 'topic', 'A topic');
            INSERT INTO artifacts VALUES
              ('art-blob', 'session-1', 'generated.txt', 'generated.txt', 'generated.txt',
               'text/plain', 12, 'blob-1', 'blob-1', 'hello preview', 'conversation',
               'lucid_memories', 'hook', '2026-01-03', '2026-01-03'),
              ('art-repo', 'session-1', 'C:/repo/file.ts', 'file.ts', 'file.ts',
               'text/typescript', 20, 'abc', NULL, 'export const x = 1', 'repository',
               'managed_elsewhere', 'hook', '2026-01-03', '2026-01-03');
            """
        )
        connection.commit()
        connection.close()
        persona_dir = Path(self.tempdir.name) / "persona"
        persona_dir.mkdir()
        (persona_dir / "persona.json").write_text(
            '{"schema_version": 1, "scope": "user", "sections": [{"id": "tone", "title": "Tone"}], '
            '"token_estimate": 40, "token_budget": 8000}',
            encoding="utf-8",
        )

        status = self.service.memory_status()
        self.assertEqual(status["knowledge"]["counts"], {"active": 1, "faded": 1})
        self.assertEqual(status["candidates"]["counts"]["pending"], 1)
        candidates = self.service.memory_candidates(
            CollectionQuery(text="useful", page=Page(number=1, size=10))
        )
        self.assertEqual(candidates["data"][0]["id"], "candidate-1")
        overview = self.service.overview()
        self.assertEqual(overview["embeddings"]["total"], 1)
        self.assertEqual(overview["storage"]["blobs"]["bytes"], 42)
        self.assertEqual(overview["map"]["by_type"]["topic"], 1)
        self.assertEqual(overview["hooks"]["status"], "partial")
        self.assertIn("beforeSubmitPrompt", overview["hooks"]["missing"])
        self.assertEqual(overview["persona"]["sections"], 1)
        self.assertEqual(overview["context"]["packs"], 0)

        knowledge = self.service.knowledge(CollectionQuery(status="faded", page=Page(1, 10)))
        self.assertEqual(knowledge["pagination"]["total"], 1)
        self.assertEqual(knowledge["data"][0]["id"], "memory-2")
        self.assertEqual(knowledge["data"][0]["body_preview"], "Faded body")
        self.assertNotIn("body", knowledge["data"][0])
        detail = self.service.knowledge_item("memory-1")
        self.assertEqual(detail["title"], "Active memory")
        self.assertEqual(detail["body"], "Active body")

        tasks = self.service.memory_tasks(CollectionQuery(status="pending", page=Page(1, 10)))
        self.assertEqual(tasks["data"][0]["id"], "task-1")
        self.assertNotIn("payload_json", tasks["data"][0])

        artifacts = self.service.artifacts(CollectionQuery(page=Page(1, 10)))
        by_id = {item["id"]: item for item in artifacts["data"]}
        self.assertEqual(by_id["art-blob"]["content_availability"], "blob")
        self.assertEqual(by_id["art-repo"]["content_availability"], "external")
        scoped = self.service.artifacts(CollectionQuery(scope="repository", page=Page(1, 10)))
        self.assertEqual(scoped["pagination"]["total"], 1)
        blob = self.service.artifact("art-blob")
        self.assertEqual(blob["origin"], "lucid_memories")
        self.assertEqual(blob["preview"], "hello preview")
        with self.assertRaises(LookupError):
            self.service.artifact("missing")
        with self.assertRaises(LookupError):
            self.service.knowledge_item("missing")

    def test_recall_graph_reads_shared_retrieval_data(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE retrieval_requests (
              id TEXT PRIMARY KEY, conversation_id TEXT, generation_id TEXT,
              method TEXT, query TEXT, target TEXT, source TEXT, status TEXT,
              started_at TEXT, completed_at TEXT, workspace_root TEXT
            );
            CREATE TABLE retrieval_logs (
              id TEXT PRIMARY KEY, request_id TEXT, prompt TEXT,
              prompt_at TEXT, links_json TEXT, hits_json TEXT
            );
            CREATE TABLE retrieval_results (
              id TEXT PRIMARY KEY, request_id TEXT, source_db TEXT,
              entity_type TEXT, entity_id TEXT, title TEXT, rank INTEGER,
              score REAL, memory_score REAL, selected INTEGER, ordinal INTEGER
            );
            INSERT INTO retrieval_requests VALUES
              ('request-1', 'session-1', NULL, 'recall', 'find alpha',
               NULL, 'hook', 'done', '2026-01-03T00:00:00Z',
               '2026-01-03T00:00:01Z', NULL);
            INSERT INTO retrieval_logs VALUES
              ('log-1', 'request-1', 'Find alpha', '2026-01-03T00:00:00Z',
               '[]', '[]');
            INSERT INTO retrieval_results VALUES
              ('result-1', 'request-1', 'map', 'topic', 'topic-1',
               'Alpha topic', 1, 0.9, NULL, 1, 1);
            """
        )
        connection.commit()
        connection.close()

        graph = self.service.recall_graph()

        self.assertEqual(graph["meta"]["log_count"], 1)
        self.assertIn("conversation:session-1", {node["id"] for node in graph["nodes"]})
        self.assertIn("retrieval:request-1", {node["id"] for node in graph["nodes"]})
        self.assertTrue(any(edge["relation"] == "recalled" for edge in graph["edges"]))


class ApiTests(unittest.TestCase):
    def test_remote_bind_requires_token(self) -> None:
        settings = Settings("0.0.0.0", 8765, Path("missing.sqlite"), Path("dist"), None)
        with self.assertRaises(RuntimeError):
            settings.validate()

    def test_invalid_query_is_a_client_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bus.sqlite"
            sqlite3.connect(path).close()
            settings = Settings(
                "127.0.0.1",
                0,
                path,
                Path(directory) / "dist",
                None,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = HTTPConnection(*server.server_address)
                client.request("GET", "/api/v1/analytics/daily?days=abc")
                response = client.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 400)
                self.assertEqual(payload["type"], "urn:problem:invalid-query")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_missing_database_is_service_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                "127.0.0.1",
                0,
                Path(directory) / "missing.sqlite",
                Path(directory) / "dist",
                None,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = HTTPConnection(*server.server_address)
                client.request("GET", "/api/v1/overview")
                response = client.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 503)
                self.assertEqual(payload["type"], "urn:problem:database-unavailable")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_unknown_artifact_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bus.sqlite"
            sqlite3.connect(path).close()
            settings = Settings("127.0.0.1", 0, path, Path(directory) / "dist", None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = HTTPConnection(*server.server_address)
                client.request("GET", "/api/v1/artifacts/missing")
                response = client.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 404)
                self.assertEqual(payload["title"], "Not Found")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_missing_frontend_build_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bus.sqlite"
            sqlite3.connect(path).close()
            settings = Settings("127.0.0.1", 0, path, Path(directory) / "dist", None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = HTTPConnection(*server.server_address)
                client.request("GET", "/guide")
                response = client.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 404)
                self.assertIn("フロントエンドのビルドが見つかりません", body)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_guide_spa_routing_served_when_dist_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bus.sqlite"
            sqlite3.connect(path).close()
            dist_dir = Path(directory) / "dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "index.html").write_text("<!doctype html><html><body><div id='root'>SPA</div></body></html>", encoding="utf-8")
            settings = Settings("127.0.0.1", 0, path, dist_dir, None)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(settings))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = HTTPConnection(*server.server_address)
                client.request("GET", "/guide")
                response = client.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("text/html", response.headers.get("Content-Type", ""))
                self.assertIn("<div id='root'>SPA</div>", body)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_default_frontend_dist_resolves_web_directory(self) -> None:
        from lucid_memories.web.backend.config import default_frontend_dist
        dist_path = default_frontend_dist()
        self.assertEqual(dist_path.name, "dist")


