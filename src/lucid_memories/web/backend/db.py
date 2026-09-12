from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class DatabaseUnavailableError(RuntimeError):
    """Raised when the configured lucid-memories database cannot be opened."""


@dataclass(frozen=True)
class DatabaseInfo:
    path: Path
    tables: frozenset[str]
    schema_version: int | None
    modified_at: str | None

    def has(self, table: str) -> bool:
        return table in self.tables


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise DatabaseUnavailableError(f"lucid-memories database が見つかりません: {path.name}")
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
            timeout=5,
        )
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError("lucid-memories database を読み取れません。") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def inspect(connection: sqlite3.Connection, path: Path) -> DatabaseInfo:
    tables = frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    )
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    try:
        modified_at = datetime.fromtimestamp(
            path.stat().st_mtime,
            timezone.utc,
        ).replace(microsecond=0).isoformat()
    except OSError:
        modified_at = None
    return DatabaseInfo(path, tables, schema_version, modified_at)


def table_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    if not table_exists(connection, name):
        return set()
    return {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({quote_identifier(name)})")
    }


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError("Invalid SQL identifier")
    return f'"{identifier}"'


def rows(connection: sqlite3.Connection, query: str, parameters: Iterable[object] = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, tuple(parameters)).fetchall()]


def one(
    connection: sqlite3.Connection,
    query: str,
    parameters: Iterable[object] = (),
) -> dict:
    row = connection.execute(query, tuple(parameters)).fetchone()
    return dict(row) if row else {}

