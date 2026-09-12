"""Lifecycle and background consolidation primitives for lucid-memories memory."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from lucid_memories.storage.db import now_iso, write_tx
from lucid_memories.runtime.util import dumps, new_id, normalize_root, truncate


DEFAULT_IMPORTANCE = 0.5
DEFAULT_SALIENCE = 0.5
DEFAULT_HALF_LIFE_DAYS = 90.0
MIN_VISIBLE_SCORE = 0.025


def _clamp(value: float | None, default: float) -> float:
    if value is None:
        return default
    return max(0.0, min(1.0, float(value)))


def lifecycle_defaults(
    kind: str,
    *,
    confidence: float | None = None,
    importance: float | None = None,
    salience: float | None = None,
    decay_half_life_days: float | None = None,
) -> dict[str, float]:
    """Return stable defaults while allowing callers to override each value."""
    kind_defaults = {
        "decision": (0.85, 0.75, 365.0),
        "warning": (0.8, 0.8, 180.0),
        "fact": (0.65, 0.55, 180.0),
        "finding": (0.55, 0.7, 60.0),
        "idea": (0.5, 0.65, 120.0),
        "handoff": (0.7, 0.7, 30.0),
        "pointer": (0.45, 0.45, 45.0),
    }
    default_importance, default_salience, default_half_life = kind_defaults.get(
        kind, (DEFAULT_IMPORTANCE, DEFAULT_SALIENCE, DEFAULT_HALF_LIFE_DAYS)
    )
    return {
        "confidence": _clamp(confidence, 0.5),
        "importance": _clamp(importance, default_importance),
        "salience": _clamp(salience, default_salience),
        "decay_half_life_days": (
            max(0.0, float(decay_half_life_days))
            if decay_half_life_days is not None
            else default_half_life
        ),
    }


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decay_factor(row: sqlite3.Row | dict[str, Any], *, now: str | None = None) -> float:
    """Calculate exponential forgetting since the last reinforcement."""
    anchor_value = row["last_accessed_at"] if not isinstance(row, dict) else row.get("last_accessed_at")
    if not anchor_value:
        anchor_value = row["created_at"] if not isinstance(row, dict) else row.get("created_at")
    anchor = _parse_time(anchor_value)
    current = _parse_time(now) if now else datetime.now(timezone.utc)
    if anchor is None or current is None:
        return 1.0
    half_life = (
        row["decay_half_life_days"]
        if not isinstance(row, dict)
        else row.get("decay_half_life_days")
    )
    try:
        half_life_value = float(half_life or DEFAULT_HALF_LIFE_DAYS)
    except (TypeError, ValueError):
        half_life_value = DEFAULT_HALF_LIFE_DAYS
    if half_life_value <= 0:
        return 1.0
    age_days = max(0.0, (current - anchor).total_seconds() / 86400.0)
    return 2.0 ** (-age_days / half_life_value)


def score(row: sqlite3.Row | dict[str, Any], *, now: str | None = None) -> float:
    """Return the retrieval contribution of a memory before query relevance."""
    def value(name: str, default: float) -> float:
        raw = row[name] if not isinstance(row, dict) else row.get(name)
        try:
            return _clamp(float(raw), default)
        except (TypeError, ValueError):
            return default

    confidence = value("confidence", 0.5)
    importance = value("importance", DEFAULT_IMPORTANCE)
    salience = value("salience", DEFAULT_SALIENCE)
    return round(confidence * importance * (0.5 + 0.5 * salience) * decay_factor(row, now=now), 6)


def touch(conn: sqlite3.Connection, knowledge_ids: list[str], *, at: str | None = None) -> None:
    ids = [str(item) for item in knowledge_ids if item]
    if not ids:
        return
    timestamp = at or now_iso()
    placeholders = ",".join("?" for _ in ids)
    with write_tx(conn):
        conn.execute(
            f"""
            UPDATE knowledge
            SET access_count = access_count + 1,
                last_accessed_at = ?
            WHERE id IN ({placeholders})
            """,
            (timestamp, *ids),
        )


def enqueue_event(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    payload: dict[str, Any] | None = None,
    at: str | None = None,
) -> None:
    if not event_id:
        return
    timestamp = at or now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_tasks(
          id, task_type, entity_type, entity_id, payload_json,
          status, attempts, available_at, created_at, updated_at
        ) VALUES (?, 'consolidate_event', 'conversation_event', ?, ?, 'pending', 0, ?, ?, ?)
        """,
        (new_id(), event_id, dumps(payload or {}), timestamp, timestamp, timestamp),
    )


def _candidate_from_event(row: sqlite3.Row) -> dict[str, Any] | None:
    input_text = (row["input_text"] or "").strip()
    output_text = (row["output_text"] or "").strip()
    source = "\n".join(part for part in (input_text, output_text) if part).strip()
    if len(source) < 12:
        return None
    source = truncate(source, 4000)
    first_line = next((line.strip() for line in source.splitlines() if line.strip()), source)
    title_prefix = {
        "user": "要求",
        "assistant": "応答",
        "tool": "ツール結果",
    }.get(row["role"] or "", "会話")
    title = truncate(f"{title_prefix}: {first_line}", 160)
    lower = source.lower()
    trigger_words = ("決定", "方針", "重要", "todo", "実装", "覚え", "忘れ", "decision", "must")
    salience = 0.75 if any(word in lower for word in trigger_words) else 0.35
    tags = ["auto-candidate", row["event_type"]]
    if row["role"]:
        tags.append(str(row["role"]))
    return {
        "event_id": row["id"],
        "conversation_id": row["conversation_id"],
        "workspace_root": row["workspace_root"],
        "title": title,
        "summary": (
            f"source_event_id={row['id']}\n"
            f"event_type={row['event_type']}\n\n{source}"
        ),
        "tags": tags,
        "confidence": 0.35,
        "salience": salience,
    }


def process_tasks(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    at: str | None = None,
) -> dict[str, Any]:
    """Run a bounded, retryable worker pass and create pending candidates."""
    timestamp = at or now_iso()
    rows = conn.execute(
        """
        SELECT * FROM memory_tasks
        WHERE status IN ('pending', 'error') AND available_at <= ?
        ORDER BY created_at
        LIMIT ?
        """,
        (timestamp, max(1, min(limit, 200))),
    ).fetchall()
    processed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for task in rows:
        try:
            with write_tx(conn):
                conn.execute(
                    """
                    UPDATE memory_tasks
                    SET status = 'running', attempts = attempts + 1,
                        locked_at = ?, updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'error')
                    """,
                    (timestamp, timestamp, task["id"]),
                )
                event = conn.execute(
                    "SELECT * FROM conversation_events WHERE id = ?",
                    (task["entity_id"],),
                ).fetchone()
                candidate = _candidate_from_event(event) if event else None
                if candidate:
                    conn.execute(
                        """
                        INSERT INTO memory_candidates(
                          id, event_id, conversation_id, workspace_root, kind,
                          title, summary, tags_json, confidence, salience,
                          status, source_event_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'finding', ?, ?, ?, ?, ?,
                                  'pending', ?, ?, ?)
                        ON CONFLICT(event_id) DO NOTHING
                        """,
                        (
                            new_id(),
                            candidate["event_id"],
                            candidate["conversation_id"],
                            normalize_root(candidate["workspace_root"])
                            if candidate["workspace_root"]
                            else None,
                            candidate["title"],
                            candidate["summary"],
                            dumps(candidate["tags"]),
                            candidate["confidence"],
                            candidate["salience"],
                            candidate["event_id"],
                            timestamp,
                            timestamp,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE memory_tasks
                    SET status = 'done', locked_at = NULL, last_error = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, task["id"]),
                )
            processed.append({"task_id": task["id"], "event_id": task["entity_id"], "candidate": bool(candidate)})
        except Exception as exc:
            with write_tx(conn):
                conn.execute(
                    """
                    UPDATE memory_tasks
                    SET status = 'error', locked_at = NULL, last_error = ?,
                        available_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(exc)[:500], timestamp, timestamp, task["id"]),
                )
            failed.append({"task_id": task["id"], "error": str(exc)})
    return {"ok": True, "processed": processed, "failed": failed, "count": len(rows)}


def list_candidates(
    conn: sqlite3.Connection,
    *,
    workspace: str | None = None,
    status: str = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM memory_candidates
        WHERE (? IS NULL OR status = ?)
          AND (? IS NULL OR workspace_root IS NULL OR workspace_root = ?)
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (status, status, normalize_root(workspace) if workspace else None,
         normalize_root(workspace) if workspace else None, max(1, min(limit, 200))),
    ).fetchall()
    return [dict(row) for row in rows]


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM knowledge WHERE memory_status = 'active') AS active_memories,
          (SELECT COUNT(*) FROM knowledge WHERE memory_status = 'faded') AS faded_memories,
          (SELECT COUNT(*) FROM memory_tasks WHERE status = 'pending') AS pending_tasks,
          (SELECT COUNT(*) FROM memory_tasks WHERE status = 'error') AS failed_tasks,
          (SELECT COUNT(*) FROM memory_candidates WHERE status = 'pending') AS pending_candidates
        """
    ).fetchone()
    return dict(counts)


def sweep(conn: sqlite3.Connection, *, threshold: float = MIN_VISIBLE_SCORE) -> dict[str, Any]:
    """Mark decayed memories as faded without deleting or archiving them."""
    rows = conn.execute(
        """
        SELECT * FROM knowledge
        WHERE memory_status = 'active'
          AND (expires_at IS NULL OR expires_at > ?)
        """,
        (now_iso(),),
    ).fetchall()
    faded: list[str] = []
    for row in rows:
        if score(row) < threshold:
            faded.append(row["id"])
    if faded:
        with write_tx(conn):
            placeholders = ",".join("?" for _ in faded)
            conn.execute(
                f"UPDATE knowledge SET memory_status = 'faded' WHERE id IN ({placeholders})",
                faded,
            )
    return {"ok": True, "faded": faded, "count": len(faded), "threshold": threshold}
