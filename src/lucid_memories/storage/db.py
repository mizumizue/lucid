from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    SCHEMA_VERSION,
    bus_home,
    database_dir,
    db_path,
    legacy_db_path,
    schema_path,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_runtime_log(
    conn: sqlite3.Connection,
    log_name: str,
    content: str | bytes,
    *,
    log_id: str | None = None,
    created_at: str | None = None,
    content_type: str = "text/plain; charset=utf-8",
    metadata: dict | None = None,
) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    digest = hashlib.sha256(payload).hexdigest()
    log_id = log_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT OR IGNORE INTO runtime_logs(
          id, log_name, content, bytes, sha256, content_type, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_id,
            log_name,
            payload,
            len(payload),
            digest,
            content_type,
            created_at or now_iso(),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return log_id


def connect(path: Path | None = None) -> sqlite3.Connection:
    home = bus_home()
    home.mkdir(parents=True, exist_ok=True)
    database_dir().mkdir(parents=True, exist_ok=True)
    db = path or db_path()
    if path is None and not db.exists() and legacy_db_path().exists():
        db = legacy_db_path()
    conn = sqlite3.connect(str(db), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """Open an existing database without running migrations or writes."""
    db = path or db_path()
    if path is None and not db.exists() and legacy_db_path().exists():
        db = legacy_db_path()
    if not db.exists():
        raise FileNotFoundError(db)
    conn = sqlite3.connect(
        f"file:{db.resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        sql = schema_path().read_text(encoding="utf-8")
        # executescript() issues COMMIT first; do not wrap it in BEGIN.
        conn.executescript(sql)
        if current < 9:
            _ensure_memory_schema(conn)
        if current < 10:
            _ensure_artifact_storage_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now_iso()),
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    _ensure_fts(conn)
    _ensure_ontology_fts(conn)
    _ensure_turn_state(conn)
    _ensure_retrieval_logs(conn)
    _ensure_retrieval_tracking(conn)
    _ensure_blob_storage_schema(conn)
    _ensure_runtime_logs_schema(conn)
    _ensure_memory_schema(conn)
    _ensure_artifact_storage_schema(conn)
    _ensure_persona_schema(conn)


def _ensure_memory_schema(conn: sqlite3.Connection) -> None:
    """Backfill lifecycle and consolidation tables for pre-v9 databases."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(knowledge)").fetchall()
    }
    additions = (
        ("importance", "REAL NOT NULL DEFAULT 0.5"),
        ("salience", "REAL NOT NULL DEFAULT 0.5"),
        ("decay_half_life_days", "REAL NOT NULL DEFAULT 90.0"),
        ("access_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_accessed_at", "TEXT"),
        ("source_event_id", "TEXT"),
        ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("memory_status", "TEXT NOT NULL DEFAULT 'active'"),
    )
    for name, definition in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE knowledge ADD COLUMN {name} {definition}")
    candidate_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(memory_candidates)").fetchall()
    }
    if candidate_columns and "knowledge_id" not in candidate_columns:
        conn.execute("ALTER TABLE memory_candidates ADD COLUMN knowledge_id TEXT")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_memory_status
          ON knowledge(memory_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_knowledge_decay
          ON knowledge(last_accessed_at, created_at);
        CREATE TABLE IF NOT EXISTS memory_tasks (
          id TEXT PRIMARY KEY,
          task_type TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,
          available_at TEXT NOT NULL,
          locked_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_tasks_ready
          ON memory_tasks(status, available_at, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_tasks_entity
          ON memory_tasks(task_type, entity_type, entity_id);
        CREATE TABLE IF NOT EXISTS memory_candidates (
          id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL UNIQUE,
          conversation_id TEXT NOT NULL,
          workspace_root TEXT,
          kind TEXT NOT NULL DEFAULT 'finding',
          title TEXT NOT NULL,
          summary TEXT NOT NULL,
          tags_json TEXT NOT NULL DEFAULT '[]',
          confidence REAL,
          salience REAL NOT NULL DEFAULT 0.5,
          status TEXT NOT NULL DEFAULT 'pending',
          source_event_id TEXT NOT NULL,
          knowledge_id TEXT,
          expires_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_status
          ON memory_candidates(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_candidates_workspace
          ON memory_candidates(workspace_root, updated_at DESC);
        """
    )


def _ensure_artifact_storage_schema(conn: sqlite3.Connection) -> None:
    """Add content-addressed storage metadata to pre-v10 artifact rows."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()
    }
    additions = (
        ("blob_sha", "TEXT"),
        ("storage_scope", "TEXT NOT NULL DEFAULT 'workspace'"),
        ("storage_policy", "TEXT NOT NULL DEFAULT 'managed_elsewhere'"),
    )
    for name, definition in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE artifacts ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_storage_scope "
        "ON artifacts(storage_scope, updated_at DESC)"
    )


def _ensure_blob_storage_schema(conn: sqlite3.Connection) -> None:
    """Add embedded payload storage to pre-v14 blob metadata."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(blobs)").fetchall()
    }
    if "data" not in columns:
        conn.execute("ALTER TABLE blobs ADD COLUMN data BLOB")


def _ensure_runtime_logs_schema(conn: sqlite3.Connection) -> None:
    """Create the database-backed runtime log table."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_logs (
          id TEXT PRIMARY KEY,
          log_name TEXT NOT NULL,
          content BLOB NOT NULL,
          bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          content_type TEXT NOT NULL DEFAULT 'text/plain; charset=utf-8',
          created_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_logs_name
          ON runtime_logs(log_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runtime_logs_created
          ON runtime_logs(created_at DESC);
        """
    )


def _ensure_persona_schema(conn: sqlite3.Connection) -> None:
    """Create persona learning tables for existing v10 databases."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS persona_candidates (
          id TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          evidence_count INTEGER NOT NULL DEFAULT 0,
          conversation_ids_json TEXT NOT NULL DEFAULT '[]',
          source_event_ids_json TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'pending',
          applied_revision_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_persona_candidates_status
          ON persona_candidates(status, updated_at DESC);
        CREATE TABLE IF NOT EXISTS persona_revisions (
          id TEXT PRIMARY KEY,
          candidate_id TEXT,
          action TEXT NOT NULL,
          before_hash TEXT,
          after_hash TEXT,
          before_json TEXT,
          after_json TEXT,
          created_at TEXT NOT NULL,
          created_by TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_persona_revisions_created
          ON persona_revisions(created_at DESC);
        """
    )


def _ensure_virtual_fts(conn: sqlite3.Connection, ddl_trigram: str, ddl_plain: str) -> None:
    try:
        conn.execute(ddl_trigram)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "already exists" in msg:
            return
        try:
            conn.execute(ddl_plain)
        except sqlite3.OperationalError as exc2:
            if "already exists" not in str(exc2).lower():
                raise


def _ensure_fts(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
    ).fetchone()
    if row:
        return
    _ensure_virtual_fts(
        conn,
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5("
        "title, body, tags, knowledge_id UNINDEXED, tokenize='trigram')",
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5("
        "title, body, tags, knowledge_id UNINDEXED)",
    )


def _ensure_ontology_fts(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ontology_nodes'"
    ).fetchone()
    if row is None:
        sql = schema_path().read_text(encoding="utf-8")
        conn.executescript(sql)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ontology_nodes_fts'"
    ).fetchone()
    if row:
        return
    _ensure_virtual_fts(
        conn,
        "CREATE VIRTUAL TABLE ontology_nodes_fts USING fts5("
        "title, normalized, node_id UNINDEXED, tokenize='trigram')",
        "CREATE VIRTUAL TABLE ontology_nodes_fts USING fts5("
        "title, normalized, node_id UNINDEXED)",
    )


def _ensure_turn_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turn_state (
          conversation_id TEXT PRIMARY KEY,
          last_prompt TEXT,
          last_prompt_at TEXT,
          workspace_root TEXT
        )
        """
    )


def _ensure_retrieval_logs(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS retrieval_logs (
          id TEXT PRIMARY KEY,
          conversation_id TEXT,
          request_id TEXT,
          prompt TEXT NOT NULL,
          prompt_at TEXT NOT NULL,
          query TEXT,
          source TEXT NOT NULL,
          workspace_root TEXT,
          dbs_json TEXT NOT NULL DEFAULT '[]',
          links_json TEXT NOT NULL DEFAULT '[]',
          hits_json TEXT NOT NULL DEFAULT '[]',
          sqlite_hit_count INTEGER NOT NULL DEFAULT 0,
          map_hit_count INTEGER NOT NULL DEFAULT 0,
          link_count INTEGER NOT NULL DEFAULT 0,
          expected_json TEXT,
          matched_count INTEGER,
          expected_count INTEGER,
          gaps_json TEXT,
          improved_json TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_at ON retrieval_logs(prompt_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_source ON retrieval_logs(source, created_at DESC)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(retrieval_logs)")}
    if "request_id" not in cols:
        conn.execute("ALTER TABLE retrieval_logs ADD COLUMN request_id TEXT")
    if "gaps_json" not in cols:
        conn.execute("ALTER TABLE retrieval_logs ADD COLUMN gaps_json TEXT")
    if "improved_json" not in cols:
        conn.execute("ALTER TABLE retrieval_logs ADD COLUMN improved_json TEXT")


def _ensure_retrieval_tracking(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS retrieval_requests (
          id TEXT PRIMARY KEY,
          conversation_id TEXT,
          generation_id TEXT,
          parent_request_id TEXT,
          method TEXT NOT NULL,
          query TEXT,
          target TEXT,
          workspace_root TEXT,
          source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          started_at TEXT NOT NULL,
          completed_at TEXT,
          result_count INTEGER NOT NULL DEFAULT 0,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_requests_conversation
          ON retrieval_requests(conversation_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_retrieval_requests_generation
          ON retrieval_requests(generation_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_retrieval_requests_method
          ON retrieval_requests(method, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_retrieval_requests_workspace
          ON retrieval_requests(workspace_root, started_at DESC);
        CREATE TABLE IF NOT EXISTS retrieval_results (
          id TEXT PRIMARY KEY,
          request_id TEXT NOT NULL REFERENCES retrieval_requests(id) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL,
          source_db TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          entity_id TEXT,
          title TEXT,
          rank INTEGER,
          score REAL,
          memory_score REAL,
          selected INTEGER NOT NULL DEFAULT 1,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          UNIQUE(request_id, ordinal)
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_results_request
          ON retrieval_results(request_id, ordinal);
        CREATE INDEX IF NOT EXISTS idx_retrieval_results_entity
          ON retrieval_results(source_db, entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_retrieval_logs_request
          ON retrieval_logs(request_id);
        """
    )
    legacy_rows = conn.execute(
        """
        SELECT * FROM retrieval_logs
        WHERE request_id IS NULL
        ORDER BY created_at
        """
    ).fetchall()
    for row in legacy_rows:
        request_id = str(row["id"])
        started_at = row["prompt_at"] or row["created_at"]
        try:
            hits = json.loads(row["hits_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            hits = []
        if not isinstance(hits, list):
            hits = []
        conn.execute(
            """
            INSERT OR IGNORE INTO retrieval_requests(
              id, conversation_id, method, query, workspace_root, source,
              status, started_at, completed_at, result_count, metadata_json
            ) VALUES (?, ?, 'legacy_log', ?, ?, ?, 'completed', ?, ?, ?, ?)
            """,
            (
                request_id,
                row["conversation_id"],
                row["query"],
                row["workspace_root"],
                row["source"],
                started_at,
                row["created_at"],
                len(hits),
                json.dumps({"legacy_log_id": request_id}, ensure_ascii=False),
            ),
        )
        for ordinal, hit in enumerate(hits):
            if not isinstance(hit, dict):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO retrieval_results(
                  id, request_id, ordinal, source_db, entity_type, entity_id,
                  title, rank, score, memory_score, selected, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    f"legacy-result:{request_id}:{ordinal}",
                    request_id,
                    ordinal,
                    str(hit.get("db") or "unknown"),
                    str(hit.get("kind") or "unknown"),
                    hit.get("id"),
                    hit.get("title"),
                    ordinal + 1,
                    hit.get("score"),
                    hit.get("memory_score"),
                    json.dumps(hit, ensure_ascii=False),
                    row["created_at"],
                ),
            )
        conn.execute(
            "UPDATE retrieval_logs SET request_id = ? WHERE id = ?",
            (request_id, row["id"]),
        )


@contextmanager
def write_tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def checkpoint_passive(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError:
        pass
