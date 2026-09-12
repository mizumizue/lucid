from __future__ import annotations

import hashlib
import sqlite3

from .db import connect, now_iso
from .paths import blobs_dir


def put_blob(
    conn: sqlite3.Connection,
    data: bytes,
    content_type: str = "text/plain; charset=utf-8",
) -> str:
    sha = hashlib.sha256(data).hexdigest()
    conn.execute(
        """
        INSERT INTO blobs(sha256, bytes, content_type, created_at, data)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(sha256) DO UPDATE SET
          bytes = excluded.bytes,
          content_type = COALESCE(blobs.content_type, excluded.content_type),
          data = COALESCE(blobs.data, excluded.data)
        """,
        (sha, len(data), content_type, now_iso(), data),
    )
    return sha


def get_blob(sha: str, conn: sqlite3.Connection | None = None) -> bytes | None:
    owner = conn is None
    database = conn or connect()
    try:
        row = database.execute(
            "SELECT data FROM blobs WHERE sha256 = ?",
            (sha,),
        ).fetchone()
        if row is not None and row["data"] is not None:
            return bytes(row["data"])
    finally:
        if owner:
            database.close()

    # Compatibility fallback while a legacy storage migration is in progress.
    path = blobs_dir() / sha[:2] / sha
    if path.exists():
        return path.read_bytes()
    return None


def get_blob_text(
    sha: str,
    conn: sqlite3.Connection | None = None,
) -> str | None:
    raw = get_blob(sha, conn=conn)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")
