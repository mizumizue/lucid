#!/usr/bin/env python3
"""Migrate lucid-memories storage into .db and embedded SQLite tables."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lucid_memories import blobs  # noqa: E402
from lucid_memories.db import connect, now_iso, record_runtime_log  # noqa: E402
from lucid_memories.paths import (  # noqa: E402
    blobs_dir,
    bus_home,
    db_path,
    legacy_db_path,
    legacy_map_path,
    logs_dir,
    map_path,
)


def _move_storage(source: Path, destination: Path) -> str:
    if destination.exists() and source.exists():
        raise RuntimeError(f"移行先と移行元が両方存在します: {source} / {destination}")
    if destination.exists():
        return "already_present"
    if not source.exists():
        return "missing"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return "moved"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_files() -> list[Path]:
    directory = blobs_dir()
    if not directory.exists():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file())


def _log_files() -> list[Path]:
    directory = logs_dir()
    if not directory.exists():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file())


def migrate() -> dict[str, object]:
    home = bus_home()
    home.mkdir(parents=True, exist_ok=True)
    db_move = _move_storage(legacy_db_path(), db_path())
    map_move = _move_storage(legacy_map_path(), map_path())

    conn = connect(path=db_path())
    try:
        blob_checks: list[tuple[str, int]] = []
        for path in _blob_files():
            payload = path.read_bytes()
            expected = path.name
            actual = _hash(payload)
            if actual != expected or path.parent.name != expected[:2]:
                raise RuntimeError(f"blobのハッシュが不正です: {path}")
            blobs.put_blob(conn, payload)
            row = conn.execute(
                "SELECT bytes, data FROM blobs WHERE sha256 = ?",
                (expected,),
            ).fetchone()
            if row is None or row["data"] is None:
                raise RuntimeError(f"blobのDB保存に失敗しました: {path}")
            stored = bytes(row["data"])
            if _hash(stored) != expected or len(stored) != row["bytes"]:
                raise RuntimeError(f"blobのDB検証に失敗しました: {path}")
            blob_checks.append((expected, len(payload)))

        log_checks: list[tuple[str, int, str]] = []
        for path in _log_files():
            payload = path.read_bytes()
            digest = _hash(payload)
            log_id = f"migration:{path.name}:{digest}"
            record_runtime_log(
                conn,
                path.name,
                payload,
                log_id=log_id,
                created_at=now_iso(),
                metadata={"migrated_from": str(path.relative_to(home))},
            )
            row = conn.execute(
                "SELECT bytes, sha256, content FROM runtime_logs WHERE id = ?",
                (log_id,),
            ).fetchone()
            if (
                row is None
                or row["bytes"] != len(payload)
                or row["sha256"] != digest
                or bytes(row["content"]) != payload
            ):
                raise RuntimeError(f"logのDB保存に失敗しました: {path}")
            log_checks.append((path.name, len(payload), digest))
    finally:
        conn.close()

    if blobs_dir().exists():
        shutil.rmtree(blobs_dir())
    if logs_dir().exists():
        shutil.rmtree(logs_dir())

    return {
        "ok": True,
        "home": str(home),
        "database": str(db_path()),
        "graph_database": str(map_path()),
        "database_move": db_move,
        "graph_move": map_move,
        "blobs_migrated": len(blob_checks),
        "logs_migrated": len(log_checks),
        "removed": {
            "blobs": not blobs_dir().exists(),
            "logs": not logs_dir().exists(),
        },
    }


if __name__ == "__main__":
    try:
        print(json.dumps(migrate(), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
